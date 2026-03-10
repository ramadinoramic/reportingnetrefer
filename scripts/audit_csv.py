#!/usr/bin/env python3
"""
audit_csv.py  –  Diagnose missing data between a Netrefer CSV and the database.

Usage:
    python scripts/audit_csv.py --file drop/netrefer_2026-03-09.csv

What it checks:
  1. How many rows the CSV contains
  2. Which CSV column headers are NOT in column_map.yaml (→ silently ignored)
  3. Which column_map.yaml columns are NOT in the CSV (→ will load as 0)
  4. Rows that would be SKIPPED (blank/zero affiliate_id)
  5. Rows that would COLLIDE on the unique key (report_date, affiliate_id, campaign_name)
     — these are overwritten by UPSERT, causing data loss
  6. How many rows are currently in the DB for that date
"""

import argparse
import csv
import io
import os
import sys
from collections import Counter
from pathlib import Path
from datetime import date, datetime
import re

import mysql.connector
import yaml
from dotenv import load_dotenv

load_dotenv()

DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y")


def _parse_date(raw: str):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def date_from_filename(path: Path):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.stem)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return None


def load_column_map():
    map_path = Path(__file__).parent.parent / "etl" / "column_map.yaml"
    with open(map_path) as f:
        return yaml.safe_load(f)["column_map"]


def read_file(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="latin-1")


def find_header_row(lines):
    for i, line in enumerate(lines):
        if "Affiliate ID" in line:
            return i
    return None


def db_connection():
    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
    )


def hr(char="─", width=70):
    print(char * width)


def main():
    parser = argparse.ArgumentParser(description="Audit Netrefer CSV vs DB")
    parser.add_argument("--file", required=True, help="Path to the CSV file")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    report_date = date_from_filename(path)
    col_map = load_column_map()

    raw = read_file(path)
    lines = raw.splitlines()

    hr("═")
    print(f"  NETREFER CSV AUDIT: {path.name}")
    hr("═")

    # ── Header detection ──────────────────────────────────────────────────────
    header_idx = find_header_row(lines)
    if header_idx is None:
        print("ERROR: Could not find 'Affiliate ID' header row in the CSV.")
        sys.exit(1)
    print(f"\n[1] Header found at line {header_idx + 1} (0-indexed: {header_idx})")

    csv_text = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_text))
    csv_headers = [h.strip() for h in (reader.fieldnames or []) if h]
    print(f"    CSV has {len(csv_headers)} columns")

    # ── Column mapping check ───────────────────────────────────────────────────
    mapped_headers   = [h for h in csv_headers if col_map.get(h)]
    unmapped_headers = [h for h in csv_headers if not col_map.get(h)]
    missing_from_csv = [k for k in col_map if k not in csv_headers]

    print(f"\n[2] Column mapping")
    print(f"    Mapped   (will load):  {len(mapped_headers)}")
    if unmapped_headers:
        print(f"    UNMAPPED (silently ignored) — {len(unmapped_headers)} headers:")
        for h in unmapped_headers:
            print(f"      - \"{h}\"")
    if missing_from_csv:
        print(f"    MISSING from CSV (will default to 0) — {len(missing_from_csv)} columns:")
        for h in missing_from_csv:
            print(f"      - \"{h}\" → {col_map[h]}")

    # ── Row analysis ───────────────────────────────────────────────────────────
    print(f"\n[3] Row analysis")
    all_rows = list(reader)
    total_rows = len(all_rows)
    print(f"    Total rows in CSV (after header): {total_rows}")

    skipped = []
    kept = []
    for row in all_rows:
        aff_id = row.get("Affiliate ID", "").strip()
        if not aff_id or aff_id == "0":
            skipped.append(row)
        else:
            kept.append(row)

    print(f"    Skipped (totals/blank affiliate_id): {len(skipped)}")
    print(f"    Will be loaded: {len(kept)}")

    # ── Unique key collision check ─────────────────────────────────────────────
    print(f"\n[4] Unique key collision check  (report_date, affiliate_id, campaign_name)")
    key_counts: Counter = Counter()
    key_rows = {}
    for row in kept:
        aff_id      = row.get("Affiliate ID", "").strip()
        camp_name   = row.get("Default Marketing Source Name", "").strip()[:100]
        key = (str(report_date) if report_date else "?", aff_id, camp_name)
        key_counts[key] += 1
        key_rows.setdefault(key, []).append(row)

    collisions = {k: v for k, v in key_counts.items() if v > 1}
    if collisions:
        lost_rows = sum(v - 1 for v in collisions.values())
        print(f"    *** {len(collisions)} key(s) have duplicates → {lost_rows} row(s) will be OVERWRITTEN by UPSERT ***")
        print(f"    This means {lost_rows} rows of data are being silently lost!\n")
        print(f"    Colliding keys (showing up to 10):")
        for (rdate, aff, camp), count in list(collisions.items())[:10]:
            print(f"      affiliate_id={aff!r}  campaign={camp!r}  →  {count} rows (only last survives)")
            # Show what differs between colliding rows
            rows = key_rows[(rdate, aff, camp)]
            diff_cols = []
            ref = rows[0]
            for col in csv_headers:
                vals = set(r.get(col, "").strip() for r in rows)
                if len(vals) > 1:
                    diff_cols.append((col, vals))
            if diff_cols:
                print(f"        Columns that differ between these rows:")
                for col, vals in diff_cols[:5]:
                    print(f"          {col}: {vals}")
    else:
        print(f"    No collisions — all {len(kept)} rows have unique keys. UPSERT is safe.")

    # ── DB comparison ─────────────────────────────────────────────────────────
    if report_date:
        print(f"\n[5] Database comparison for {report_date}")
        try:
            conn = db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM netrefer_stats WHERE report_date = %s",
                (report_date,)
            )
            db_count = cur.fetchone()[0]
            cur.execute(
                "SELECT SUM(clicks), SUM(net_revenue) FROM netrefer_stats WHERE report_date = %s",
                (report_date,)
            )
            row = cur.fetchone()
            db_clicks = row[0] or 0
            db_revenue = row[1] or 0
            cur.close()
            conn.close()

            print(f"    Rows in DB for {report_date}: {db_count}")
            print(f"    DB totals: clicks={db_clicks}  net_revenue={db_revenue}")

            if db_count < len(kept):
                diff = len(kept) - db_count
                print(f"    *** DB has {diff} FEWER rows than the CSV → data was lost during load ***")
            elif db_count > len(kept):
                diff = db_count - len(kept)
                print(f"    DB has {diff} MORE rows than CSV (data from a previous load still present)")
            else:
                print(f"    Row counts match.")

        except Exception as e:
            print(f"    Could not connect to DB: {e}")
            print(f"    (Set MYSQL_HOST/USER/PASSWORD/DATABASE in .env)")
    else:
        print(f"\n[5] DB comparison skipped — could not detect date from filename")
        print(f"    Tip: name your file netrefer_YYYY-MM-DD.csv")

    hr("═")
    print()


if __name__ == "__main__":
    main()
