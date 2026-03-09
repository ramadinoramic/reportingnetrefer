#!/usr/bin/env python3
"""
Netrefer → MySQL ETL
====================
Supports two ingestion modes:
  1. API mode  – fetches reports directly from the Netrefer API
  2. CSV mode  – watches a drop folder for manually exported CSV files

Usage:
    python etl/netrefer_etl.py --mode api --start 2024-01-01 --end 2024-01-31
    python etl/netrefer_etl.py --mode csv --file /data/netrefer/report.csv
    python etl/netrefer_etl.py --mode csv --dir  /data/netrefer/incoming/
"""

import argparse
import csv
import io
import logging
import os
import shutil
import sys
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional

import mysql.connector
import requests
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
    """Load CSV-header → DB-column mapping from YAML."""
    map_path = Path(__file__).parent / "column_map.yaml"
    with open(map_path) as f:
        cfg = yaml.safe_load(f)
    return cfg["column_map"]


def db_connection():
    """Return a MySQL connection using environment variables."""
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
# Netrefer API client
# ---------------------------------------------------------------------------
class NetreferClient:
    """Thin HTTP wrapper for the Netrefer reporting API."""

    def __init__(self):
        self.api_key = os.environ["NETREFER_API_KEY"]
        self.base_url = os.getenv(
            "NETREFER_API_URL", "https://api.netrefer.com/v1/reports"
        )

    def fetch_csv(self, start: date, end: date) -> str:
        """
        Fetch the affiliate stats report as CSV text.
        Adjust query parameters to match your Netrefer API contract.
        """
        params = {
            "apikey": self.api_key,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "format": "csv",
            "report": "affiliate_stats",
        }
        log.info("Fetching Netrefer API %s → %s", start, end)
        resp = requests.get(self.base_url, params=params, timeout=120)
        resp.raise_for_status()
        log.info("Received %d bytes from Netrefer API", len(resp.content))
        return resp.text


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------
def parse_csv(raw_csv: str, col_map: Dict[str, str]) -> List[Dict]:
    """
    Parse raw CSV text into a list of dicts keyed by DB column names.
    Unmapped columns are silently ignored.
    """
    rows = []
    reader = csv.DictReader(io.StringIO(raw_csv))

    for i, row in enumerate(reader, start=1):
        mapped = {}
        for csv_col, value in row.items():
            db_col = col_map.get(csv_col.strip())
            if db_col:
                mapped[db_col] = value.strip() if value else ""

        if not mapped.get("report_date"):
            log.warning("Row %d skipped – no report_date", i)
            continue

        try:
            mapped["report_date"] = datetime.strptime(
                mapped["report_date"], "%Y-%m-%d"
            ).date()
        except ValueError:
            # Try common alternate formats
            for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
                try:
                    mapped["report_date"] = datetime.strptime(
                        mapped["report_date"], fmt
                    ).date()
                    break
                except ValueError:
                    continue
            else:
                log.warning("Row %d skipped – unparseable date: %s", i, mapped["report_date"])
                continue

        rows.append(mapped)

    log.info("Parsed %d rows from CSV", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Data transformation
# ---------------------------------------------------------------------------
def _to_cents(value: str) -> int:
    """Convert a currency string like '1,234.56' to integer cents."""
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
    """Cast types and rename currency columns to *_cents."""
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


def load_to_mysql(records: List[Dict], batch_size: int = 500) -> int:
    """Upsert records into netrefer_stats. Returns total rows affected."""
    if not records:
        log.warning("No records to load")
        return 0

    conn = db_connection()
    cursor = conn.cursor()
    total = 0

    try:
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            cursor.executemany(UPSERT_SQL, batch)
            conn.commit()
            total += cursor.rowcount
            log.info(
                "Loaded batch %d-%d (%d rows affected)",
                i + 1, i + len(batch), cursor.rowcount,
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    return total


# ---------------------------------------------------------------------------
# File-drop mode helpers
# ---------------------------------------------------------------------------
def process_csv_file(file_path: Path, col_map: Dict[str, str]) -> int:
    """Parse, transform, and load a single CSV file. Returns rows loaded."""
    log.info("Processing file: %s", file_path)
    raw = file_path.read_text(encoding="utf-8-sig")  # handles BOM
    rows = parse_csv(raw, col_map)
    records = transform(rows)
    count = load_to_mysql(records)
    log.info("Done: %d rows upserted from %s", count, file_path.name)
    return count


def process_csv_dir(drop_dir: Path, processed_dir: Optional[Path], col_map: Dict[str, str]):
    """Process all *.csv files in drop_dir, moving them to processed_dir when done."""
    csv_files = sorted(drop_dir.glob("*.csv"))
    if not csv_files:
        log.info("No CSV files found in %s", drop_dir)
        return

    if processed_dir:
        processed_dir.mkdir(parents=True, exist_ok=True)

    for f in csv_files:
        try:
            process_csv_file(f, col_map)
            if processed_dir:
                dest = processed_dir / f.name
                shutil.move(str(f), str(dest))
                log.info("Moved %s → %s", f.name, dest)
        except Exception as e:
            log.error("Failed to process %s: %s", f.name, e, exc_info=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Netrefer → MySQL ETL")
    sub = p.add_subparsers(dest="mode", required=True)

    # API mode
    api = sub.add_parser("api", help="Fetch data from Netrefer API")
    api.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    api.add_argument("--end", help="End date YYYY-MM-DD (default: yesterday)")

    # CSV mode (single file)
    csv_p = sub.add_parser("csv", help="Load a CSV file or directory")
    csv_p.add_argument("--file", help="Path to a single CSV file")
    csv_p.add_argument("--dir",  help="Directory containing CSV files")

    return p.parse_args()


def main():
    args = parse_args()
    col_map = load_column_map()

    if args.mode == "api":
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = (
            datetime.strptime(args.end, "%Y-%m-%d").date()
            if args.end
            else date.today() - timedelta(days=1)
        )
        client = NetreferClient()
        raw = client.fetch_csv(start, end)
        rows = parse_csv(raw, col_map)
        records = transform(rows)
        count = load_to_mysql(records)
        log.info("ETL complete: %d rows upserted", count)

    elif args.mode == "csv":
        if args.file:
            process_csv_file(Path(args.file), col_map)
        elif args.dir:
            processed = Path(os.getenv("PROCESSED_DIR", str(Path(args.dir) / "processed")))
            process_csv_dir(Path(args.dir), processed, col_map)
        else:
            log.error("Provide --file or --dir for csv mode")
            sys.exit(1)


if __name__ == "__main__":
    main()
