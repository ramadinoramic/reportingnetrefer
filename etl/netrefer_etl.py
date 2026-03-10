#!/usr/bin/env python3
"""
Netrefer CSV → MySQL ETL
========================
Your Netrefer CSV has no date column, so you must supply --date when loading.

Usage:
    # Load a single file for a specific period
    python etl/netrefer_etl.py --file report.csv --date 2024-01-31

    # Load all CSVs from the drop folder (names like netrefer_2024-01-31.csv
    # are auto-dated; otherwise --date is required)
    python etl/netrefer_etl.py --dir --date 2024-01-31
"""

import argparse
import csv
import io
import logging
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional

import mysql.connector
import yaml
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("netrefer_etl")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_column_map() -> Dict[str, str]:
    map_path = Path(__file__).parent / "column_map.yaml"
    with open(map_path) as f:
        return yaml.safe_load(f)["column_map"]


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
# Parse-failure tracking
# ---------------------------------------------------------------------------
class _ParseFailures:
    """Counts coerce-to-zero events per column so they surface as warnings."""
    def __init__(self):
        self._counts: Dict[str, int] = {}

    def bump(self, col: str):
        self._counts[col] = self._counts.get(col, 0) + 1

    @property
    def total(self) -> int:
        return sum(self._counts.values())

    def summary(self) -> Optional[str]:
        if not self._counts:
            return None
        parts = [f"{col}({n})" for col, n in sorted(self._counts.items())]
        return "parse_failures: " + ", ".join(parts)


# ---------------------------------------------------------------------------
# CSV parser — handles Netrefer's quirky format
# ---------------------------------------------------------------------------
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y")


def _parse_date(raw: str) -> Optional[date]:
    if not raw:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _to_decimal(value: str, col: str = "", pf: Optional[_ParseFailures] = None) -> str:
    """Clean a currency/numeric string for MySQL DECIMAL insertion."""
    if not value:
        return "0"
    cleaned = value.replace(",", "").strip()
    try:
        float(cleaned)
        return cleaned
    except ValueError:
        if pf and col:
            pf.bump(col)
        return "0"


def _to_int(value: str, col: str = "", pf: Optional[_ParseFailures] = None) -> int:
    if not value:
        return 0
    try:
        return int(float(value.replace(",", "").strip()))
    except (ValueError, TypeError):
        if pf and col:
            pf.bump(col)
        return 0


def _find_header_row(lines: List[str]) -> Optional[int]:
    """Return index of the line that contains 'Affiliate ID'."""
    for i, line in enumerate(lines):
        if "Affiliate ID" in line:
            return i
    return None


