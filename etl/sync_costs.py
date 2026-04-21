#!/usr/bin/env python3
"""
sync_costs.py — Sync affiliate cost data from Google Sheets → MySQL.

Two sources are merged into `traffic_costs`:

1. **Deal-based costs** (automatic):
   Reads deal terms from the "Deals" tab in Google Sheets, then queries actual
   performance from bahigo_wettigo_stats / netrefer_stats and calculates daily
   costs according to the deal type:
     CPA      → ftds × cpa_rate_eur
     RevShare → net_revenue × revshare_pct / 100
     Flat     → flat_monthly_eur / days_in_calendar_month
     Hybrid   → CPA cost + RevShare cost

2. **Manual costs** (override):
   Reads direct cost entries from the "Manual Costs" tab (ad spend, bonuses,
   one-off payments).  These are inserted as cost_source='manual' and coexist
   with deal-based entries in the same date range.

Setup
-----
1. Create a Google Service Account and download the JSON key.
2. Share your Google Sheet with the service account email (viewer is enough).
3. Add to .env:
       GOOGLE_SA_KEY_PATH=credentials/google_sa.json
       COST_SHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms

Usage
-----
    python etl/sync_costs.py                        # last 30 days
    python etl/sync_costs.py --days 7
    python etl/sync_costs.py --from 2026-04-01 --to 2026-04-15
    python etl/sync_costs.py --dry-run              # print rows, no DB writes
"""

import argparse
import calendar
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] sync_costs: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("sync_costs")

# ──────────────────────────────────────────────
# Google Sheets reader
# ──────────────────────────────────────────────

def _get_sheet_rows(sheet_id: str, tab_name: str, key_path: str) -> List[Dict]:
    """
    Read all rows from a Google Sheets tab and return a list of dicts keyed
    by the header row.  Requires the `gspread` package.
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        log.error(
            "Missing packages: run  pip install gspread google-auth  "
            "or  pip install -r requirements.txt"
        )
        sys.exit(1)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds  = Credentials.from_service_account_file(key_path, scopes=scopes)
    client = gspread.authorize(creds)

    try:
        sheet = client.open_by_key(sheet_id)
        ws    = sheet.worksheet(tab_name)
    except Exception as e:
        log.error("Could not open sheet %s / tab '%s': %s", sheet_id, tab_name, e)
        raise

    records = ws.get_all_records(numericise_ignore=["all"])
    log.info("Sheet '%s': %d rows loaded", tab_name, len(records))
    return records


# ──────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────

def _db() -> mysql.connector.MySQLConnection:
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", 3308)),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"),
    )


def _flt(v, default=0.0) -> float:
    try:
        return float(str(v).replace(",", "").strip()) if v not in ("", None) else default
    except (ValueError, TypeError):
        return default


def _parse_date(v) -> Optional[date]:
    if not v or str(v).strip() in ("", "-", "N/A"):
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    return None


# ──────────────────────────────────────────────
# Sync deals tab → affiliate_deals
# ──────────────────────────────────────────────

UPSERT_DEAL = """
INSERT INTO affiliate_deals
    (affiliate_id, brand, deal_type, cpa_rate_eur, revshare_pct, flat_monthly_eur,
     valid_from, valid_to, notes)
VALUES
    (%(affiliate_id)s, %(brand)s, %(deal_type)s, %(cpa_rate_eur)s, %(revshare_pct)s,
     %(flat_monthly_eur)s, %(valid_from)s, %(valid_to)s, %(notes)s)
ON DUPLICATE KEY UPDATE
    deal_type        = VALUES(deal_type),
    cpa_rate_eur     = VALUES(cpa_rate_eur),
    revshare_pct     = VALUES(revshare_pct),
    flat_monthly_eur = VALUES(flat_monthly_eur),
    valid_to         = VALUES(valid_to),
    notes            = VALUES(notes),
    synced_at        = CURRENT_TIMESTAMP
"""


def sync_deals(rows: List[Dict], conn, dry_run: bool) -> int:
    """Upsert deal terms from Google Sheets into affiliate_deals."""
    records = []
    for r in rows:
        aff_id = str(r.get("affiliate_id", "")).strip()
        if not aff_id:
            continue
        valid_from = _parse_date(r.get("start_date") or r.get("valid_from"))
        if not valid_from:
            log.warning("Deals row skipped — missing start_date for affiliate %s", aff_id)
            continue
        records.append({
            "affiliate_id":    aff_id,
            "brand":           str(r.get("brand", "")).strip(),
            "deal_type":       str(r.get("deal_type", "CPA")).strip().upper(),
            "cpa_rate_eur":    _flt(r.get("cpa_rate_eur")),
            "revshare_pct":    _flt(r.get("revshare_pct")),
            "flat_monthly_eur":_flt(r.get("flat_monthly_eur")),
            "valid_from":      valid_from,
            "valid_to":        _parse_date(r.get("end_date") or r.get("valid_to")),
            "notes":           str(r.get("notes", "")).strip() or None,
        })

    if dry_run:
        log.info("[DRY RUN] Would upsert %d deal rows", len(records))
        return len(records)

    cur = conn.cursor()
    cur.executemany(UPSERT_DEAL, records)
    conn.commit()
    log.info("Deals synced: %d rows upserted", cur.rowcount)
    cur.close()
    return len(records)


# ──────────────────────────────────────────────
# Sync manual costs tab → traffic_costs
# ──────────────────────────────────────────────

UPSERT_COST = """
INSERT INTO traffic_costs
    (report_date, affiliate_id, brand, geo, cost_eur, cost_source, cost_type, notes)
