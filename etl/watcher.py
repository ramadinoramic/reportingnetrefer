#!/usr/bin/env python3
"""
Automated CSV Drop-Folder Watcher
==================================
Polls CSV_DROP_DIR every 60 seconds. When a new *_YYYY-MM-DD.csv appears,
it is loaded automatically via the ETL and moved to processed/.

Usage:
    python etl/watcher.py           # runs forever (Ctrl-C to stop)
    python etl/watcher.py --once    # process whatever is in drop/ right now, then exit

The watcher uses the same ETL code and .env config as manual loads.
It will SKIP files whose date has already been successfully loaded
(i.e. already present in etl_runs with status='success' for that source_detail),
unless --reprocess is passed.

Run as a background process:
    nohup python etl/watcher.py >> logs/watcher.log 2>&1 &

Or via make:
    make watch
"""

import logging
import os
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

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] watcher: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("watcher")

DROP_DIR      = Path(os.getenv("CSV_DROP_DIR",  "./drop"))
PROCESSED_DIR = Path(os.getenv("PROCESSED_DIR", str(DROP_DIR / "processed")))
POLL_SECONDS  = int(os.getenv("WATCHER_POLL_SECONDS", 60))


def already_loaded(filename: str) -> bool:
    """Return True if etl_runs has a successful run for this exact filename."""
    try:
        conn   = db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM etl_runs WHERE source_detail = %s AND status = 'success' LIMIT 1",
            (filename,),
        )
        found = cursor.fetchone() is not None
        cursor.close()
        conn.close()
        return found
    except Exception as exc:
        log.warning("DB check failed (%s) — assuming not loaded", exc)
        return False


def scan_and_load():
    """Check drop/ for new CSVs and load any that haven't been processed yet."""
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
            import shutil
            shutil.move(str(f), str(PROCESSED_DIR / f.name))
            log.info("Moved to processed: %s", f.name)
            loaded += 1
        except Exception as exc:
            log.error("Failed to load %s: %s", f.name, exc, exc_info=True)

    if loaded:
        log.info("Watcher cycle complete: %d file(s) loaded", loaded)


def main():
    import argparse
    p = argparse.ArgumentParser(description="Netrefer CSV drop-folder watcher")
    p.add_argument("--once", action="store_true",
                   help="Process current drop/ contents and exit (no loop)")
    args = p.parse_args()

    log.info("Watcher starting — monitoring %s every %ds", DROP_DIR, POLL_SECONDS)
    DROP_DIR.mkdir(parents=True, exist_ok=True)

    if args.once:
        scan_and_load()
        return

    # Run once immediately, then on schedule
    scan_and_load()
    schedule.every(POLL_SECONDS).seconds.do(scan_and_load)

    while True:
        schedule.run_pending()
        time.sleep(5)


if __name__ == "__main__":
    main()
