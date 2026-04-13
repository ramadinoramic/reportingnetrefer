#!/usr/bin/env python3
"""
Automated CSV Drop-Folder Watcher
==================================
Polls two drop folders every 60 seconds:

  drop/              — Youwin files  (netrefer_YYYY-MM-DD.csv)
  drop/bahigo_wettigo/ — Bahigo & Wettigo file PAIRS:
                          netrefer_YYYY-MM-DD.csv        (affiliate stats)
                          netrefer_custom_YYYY-MM-DD.csv  (customer report)

Youwin files are loaded individually into netrefer_stats as before.
Bahigo/Wettigo pairs are only processed when BOTH files for the same date
are present; results go into bahigo_wettigo_stats.

Usage:
    python etl/watcher.py           # runs forever (Ctrl-C to stop)
    python etl/watcher.py --once    # process whatever is present, then exit
"""

import logging
import os
import shutil
import sys
import time
from datetime import date
from pathlib import Path

import schedule
from dotenv import load_dotenv

load_dotenv()

# Add project root so we can import etl package
sys.path.insert(0, str(Path(__file__).parent.parent))

from etl.netrefer_etl import (
    date_from_filename,
    db_connection,
    load_column_map,
    process_file,
)
from etl.bahigo_wettigo_etl import process_pair

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] watcher: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("watcher")

DROP_DIR      = Path(os.getenv("CSV_DROP_DIR",  "./drop"))
PROCESSED_DIR = Path(os.getenv("PROCESSED_DIR", str(DROP_DIR / "processed")))
BW_DROP_DIR   = DROP_DIR / "bahigo_wettigo"
BW_PROC_DIR   = BW_DROP_DIR / "processed"
POLL_SECONDS  = int(os.getenv("WATCHER_POLL_SECONDS", 60))


def already_loaded(source_detail: str) -> bool:
    """Return True if etl_runs has a successful run for this source_detail."""
    try:
        conn   = db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM etl_runs WHERE source_detail = %s AND status = 'success' LIMIT 1",
            (source_detail,),
        )
        found = cursor.fetchone() is not None
        cursor.close()
        conn.close()
        return found
    except Exception as exc:
        log.warning("DB check failed (%s) — assuming not loaded", exc)
        return False


# ──────────────────────────────────────────────
# Youwin watcher (unchanged logic)
# ──────────────────────────────────────────────

def scan_and_load():
    """Check drop/ for new Youwin CSVs and load any that haven't been processed yet."""
    csv_files = sorted(DROP_DIR.glob("*.csv"))
    if not csv_files:
        log.debug("drop/ is empty — nothing to do")
        return

    col_map = load_column_map()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    loaded = 0

    for f in csv_files:
        report_date = date_from_filename(f)
        if not report_date:
            log.warning(
                "Skipping %s — cannot determine date. "
                "Rename to netrefer_YYYY-MM-DD.csv",
                f.name,
            )
            continue

        if already_loaded(f.name):
            log.info("Already loaded: %s — skipping", f.name)
            continue

        log.info("New file detected: %s (date: %s)", f.name, report_date)
        try:
            process_file(f, report_date, col_map)
            shutil.move(str(f), str(PROCESSED_DIR / f.name))
            log.info("Moved to processed: %s", f.name)
            loaded += 1
        except Exception as exc:
            log.error("Failed to load %s: %s", f.name, exc, exc_info=True)

    if loaded:
        log.info("Youwin watcher cycle: %d file(s) loaded", loaded)


# ──────────────────────────────────────────────
# Bahigo & Wettigo watcher (paired files)
# ──────────────────────────────────────────────

def scan_and_load_bw():
    """
    Check drop/bahigo_wettigo/ for paired files and process any new pairs.

    A pair is: netrefer_YYYY-MM-DD.csv  +  netrefer_custom_YYYY-MM-DD.csv
    for the same date.  Both files must be present before processing starts.
    The audit key stored in etl_runs is 'bw:netrefer_custom_YYYY-MM-DD.csv'.
    """
    if not BW_DROP_DIR.exists():
        return

    # Find all customer report files — they are the canonical "pair key"
    customer_files = sorted(BW_DROP_DIR.glob("netrefer_custom_*.csv"))
    if not customer_files:
        log.debug("drop/bahigo_wettigo/ — no customer report files found")
        return

    BW_PROC_DIR.mkdir(parents=True, exist_ok=True)
    loaded = 0

    for customer_file in customer_files:
        report_date = date_from_filename(customer_file)
        if not report_date:
            log.warning(
                "BW: skipping %s — cannot determine date", customer_file.name
            )
            continue

        audit_key = f"bw:{customer_file.name}"
        if already_loaded(audit_key):
            log.info("BW: already loaded: %s — skipping", customer_file.name)
            continue

        # Look for the matching affiliate stats file
        stats_file = BW_DROP_DIR / f"netrefer_{report_date.strftime('%Y-%m-%d')}.csv"
        if not stats_file.exists():
            log.info(
                "BW: waiting for stats file %s (customer file ready, stats not yet dropped)",
                stats_file.name,
            )
            continue

        log.info(
            "BW: new pair detected for %s — stats=%s, customers=%s",
            report_date, stats_file.name, customer_file.name,
        )
        try:
            process_pair(stats_file, customer_file, report_date)
            shutil.move(str(stats_file),    str(BW_PROC_DIR / stats_file.name))
            shutil.move(str(customer_file), str(BW_PROC_DIR / customer_file.name))
            log.info(
                "BW: moved to processed: %s + %s",
                stats_file.name, customer_file.name,
            )
            loaded += 1
        except Exception as exc:
            log.error(
                "BW: failed to process pair for %s: %s",
                report_date, exc, exc_info=True,
            )

    if loaded:
        log.info("BW watcher cycle: %d pair(s) loaded", loaded)


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(description="Netrefer CSV drop-folder watcher")
    p.add_argument("--once", action="store_true",
                   help="Process current drop/ contents and exit (no loop)")
    args = p.parse_args()

    log.info(
        "Watcher starting — Youwin: %s  |  BW: %s  |  poll every %ds",
        DROP_DIR, BW_DROP_DIR, POLL_SECONDS,
    )
    DROP_DIR.mkdir(parents=True, exist_ok=True)
    BW_DROP_DIR.mkdir(parents=True, exist_ok=True)

    if args.once:
        scan_and_load()
        scan_and_load_bw()
        return

    # Run once immediately, then on schedule
    scan_and_load()
    scan_and_load_bw()
    schedule.every(POLL_SECONDS).seconds.do(scan_and_load)
    schedule.every(POLL_SECONDS).seconds.do(scan_and_load_bw)

    while True:
        schedule.run_pending()
        time.sleep(5)


if __name__ == "__main__":
    main()