def parse_csv(raw_text: str, col_map: Dict[str, str], report_date: date) -> List[Dict]:
    """
    Parse the Netrefer CSV into a list of dicts ready for MySQL.

    Handles:
    - sep= line at the top
    - Many blank lines before the header
    - Totals row at the bottom (blank or zero affiliate_id)
    """
    lines = raw_text.splitlines()

    header_idx = _find_header_row(lines)
    if header_idx is None:
        raise ValueError("Could not find header row containing 'Affiliate ID'")

    # Slice from the header row downward
    csv_text = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_text))

    rows = []
    for i, row in enumerate(reader, start=1):
        # Map headers → internal names
        mapped = {}
        for csv_col, value in row.items():
            if csv_col is None:
                continue
            db_col = col_map.get(csv_col.strip())
            if db_col:
                mapped[db_col] = (value or "").strip()

        affiliate_id = mapped.get("affiliate_id", "").strip()

        # Skip totals row (empty or "0" affiliate_id) and blank rows
        if not affiliate_id or affiliate_id == "0":
            log.debug("Row %d skipped (totals/blank): affiliate_id=%r", i, affiliate_id)
            continue

        # Inject the report date (not in CSV)
        mapped["report_date"] = report_date

        # Parse affiliate_signup_date if present
        raw_signup = mapped.get("affiliate_signup_date", "")
        mapped["affiliate_signup_date"] = _parse_date(raw_signup)  # None if unparseable

        rows.append(mapped)

    log.info("Parsed %d affiliate rows (excluding totals)", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------
INT_COLS = {
    "views", "unique_views", "clicks", "unique_clicks",
    "registrations", "depositing_customers", "active_customers",
    "new_depositing_customers", "new_active_customers",
    "first_depositors", "first_active_customers",
    "transactions", "sub_affiliates",
}

DECIMAL_COLS = {
    "deposits", "turnover", "contributions", "payouts",
    "gross_revenue", "bonuses", "adj_general", "chargebacks", "net_revenue",
    "rev_share_reward", "cpa_reward", "sub_affiliate_reward",
    "other_rewards", "total_reward",
}

STR_COLS = {
    "affiliate_id", "affiliate_name", "affiliate_email", "affiliate_status",
    "campaign_name", "media_type", "reward_plan_id", "reward_plan", "country",
}


def transform(rows: List[Dict], pf: Optional[_ParseFailures] = None) -> List[Dict]:
    result = []
    for row in rows:
        rec = {"report_date": row["report_date"],
               "affiliate_signup_date": row.get("affiliate_signup_date")}
        for col in STR_COLS:
            rec[col] = row.get(col, "")
        for col in INT_COLS:
            rec[col] = _to_int(row.get(col, "0"), col, pf)
        for col in DECIMAL_COLS:
            rec[col] = _to_decimal(row.get(col, "0"), col, pf)
        result.append(rec)
    return result


# ---------------------------------------------------------------------------
# Data quality validation
# ---------------------------------------------------------------------------

def validate_rows(records: List[Dict]) -> List[str]:
    """Structural sanity checks on parsed records. Returns warning strings."""
    warns: List[str] = []

    if not records:
        warns.append("ZERO_ROWS: CSV produced no loadable rows (totals/blanks only)")
        return warns

    total_clicks = sum(r.get("clicks", 0) for r in records)
    total_ftds   = sum(r.get("first_depositors", 0) for r in records)
    total_nr     = sum(float(r.get("net_revenue", 0)) for r in records)

    if total_clicks == 0 and total_ftds == 0 and total_nr == 0:
        warns.append(
            "ALL_ZEROS: clicks, first_depositors, and net_revenue are all 0 "
            "— possible empty or corrupt export"
        )

    bad_ftd = sum(
        1 for r in records
        if r.get("first_depositors", 0) > r.get("registrations", 0)
    )
    if bad_ftd:
        warns.append(f"FTD_GT_REG: {bad_ftd} row(s) have first_depositors > registrations")

    return warns


def check_outliers(conn, report_date: date) -> List[str]:
    """
    Compare today's aggregates to the 14-day rolling average.
    Returns a list of warning strings (empty = clean).
    """
    warns: List[str] = []
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT AVG(d_ftds), AVG(d_clicks), AVG(d_nr)
            FROM (
                SELECT
                    report_date,
                    SUM(first_depositors) AS d_ftds,
                    SUM(clicks)           AS d_clicks,
                    SUM(net_revenue)      AS d_nr
                FROM netrefer_stats
                WHERE report_date < %s
                  AND report_date >= %s - INTERVAL 14 DAY
                GROUP BY report_date
            ) _daily
        """, (report_date, report_date))
        row = cursor.fetchone()
        if not row or row[0] is None:
            return warns  # not enough history yet

        avg_ftds, avg_clicks, avg_nr = (float(x) if x else 0.0 for x in row)

        cursor.execute("""
            SELECT SUM(first_depositors), SUM(clicks), SUM(net_revenue)
            FROM netrefer_stats WHERE report_date = %s
        """, (report_date,))
        today = cursor.fetchone()
        if not today or today[0] is None:
            return warns

        today_ftds, today_clicks, today_nr = (float(x) if x else 0.0 for x in today)

        for metric, t_val, a_val in [
            ("FTDs",        today_ftds,   avg_ftds),
            ("clicks",      today_clicks, avg_clicks),
            ("net_revenue", today_nr,     avg_nr),
        ]:
            if a_val <= 0:
                continue
            ratio = t_val / a_val
            if ratio > 5.0:
                warns.append(
                    f"OUTLIER_HIGH {metric}: {ratio:.1f}x 14-day avg "
                    f"(today={t_val:.0f}, avg={a_val:.0f})"
                )
            elif ratio < 0.2 and t_val >= 0:
                warns.append(
                    f"OUTLIER_LOW {metric}: {ratio:.2f}x 14-day avg "
                    f"(today={t_val:.0f}, avg={a_val:.0f})"
                )
    finally:
        cursor.close()

    return warns


# ---------------------------------------------------------------------------
# MySQL loader
# ---------------------------------------------------------------------------
UPSERT_SQL = """
INSERT INTO netrefer_stats (
    report_date,
    affiliate_id, affiliate_name, affiliate_email, affiliate_status,
    affiliate_signup_date,
    campaign_name, media_type, reward_plan_id, reward_plan, country,
    views, unique_views, clicks, unique_clicks,
    registrations, depositing_customers, active_customers,
    new_depositing_customers, new_active_customers,
    first_depositors, first_active_customers,
    transactions, sub_affiliates,
    deposits, turnover, contributions, payouts,
    gross_revenue, bonuses, adj_general, chargebacks, net_revenue,
    rev_share_reward, cpa_reward, sub_affiliate_reward,
    other_rewards, total_reward
) VALUES (
    %(report_date)s,
    %(affiliate_id)s, %(affiliate_name)s, %(affiliate_email)s,
    %(affiliate_status)s, %(affiliate_signup_date)s,
    %(campaign_name)s, %(media_type)s, %(reward_plan_id)s,
    %(reward_plan)s, %(country)s,
    %(views)s, %(unique_views)s, %(clicks)s, %(unique_clicks)s,
    %(registrations)s, %(depositing_customers)s, %(active_customers)s,
    %(new_depositing_customers)s, %(new_active_customers)s,
    %(first_depositors)s, %(first_active_customers)s,
    %(transactions)s, %(sub_affiliates)s,
    %(deposits)s, %(turnover)s, %(contributions)s, %(payouts)s,
    %(gross_revenue)s, %(bonuses)s, %(adj_general)s,
    %(chargebacks)s, %(net_revenue)s,
    %(rev_share_reward)s, %(cpa_reward)s, %(sub_affiliate_reward)s,
    %(other_rewards)s, %(total_reward)s
)
ON DUPLICATE KEY UPDATE
    affiliate_name           = VALUES(affiliate_name),
    affiliate_email          = VALUES(affiliate_email),
    affiliate_status         = VALUES(affiliate_status),
    campaign_name            = VALUES(campaign_name),
    media_type               = VALUES(media_type),
    reward_plan              = VALUES(reward_plan),
    country                  = VALUES(country),
    views                    = VALUES(views),
    unique_views             = VALUES(unique_views),
    clicks                   = VALUES(clicks),
    unique_clicks            = VALUES(unique_clicks),
    registrations            = VALUES(registrations),
    depositing_customers     = VALUES(depositing_customers),
    active_customers         = VALUES(active_customers),
    new_depositing_customers = VALUES(new_depositing_customers),
    new_active_customers     = VALUES(new_active_customers),
    first_depositors         = VALUES(first_depositors),
    first_active_customers   = VALUES(first_active_customers),
    transactions             = VALUES(transactions),
    deposits                 = VALUES(deposits),
    turnover                 = VALUES(turnover),
    contributions            = VALUES(contributions),
    payouts                  = VALUES(payouts),
    gross_revenue            = VALUES(gross_revenue),
    bonuses                  = VALUES(bonuses),
    adj_general              = VALUES(adj_general),
    chargebacks              = VALUES(chargebacks),
    net_revenue              = VALUES(net_revenue),
    rev_share_reward         = VALUES(rev_share_reward),
    cpa_reward               = VALUES(cpa_reward),
    sub_affiliate_reward     = VALUES(sub_affiliate_reward),
    other_rewards            = VALUES(other_rewards),
    total_reward             = VALUES(total_reward),
    updated_at               = CURRENT_TIMESTAMP
"""

AUDIT_START  = """
    INSERT INTO etl_runs (run_id, mode, source_detail, status, rows_parsed)
    VALUES (%s, 'csv', %s, 'running', 0)
"""
AUDIT_FINISH = """
    UPDATE etl_runs
    SET finished_at   = CURRENT_TIMESTAMP,
        rows_upserted = %s,
        rows_parsed   = %s,
        status        = %s,
        error_message = %s,
        warnings      = %s
    WHERE run_id = %s
"""


def load_to_mysql(records: List[Dict], source: str,
                  rows_parsed: int = 0,
                  struct_warnings: Optional[List[str]] = None,
                  batch_size: int = 500) -> int:
    if not records:
        log.warning("No records to load for %s", source)
        return 0

    run_id = str(uuid.uuid4())
    conn   = db_connection()
    cursor = conn.cursor()
    total  = 0

    try:
        cursor.execute(AUDIT_START, (run_id, source))
        conn.commit()

        for i in range(0, len(records), batch_size):
            batch = records[i: i + batch_size]
            cursor.executemany(UPSERT_SQL, batch)
            conn.commit()
            total += cursor.rowcount
            log.info("Batch %d–%d: %d rows affected", i + 1, i + len(batch), cursor.rowcount)

        # Outlier check against rolling 14-day average
        report_date    = records[0]["report_date"] if records else None
        outlier_warns  = check_outliers(conn, report_date) if report_date else []
        all_warns      = (struct_warnings or []) + outlier_warns

        if all_warns:
            for w in all_warns:
                log.warning("[DQ] %s", w)

        warn_str = "; ".join(all_warns) if all_warns else None
        cursor.execute(AUDIT_FINISH, (total, rows_parsed, "success", None, warn_str, run_id))
        conn.commit()

    except Exception as exc:
        conn.rollback()
        try:
            warn_str = "; ".join(struct_warnings) if struct_warnings else None
            cursor.execute(AUDIT_FINISH, (total, rows_parsed, "failed", str(exc), warn_str, run_id))
            conn.commit()
        except Exception:
            pass
        raise
    finally:
        cursor.close()
        conn.close()

    return total


# ---------------------------------------------------------------------------
# Date auto-detection from filename
# ---------------------------------------------------------------------------
def date_from_filename(path: Path) -> Optional[date]:
    """Try to extract a date from the filename, e.g. report_2024-01-31.csv"""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", path.stem)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------
def _read_file(file_path: Path) -> str:
    """Read file trying UTF-8 (with BOM), then UTF-16 (Excel exports)."""
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return file_path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return file_path.read_text(encoding="latin-1")  # last resort


def process_file(file_path: Path, report_date: date, col_map: Dict[str, str]) -> int:
    log.info("Loading: %s (date: %s)", file_path.name, report_date)
    raw     = _read_file(file_path)
    rows    = parse_csv(raw, col_map, report_date)
    pf      = _ParseFailures()
    records = transform(rows, pf)

    # Collect all structural warnings before hitting the DB
    warns = validate_rows(records)
    if pf.summary():
        warns.append(pf.summary())
    if warns:
        for w in warns:
            log.warning("[DQ] %s  (%s)", w, file_path.name)

    count = load_to_mysql(records, source=file_path.name,
                          rows_parsed=len(rows),
                          struct_warnings=warns)
    log.info("Done: %d rows upserted from %s", count, file_path.name)
    return count


def process_drop_dir(drop_dir: Path, processed_dir: Path,
                     col_map: Dict[str, str], fallback_date: Optional[date]):
    csv_files = sorted(drop_dir.glob("*.csv"))
    if not csv_files:
        log.info("No CSV files found in %s", drop_dir)
        return

    processed_dir.mkdir(parents=True, exist_ok=True)

    for f in csv_files:
        report_date = date_from_filename(f) or fallback_date
        if not report_date:
            log.error(
                "Cannot determine date for %s. "
                "Rename file to include date (e.g. netrefer_2024-01-31.csv) "
                "or pass --date.", f.name
            )
            continue
        try:
            process_file(f, report_date, col_map)
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
                       help="Process all CSVs in CSV_DROP_DIR (.env)")
    p.add_argument(
        "--date", required=False,
        help="Report date YYYY-MM-DD. Required for --file. "
             "For --dir, files named *_YYYY-MM-DD.csv are auto-dated; "
             "others fall back to this value."
    )
    args = p.parse_args()

    col_map     = load_column_map()
    report_date = date.fromisoformat(args.date) if args.date else None

    if args.file:
        if not report_date:
            # Try filename first
            report_date = date_from_filename(Path(args.file))
        if not report_date:
            p.error("--date YYYY-MM-DD is required when using --file "
                    "(unless the filename contains the date, e.g. netrefer_2024-01-31.csv)")
        process_file(Path(args.file), report_date, col_map)

    else:
        drop_dir      = Path(os.getenv("CSV_DROP_DIR", "./drop"))
        processed_dir = Path(os.getenv("PROCESSED_DIR", str(drop_dir / "processed")))
        process_drop_dir(drop_dir, processed_dir, col_map, report_date)


if __name__ == "__main__":
    main()