VALUES
    (%(report_date)s, %(affiliate_id)s, %(brand)s, %(geo)s,
     %(cost_eur)s, %(cost_source)s, %(cost_type)s, %(notes)s)
ON DUPLICATE KEY UPDATE
    cost_eur    = VALUES(cost_eur),
    cost_type   = VALUES(cost_type),
    notes       = VALUES(notes),
    synced_at   = CURRENT_TIMESTAMP
"""


def sync_manual_costs(rows: List[Dict], conn, dry_run: bool) -> int:
    """Upsert manual cost entries from Google Sheets into traffic_costs."""
    records = []
    for r in rows:
        aff_id = str(r.get("affiliate_id", "")).strip()
        rdate  = _parse_date(r.get("date"))
        amount = _flt(r.get("amount_eur"))
        if not aff_id or not rdate or amount == 0:
            continue
        records.append({
            "report_date":  rdate,
            "affiliate_id": aff_id,
            "brand":        str(r.get("brand", "")).strip(),
            "geo":          str(r.get("geo", "")).strip(),
            "cost_eur":     amount,
            "cost_source":  "manual",
            "cost_type":    str(r.get("cost_source", r.get("cost_type", "ad_spend"))).strip(),
            "notes":        str(r.get("notes", "")).strip() or None,
        })

    if dry_run:
        log.info("[DRY RUN] Would upsert %d manual cost rows", len(records))
        return len(records)

    cur = conn.cursor()
    cur.executemany(UPSERT_COST, records)
    conn.commit()
    log.info("Manual costs synced: %d rows upserted", cur.rowcount)
    cur.close()
    return len(records)


# ──────────────────────────────────────────────
# Deal-based cost calculation
# ──────────────────────────────────────────────

FETCH_DEALS_SQL = """
    SELECT affiliate_id, brand, deal_type,
           cpa_rate_eur, revshare_pct, flat_monthly_eur,
           valid_from, valid_to
    FROM affiliate_deals
    WHERE valid_from <= %(to_date)s
      AND (valid_to IS NULL OR valid_to >= %(from_date)s)
"""

PERF_BW_SQL = """
    SELECT report_date, affiliate_id, brand_name, geo,
           SUM(first_depositors) AS ftds,
           SUM(registrations)    AS regs,
           SUM(net_revenue)      AS ngr,
           SUM(views)            AS views
    FROM bahigo_wettigo_stats
    WHERE affiliate_id = %(affiliate_id)s
      AND report_date BETWEEN %(from_date)s AND %(to_date)s
      AND (%(brand)s = '' OR brand_name = %(brand)s)
    GROUP BY report_date, affiliate_id, brand_name, geo
"""

PERF_YOUWIN_SQL = """
    SELECT report_date, affiliate_id, '' AS brand_name, country AS geo,
           SUM(first_depositors) AS ftds,
           SUM(registrations)    AS regs,
           SUM(net_revenue)      AS ngr,
           SUM(views)            AS views
    FROM netrefer_stats
    WHERE affiliate_id = %(affiliate_id)s
      AND report_date BETWEEN %(from_date)s AND %(to_date)s
    GROUP BY report_date, affiliate_id, country
