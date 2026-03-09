#!/usr/bin/env python3
"""
Netrefer CSV → MySQL ETL
========================
Drop one or more Netrefer CSV exports into the drop folder (or pass a file
path directly) and this script upserts the data into MySQL.

Usage:
    # Load a single file
    python etl/netrefer_etl.py --file /path/to/report.csv

    # Load all CSVs from the drop folder (set CSV_DROP_DIR in .env)
    python etl/netrefer_etl.py --dir
"""

import argparse
import csv
import io
import logging
import os
import shutil
import sys
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional

import mysql.connector
import yaml
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("netrefer_etl")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def load_column_map() -> Dict[str, str]:
    map_path = Path(__file__).parent / "column_map.yaml"
    with open(map_path) as f:
        cfg = yaml.safe_load(f)
    return cfg["column_map"]


def db_connection():
    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
        autocommit=False,
    )


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d")


def _parse_date(raw: str):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_csv(raw_csv: str, col_map: Dict[str, str]) -> List[Dict]:
    rows = []
    reader = csv.DictReader(io.StringIO(raw_csv))
    for i, row in enumerate(reader, start=1):
        mapped = {}
        for csv_col, value in row.items():
            db_col = col_map.get(csv_col.strip())
            if db_col:
                mapped[db_col] = (value or "").strip()

        if not mapped.get("report_date"):
            log.warning("Row %d skipped – no report_date", i)
            continue

        parsed_date = _parse_date(mapped["report_date"])
        if not parsed_date:
            log.warning("Row %d skipped – unparseable date: %s", i, mapped["report_date"])
            continue
        mapped["report_date"] = parsed_date
        rows.append(mapped)

    log.info("Parsed %d rows", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------
def _to_cents(value: str) -> int:
    if not value:
        return 0
    try:
        cleaned = value.replace(",", "").replace("$", "").replace("€", "").strip()
        return int(Decimal(cleaned) * 100)
    except InvalidOperation:
        return 0


def _to_int(value: str) -> int:
    if not value:
        return 0
    try:
        return int(float(value.replace(",", "")))
    except (ValueError, TypeError):
        return 0


CURRENCY_COLS = {"deposits", "net_revenue", "gross_revenue", "chargebacks", "commission"}
INT_COLS = {"impressions", "clicks", "registrations", "first_depositors", "total_depositors"}


def transform(rows: List[Dict]) -> List[Dict]:
    result = []
    for row in rows:
        rec = {
            "report_date":    row.get("report_date"),
            "affiliate_id":   row.get("affiliate_id", "UNKNOWN"),
            "affiliate_name": row.get("affiliate_name", ""),
            "campaign_id":    row.get("campaign_id", ""),
            "campaign_name":  row.get("campaign_name", ""),
            "brand":          row.get("brand", ""),
            "country":        row.get("country", ""),
            "media_type":     row.get("media_type", ""),
        }
        for col in INT_COLS:
            rec[col] = _to_int(row.get(col, "0"))
        for col in CURRENCY_COLS:
            rec[f"{col}_cents"] = _to_cents(row.get(col, "0"))
        result.append(rec)
    return result


# ---------------------------------------------------------------------------
# MySQL loader
# ---------------------------------------------------------------------------
UPSERT_SQL = """
INSERT INTO netrefer_stats (
    report_date, affiliate_id, affiliate_name,
    campaign_id, campaign_name, brand, country, media_type,
    impressions, clicks,
    registrations, first_depositors, total_depositors,
    deposits_cents, net_revenue_cents, gross_revenue_cents,
    chargebacks_cents, commission_cents
) VALUES (
    %(report_date)s, %(affiliate_id)s, %(affiliate_name)s,
    %(campaign_id)s, %(campaign_name)s, %(brand)s, %(country)s, %(media_type)s,
    %(impressions)s, %(clicks)s,
    %(registrations)s, %(first_depositors)s, %(total_depositors)s,
    %(deposits_cents)s, %(net_revenue_cents)s, %(gross_revenue_cents)s,
    %(chargebacks_cents)s, %(commission_cents)s
)
ON DUPLICATE KEY UPDATE
    affiliate_name      = VALUES(affiliate_name),
    campaign_name       = VALUES(campaign_name),
    media_type          = VALUES(media_type),
    impressions         = VALUES(impressions),
    clicks              = VALUES(clicks),
    registrations       = VALUES(registrations),
    first_depositors    = VALUES(first_depositors),
    total_depositors    = VALUES(total_depositors),
    deposits_cents      = VALUES(deposits_cents),
    net_revenue_cents   = VALUES(net_revenue_cents),
    gross_revenue_cents = VALUES(gross_revenue_cents),
    chargebacks_cents   = VALUES(chargebacks_cents),
    commission_cents    = VALUES(commission_cents),
    updated_at          = CURRENT_TIMESTAMP
"""

AUDIT_START = """
INSERT INTO etl_runs (run_id, mode, source_detail, status)
VALUES (%s, 'csv', %s, 'running')
"""

AUDIT_FINISH = """
UPDATE etl_runs
SET finished_at = CURRENT_TIMESTAMP,
    rows_upserted = %s,
    status = %s,
    error_message = %s
WHERE run_id = %s
"""


def load_to_mysql(records: List[Dict], source: str, batch_size: int = 500) -> int:
    if not records:
        log.warning("No records to load")
        return 0

    run_id = str(uuid.uuid4())
    conn = db_connection()
    cursor = conn.cursor()
    total = 0

    try:
        cursor.execute(AUDIT_START, (run_id, source))
        conn.commit()

        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            cursor.executemany(UPSERT_SQL, batch)
            conn.commit()
            total += cursor.rowcount
            log.info("Batch %d–%d: %d rows affected", i + 1, i + len(batch), cursor.rowcount)

        cursor.execute(AUDIT_FINISH, (total, "success", None, run_id))
        conn.commit()

    except Exception as exc:
        conn.rollback()
        cursor.execute(AUDIT_FINISH, (total, "failed", str(exc), run_id))
        conn.commit()
        raise
    finally:
        cursor.close()
        conn.close()

    return total


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------
def process_file(file_path: Path, col_map: Dict[str, str]) -> int:
    log.info("Loading: %s", file_path)
    raw = file_path.read_text(encoding="utf-8-sig")  # handles BOM
    rows = parse_csv(raw, col_map)
    records = transform(rows)
    count = load_to_mysql(records, source=file_path.name)
    log.info("Done: %d rows upserted from %s", count, file_path.name)
    return count


def process_drop_dir(drop_dir: Path, processed_dir: Path, col_map: Dict[str, str]):
    csv_files = sorted(drop_dir.glob("*.csv"))
    if not csv_files:
        log.info("No CSV files found in %s", drop_dir)
        return

    processed_dir.mkdir(parents=True, exist_ok=True)

    for f in csv_files:
        try:
            process_file(f, col_map)
            shutil.move(str(f), str(processed_dir / f.name))
            log.info("Moved to processed: %s", f.name)
        except Exception as e:
            log.error("Failed to process %s: %s", f.name, e, exc_info=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Netrefer CSV → MySQL ETL")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Path to a single CSV file")
    group.add_argument("--dir",  action="store_true",
                       help="Process all CSVs in CSV_DROP_DIR (set in .env)")
    args = p.parse_args()

    col_map = load_column_map()

    if args.file:
        process_file(Path(args.file), col_map)
    else:
        drop_dir = Path(os.getenv("CSV_DROP_DIR", "./drop"))
        processed_dir = Path(os.getenv("PROCESSED_DIR", str(drop_dir / "processed")))
        process_drop_dir(drop_dir, processed_dir, col_map)


if __name__ == "__main__":
    main()
