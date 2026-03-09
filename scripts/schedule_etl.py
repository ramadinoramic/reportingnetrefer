#!/usr/bin/env python3
"""
Scheduled ETL runner – fetches yesterday's Netrefer data every day at 06:00.
Run as a long-lived process (e.g. via systemd or Docker).

    python scripts/schedule_etl.py
"""

import logging
import subprocess
import sys
from datetime import date, timedelta

import schedule
import time

log = logging.getLogger("scheduler")
logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")


def run_etl():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    log.info("Starting daily ETL for %s", yesterday)
    result = subprocess.run(
        [sys.executable, "etl/netrefer_etl.py", "api", "--start", yesterday, "--end", yesterday],
        capture_output=False,
    )
    if result.returncode != 0:
        log.error("ETL failed with exit code %d", result.returncode)
    else:
        log.info("ETL completed successfully")


# Run once immediately on start, then daily at 06:00
run_etl()
schedule.every().day.at("06:00").do(run_etl)

log.info("Scheduler running – daily ETL at 06:00")
while True:
    schedule.run_pending()
    time.sleep(60)