"""


def _days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def calculate_deal_costs(from_date: date, to_date: date, conn, dry_run: bool) -> int:
    """
    For each active deal, query actual performance and calculate daily cost.
    Inserts results into traffic_costs with cost_source='deal'.
    """
    cur = conn.cursor(dictionary=True)
    cur.execute(FETCH_DEALS_SQL, {"from_date": from_date, "to_date": to_date})
    deals = cur.fetchall()
    log.info("Active deals in date range: %d", len(deals))

    cost_records = []

    for deal in deals:
        aff_id     = deal["affiliate_id"]
        brand      = deal["brand"]
        deal_type  = deal["deal_type"]
        cpa_rate   = float(deal["cpa_rate_eur"] or 0)
        revshare   = float(deal["revshare_pct"] or 0) / 100
        flat_mthly = float(deal["flat_monthly_eur"] or 0)
        d_from     = max(from_date, deal["valid_from"])
        d_to       = to_date if deal["valid_to"] is None else min(to_date, deal["valid_to"])

        perf_rows = []
        for sql in (PERF_BW_SQL, PERF_YOUWIN_SQL):
            cur.execute(sql, {"affiliate_id": aff_id, "from_date": d_from,
                              "to_date": d_to, "brand": brand})
            perf_rows.extend(cur.fetchall())

        if not perf_rows and deal_type != "Flat":
            continue

        if deal_type == "Flat":
            dates_in_range = {r["report_date"] for r in perf_rows}
            current = d_from
            while current <= d_to:
                if current not in dates_in_range:
                    perf_rows.append({
                        "report_date": current, "affiliate_id": aff_id,
                        "brand_name": brand, "geo": "",
                        "ftds": 0, "regs": 0, "ngr": 0, "views": 0,
                    })
                current += timedelta(days=1)

        for row in perf_rows:
            rdate     = row["report_date"]
            ftds      = float(row.get("ftds") or 0)
            ngr       = float(row.get("ngr")  or 0)
            row_brand = str(row.get("brand_name") or brand)
            row_geo   = str(row.get("geo") or "")

            if deal_type == "CPA":
                cost  = ftds * cpa_rate
                ctype = "CPA"
            elif deal_type == "RevShare":
                cost  = ngr * revshare
                ctype = "RevShare"
            elif deal_type == "Flat":
                cost  = flat_mthly / _days_in_month(rdate)
                ctype = "Flat"
            elif deal_type == "Hybrid":
                cost  = (ftds * cpa_rate) + (ngr * revshare)
                ctype = "Hybrid"
            else:
                continue

            if cost < 0:
                cost = 0

            cost_records.append({
                "report_date":  rdate,
                "affiliate_id": aff_id,
                "brand":        row_brand,
                "geo":          row_geo,
                "cost_eur":     round(cost, 4),
                "cost_source":  "deal",
                "cost_type":    ctype,
                "notes":        None,
            })

    cur.close()

    if dry_run:
        log.info("[DRY RUN] Would upsert %d deal-based cost rows", len(cost_records))
        for r in cost_records[:5]:
            log.info("  %s", r)
        return len(cost_records)

    if cost_records:
        cur2 = conn.cursor()
        cur2.executemany(UPSERT_COST, cost_records)
        conn.commit()
        log.info("Deal costs calculated: %d rows upserted", cur2.rowcount)
        cur2.close()
    else:
        log.info("No deal-based cost rows to write")

    return len(cost_records)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Sync affiliate costs from Google Sheets to MySQL"
    )
    p.add_argument("--days",       type=int, default=30,
                   help="Number of past days to recalculate (default: 30)")
    p.add_argument("--from",       dest="from_date",
                   help="Start date YYYY-MM-DD (overrides --days)")
    p.add_argument("--to",         dest="to_date",
                   help="End date YYYY-MM-DD (default: today)")
    p.add_argument("--dry-run",    action="store_true",
                   help="Print what would be written without touching the DB")
    p.add_argument("--skip-sheets", action="store_true",
                   help="Skip Google Sheets sync — only recalculate deal costs from DB")
    args = p.parse_args()

    today     = date.today()
    to_date   = _parse_date(args.to_date)   or today
    from_date = _parse_date(args.from_date) or (today - timedelta(days=args.days - 1))

    log.info("Cost sync: %s → %s  (dry_run=%s)", from_date, to_date, args.dry_run)

    sheet_id = os.getenv("COST_SHEET_ID", "")
    key_path = os.getenv("GOOGLE_SA_KEY_PATH", "credentials/google_sa.json")

    conn = None if args.dry_run else _db()

    try:
        if not args.skip_sheets:
            if not sheet_id:
                log.warning(
                    "COST_SHEET_ID not set in .env — skipping Google Sheets sync."
                )
            elif not Path(key_path).exists():
                log.warning(
                    "Google SA key not found at %s — skipping Sheets sync.", key_path
                )
            else:
                log.info("Syncing Deals tab …")
                deal_rows = _get_sheet_rows(sheet_id, "Deals", key_path)
                sync_deals(deal_rows, conn, dry_run=args.dry_run)

                log.info("Syncing Manual Costs tab …")
                manual_rows = _get_sheet_rows(sheet_id, "Manual Costs", key_path)
                sync_manual_costs(manual_rows, conn, dry_run=args.dry_run)

        log.info("Calculating deal-based costs for %s → %s …", from_date, to_date)
        db_conn = conn or _db()
        calculate_deal_costs(from_date, to_date, db_conn, dry_run=args.dry_run)
        if not conn:
            db_conn.close()

        log.info("Cost sync complete.")

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.error("Fatal: %s", e, exc_info=True)
        sys.exit(1)
