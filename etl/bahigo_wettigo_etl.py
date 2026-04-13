#!/usr/bin/env python3
"""
Bahigo & Wettigo dual-brand ETL
================================
Processes two daily CSV files that must share the same report date:

  drop/bahigo_wettigo/netrefer_YYYY-MM-DD.csv
      Affiliate performance stats — same Netrefer format as Youwin.
      Provides: clicks, views, commission per affiliate.

  drop/bahigo_wettigo/netrefer_custom_YYYY-MM-DD.csv
      Customer-level report — one row per customer with Brand + Country.
      Provides: exact brand/geo breakdown of revenue and conversions.

The two files are joined on Affiliate ID.  Customer-level metrics (FTDs,
registrations, deposits, revenue) come from the customer report exactly.
Traffic (clicks, views) and commission are split proportionally across
brand/geo combinations by customer count.

Results land in bahigo_wettigo_stats — separate from netrefer_stats (Youwin).

Usage:
    python etl/bahigo_wettigo_etl.py \\
        --stats drop/bahigo_wettigo/netrefer_2026-04-12.csv \\
        --customers drop/bahigo_wettigo/netrefer_custom_2026-04-12.csv

    # Date is auto-detected from the customer filename; override with --date.
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
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import mysql.connector
import yaml
from dotenv import load_dotenv

load_dotenv()

# Reuse low-level helpers from the existing Youwin ETL
sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.netrefer_etl import (
    _parse_date,
    _read_file,
    _to_decimal,
    _to_int,
    _ParseFailures,
    date_from_filename,
    db_connection,
    load_column_map,
    parse_csv as parse_affiliate_stats_csv,
    transform as transform_affiliate_stats,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] bw_etl: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bw_etl")


# ──────────────────────────────────────────────
# Customer report parser
# ──────────────────────────────────────────────

# Brand name normalisation: maps known alternate spellings to the canonical name.
# 'Bahibi' appears in some Netrefer exports but is a distinct internal entity —
# do NOT merge it into Bahigo.  Add only confirmed aliases here.
_BRAND_ALIASES: Dict[str, str] = {
    "bahigo": "Bahigo",
    "wettigo": "Wettigo",
}


def _normalize_brand(raw: str) -> str:
    """Return the canonical brand name, or the original value if not recognised."""
    return _BRAND_ALIASES.get(raw.strip().lower(), raw.strip())


def _clean_euro(value: str) -> float:
    """Convert '€ 28.85' or '€  1,234.56' or '-28.85' to float."""
    if not value:
        return 0.0
    cleaned = value.strip().lstrip("€").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _detect_delimiter(first_line: str) -> str:
    """Detect whether the CSV uses tab or comma as delimiter."""
    tabs   = first_line.count("\t")
    commas = first_line.count(",")
    return "\t" if tabs > commas else ","


def load_customer_column_map() -> Dict[str, str]:
    map_path = Path(__file__).parent / "column_map_customer.yaml"
    with open(map_path) as f:
        return yaml.safe_load(f)["column_map"]


def parse_customer_report(raw_text: str, col_map: Dict[str, str]) -> List[Dict]:
    """
    Parse the customer-level Netrefer report.
    Returns a flat list of row dicts with internal field names.
    """
    lines = raw_text.splitlines()

    # Find header row (must contain 'Brand Name')
    header_idx = None
    for i, line in enumerate(lines):
        if "Brand Name" in line:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            "Could not find header row in customer report "
            "(expected a column named 'Brand Name')"
        )

    data_block = "\n".join(lines[header_idx:])
    delimiter  = _detect_delimiter(lines[header_idx])
    reader     = csv.DictReader(io.StringIO(data_block), delimiter=delimiter)

    rows = []
    for raw_row in reader:
        # Strip whitespace from keys and values
        row = {
            (k.strip() if k else ""): (v.strip() if v else "")
            for k, v in raw_row.items()
        }

        # Map headers → internal names; skip unmapped keys
        mapped = {}
        for csv_col, value in row.items():
            internal = col_map.get(csv_col)
            if internal:
                mapped[internal] = value

        affiliate_id = mapped.get("affiliate_id", "").strip()
        brand_name   = _normalize_brand(mapped.get("brand_name", ""))
        country_name = mapped.get("country_name", "Unknown").strip()

        if not affiliate_id or not brand_name:
            continue

        rows.append({
            "affiliate_id": affiliate_id,
            "brand_name":   brand_name,
            "country_name": country_name,
            "signup_date":  mapped.get("signup_date", ""),
            "ftd_date":     mapped.get("ftd_date",    ""),
            "gross_revenue": _clean_euro(mapped.get("gross_revenue", "")),
            "net_revenue":   _clean_euro(mapped.get("net_revenue",   "")),
            "deposits":      _clean_euro(mapped.get("deposits",      "")),
            "bonuses":       _clean_euro(mapped.get("bonuses",       "")),
            "adj_general":   _clean_euro(mapped.get("adj_general",   "")),
        })

    log.info("Customer report: parsed %d customer rows", len(rows))
    return rows


# ──────────────────────────────────────────────
# Aggregation
# ──────────────────────────────────────────────

# Netrefer uses these strings to represent "no date" in date columns.
_NULL_DATE_VALUES = frozenset({
    "", "n/a", "na", "null", "none", "-", "--", "0",
    "00/00/0000", "01/01/1900", "1900-01-01",
})


def _is_real_date(value: str) -> bool:
    """Return True only when the string looks like a real date, not a placeholder."""
    return bool(value) and value.strip().lower() not in _NULL_DATE_VALUES


_CUSTOMER_DATE_FMTS = (
    "%d/%m/%Y %H:%M:%S",   # Netrefer default: 12/04/2026 00:00:00
    "%d/%m/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%m/%d/%Y",
)


def _parse_customer_date(value: str) -> Optional[date]:
    """Parse a date string from the Netrefer customer report into a date object.

    Returns None if the value is empty, a placeholder, or unrecognised.
    """
    if not _is_real_date(value):
        return None
    v = value.strip()
    for fmt in _CUSTOMER_DATE_FMTS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def aggregate_customers(
    rows: List[Dict],
    report_date: date,
) -> Dict[Tuple[str, str, str], Dict]:
    """
    Aggregate customer rows by (affiliate_id, brand_name, country_name).

    Works with both file types Netrefer produces:
      • Daily new-signup file  — every row has signup_date == report_date.
      • Cumulative/range file  — contains all historical customers.

    In both cases we filter by report_date so only the relevant day's
    activity is counted:
      registrations    = customers whose signup_date == report_date
      first_depositors = customers whose ftd_date    == report_date
      revenue/deposits = summed only from rows that are active today
                         (signup OR FTD on report_date)

    total_customers is counted for ALL rows and is used purely for
    proportional traffic/commission weighting across brand/geo groups.
    """
    result: Dict[Tuple, Dict] = defaultdict(lambda: {
        "registrations":        0,
        "first_depositors":     0,
        "depositing_customers": 0,
        "gross_revenue":        0.0,
        "net_revenue":          0.0,
        "deposits":             0.0,
        "bonuses":              0.0,
        "adj_general":          0.0,
        "total_customers":      0,   # used for proportional traffic weighting
    })

    # Diagnostic: log a sample of the raw date values we see
    ftd_samples: list = []

    for r in rows:
        key = (r["affiliate_id"], r["brand_name"], r["country_name"])
        agg = result[key]

        # Always count toward total_customers (for proportional weighting).
        agg["total_customers"] += 1

        signup_dt = _parse_customer_date(r["signup_date"])
        ftd_dt    = _parse_customer_date(r["ftd_date"])

        is_new_signup = (signup_dt == report_date)
        is_ftd_today  = (ftd_dt    == report_date)

        # Count registration only if the customer signed up on report_date.
        if is_new_signup:
            agg["registrations"] += 1

        # Count FTD only if the first deposit happened on report_date.
        if is_ftd_today:
            agg["first_depositors"] += 1

        # Revenue: include only rows with activity on report_date.
        if is_new_signup or is_ftd_today:
            dep = r["deposits"]
            if dep > 0:
                agg["depositing_customers"] += 1
            agg["gross_revenue"] += r["gross_revenue"]
            agg["net_revenue"]   += r["net_revenue"]
            agg["deposits"]      += dep
            agg["bonuses"]       += r["bonuses"]
            agg["adj_general"]   += r["adj_general"]

        if len(ftd_samples) < 6:
            ftd_samples.append(repr(r["ftd_date"]))

    # Log summary so we can verify the file contents are what we expect
    log.info("First 6 ftd_date values seen: %s", ftd_samples)
    brand_geo_totals: dict = {}
    for (aff_id, brand, geo), agg in result.items():
        bg = (brand, geo)
        if bg not in brand_geo_totals:
            brand_geo_totals[bg] = {"regs": 0, "ftds": 0}
        brand_geo_totals[bg]["regs"] += agg["registrations"]
        brand_geo_totals[bg]["ftds"] += agg["first_depositors"]
    for (brand, geo), totals in sorted(brand_geo_totals.items()):
        log.info("  %-12s / %-20s → %4d regs, %3d FTDs", brand, geo, totals["regs"], totals["ftds"])

    return dict(result)


# ──────────────────────────────────────────────
# Merge affiliate stats + customer aggregation
# ──────────────────────────────────────────────

def _flt(v) -> float:
    """Safely convert an ETL decimal string (or number) to float."""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def merge(
    affiliate_rows: List[Dict],
    customer_agg:   Dict[Tuple, Dict],
    report_date:    date,
) -> List[Dict]:
    """
    Produce bahigo_wettigo_stats records by merging the two sources.

    Strategy
    --------
    Customer metrics  (registrations, FTDs, revenue, deposits)
        → exact figures from customer report aggregation per brand/geo.

    Traffic + commission  (clicks, views, total_reward …)
        → from affiliate stats, split proportionally by total_customers
           if the affiliate appears in multiple brand/geo groups.

    Affiliates with NO customer data for the day get brand='Unknown',
    geo='Unknown' and zero customer metrics (clicks still recorded).
    """
    # affiliate_id → list of (brand, geo) combinations from customer report
    aff_to_brandgeo: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for (aff_id, brand, geo) in customer_agg:
        aff_to_brandgeo[aff_id].append((brand, geo))

    # affiliate_id → affiliate stats row (after parse+transform)
    aff_stats: Dict[str, Dict] = {r["affiliate_id"]: r for r in affiliate_rows}

    records = []

    # ── Affiliates present in the affiliate stats ──────────────────────────
    for aff_id, aff_row in aff_stats.items():
        brand_geos = aff_to_brandgeo.get(aff_id, [])

        if not brand_geos:
            # No customer data → record clicks/FTDs/regs under Unknown brand/geo
            records.append({
                "report_date":          report_date,
                "brand_name":           "Unknown",
                "geo":                  "Unknown",
                "affiliate_id":         aff_id,
                "affiliate_name":       aff_row.get("affiliate_name", ""),
                "affiliate_email":      aff_row.get("affiliate_email", ""),
                "affiliate_status":     aff_row.get("affiliate_status", ""),
                "affiliate_signup_date":aff_row.get("affiliate_signup_date"),
                "campaign_name":        aff_row.get("campaign_name", ""),
                "reward_plan":          aff_row.get("reward_plan", ""),
                "views":                aff_row.get("views", 0),
                "unique_views":         aff_row.get("unique_views", 0),
                "clicks":               aff_row.get("clicks", 0),
                "unique_clicks":        aff_row.get("unique_clicks", 0),
                # FTDs/regs come from affiliate stats — no brand/geo to split on
                "registrations":        aff_row.get("registrations", 0),
                "depositing_customers": 0,
                "first_depositors":     aff_row.get("first_depositors", 0),
                "deposits":             0.0,
                "gross_revenue":        0.0,
                "bonuses":              0.0,
                "adj_general":          0.0,
                "net_revenue":          0.0,
                "rev_share_reward":     _flt(aff_row.get("rev_share_reward")),
                "cpa_reward":           _flt(aff_row.get("cpa_reward")),
                "total_reward":         _flt(aff_row.get("total_reward")),
            })
            continue

        total_customers = sum(
            customer_agg[(aff_id, b, g)]["total_customers"]
            for b, g in brand_geos
        )

        for brand, geo in brand_geos:
            agg    = customer_agg[(aff_id, brand, geo)]
            weight = (
                agg["total_customers"] / total_customers
                if total_customers > 0
                else 1.0 / len(brand_geos)
            )

            records.append({
                "report_date":          report_date,
                "brand_name":           brand,
                "geo":                  geo,
                "affiliate_id":         aff_id,
                "affiliate_name":       aff_row.get("affiliate_name", ""),
                "affiliate_email":      aff_row.get("affiliate_email", ""),
                "affiliate_status":     aff_row.get("affiliate_status", ""),
                "affiliate_signup_date":aff_row.get("affiliate_signup_date"),
                "campaign_name":        aff_row.get("campaign_name", ""),
                "reward_plan":          aff_row.get("reward_plan", ""),
                # Traffic: proportional split by brand/geo customer count
                "views":         round(aff_row.get("views",         0) * weight),
                "unique_views":  round(aff_row.get("unique_views",  0) * weight),
                "clicks":        round(aff_row.get("clicks",        0) * weight),
                "unique_clicks": round(aff_row.get("unique_clicks", 0) * weight),
                # FTDs/regs: exact from customer report
                # (cumulative totals per affiliate+brand+geo)
                "registrations":        agg["registrations"],
                "first_depositors":     agg["first_depositors"],
                "depositing_customers": agg["depositing_customers"],
                # Revenue: exact
                "deposits":      round(agg["deposits"],      4),
                "gross_revenue": round(agg["gross_revenue"], 4),
                "bonuses":       round(agg["bonuses"],       4),
                "adj_general":   round(agg["adj_general"],   4),
                "net_revenue":   round(agg["net_revenue"],   4),
                # Commission: proportional
                "rev_share_reward": round(_flt(aff_row.get("rev_share_reward")) * weight, 4),
                "cpa_reward":       round(_flt(aff_row.get("cpa_reward"))       * weight, 4),
                "total_reward":     round(_flt(aff_row.get("total_reward"))     * weight, 4),
            })

    # ── Customers whose affiliate is missing from affiliate stats ──────────
    for (aff_id, brand, geo), agg in customer_agg.items():
        if aff_id in aff_stats:
            continue   # already handled above
        log.warning(
            "Affiliate %s in customer report but not in affiliate stats — "
            "recording with zero traffic/commission",
            aff_id,
        )
        records.append({
            "report_date":          report_date,
            "brand_name":           brand,
            "geo":                  geo,
            "affiliate_id":         aff_id,
            "affiliate_name":       "",
            "affiliate_email":      "",
            "affiliate_status":     "",
            "affiliate_signup_date":None,
            "campaign_name":        "",
            "reward_plan":          "",
            "views": 0, "unique_views": 0, "clicks": 0, "unique_clicks": 0,
            "registrations":        agg["registrations"],
            "depositing_customers": agg["depositing_customers"],
            "first_depositors":     agg["first_depositors"],
            "deposits":      round(agg["deposits"],      4),
            "gross_revenue": round(agg["gross_revenue"], 4),
            "bonuses":       round(agg["bonuses"],       4),
            "adj_general":   round(agg["adj_general"],   4),
            "net_revenue":   round(agg["net_revenue"],   4),
            "rev_share_reward": 0.0,
            "cpa_reward":       0.0,
            "total_reward":     0.0,
        })

    log.info("Merge produced %d output records", len(records))
    return records


# ──────────────────────────────────────────────
# MySQL loader
# ──────────────────────────────────────────────

UPSERT_SQL = """
INSERT INTO bahigo_wettigo_stats (
    report_date, brand_name, geo,
    affiliate_id, affiliate_name, affiliate_email,
    affiliate_status, affiliate_signup_date,
    campaign_name, reward_plan,
    views, unique_views, clicks, unique_clicks,
    registrations, depositing_customers, first_depositors,
    deposits, gross_revenue, bonuses, adj_general, net_revenue,
    rev_share_reward, cpa_reward, total_reward
) VALUES (
    %(report_date)s, %(brand_name)s, %(geo)s,
    %(affiliate_id)s, %(affiliate_name)s, %(affiliate_email)s,
    %(affiliate_status)s, %(affiliate_signup_date)s,
    %(campaign_name)s, %(reward_plan)s,
    %(views)s, %(unique_views)s, %(clicks)s, %(unique_clicks)s,
    %(registrations)s, %(depositing_customers)s, %(first_depositors)s,
    %(deposits)s, %(gross_revenue)s, %(bonuses)s, %(adj_general)s, %(net_revenue)s,
    %(rev_share_reward)s, %(cpa_reward)s, %(total_reward)s
)
ON DUPLICATE KEY UPDATE
    affiliate_name        = VALUES(affiliate_name),
    affiliate_email       = VALUES(affiliate_email),
    affiliate_status      = VALUES(affiliate_status),
    campaign_name         = VALUES(campaign_name),
    reward_plan           = VALUES(reward_plan),
    views                 = VALUES(views),
    unique_views          = VALUES(unique_views),
    clicks                = VALUES(clicks),
    unique_clicks         = VALUES(unique_clicks),
    registrations         = VALUES(registrations),
    depositing_customers  = VALUES(depositing_customers),
    first_depositors      = VALUES(first_depositors),
    deposits              = VALUES(deposits),
    gross_revenue         = VALUES(gross_revenue),
    bonuses               = VALUES(bonuses),
    adj_general           = VALUES(adj_general),
    net_revenue           = VALUES(net_revenue),
    rev_share_reward      = VALUES(rev_share_reward),
    cpa_reward            = VALUES(cpa_reward),
    total_reward          = VALUES(total_reward),
    updated_at            = CURRENT_TIMESTAMP
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
        error_message = %s
    WHERE run_id = %s
"""


def load_to_mysql(records: List[Dict], source: str, batch_size: int = 500) -> int:
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

        cursor.execute(AUDIT_FINISH, (total, len(records), "success", None, run_id))
        conn.commit()

    except Exception as exc:
        conn.rollback()
        try:
            cursor.execute(AUDIT_FINISH, (total, len(records), "failed", str(exc), run_id))
            conn.commit()
        except Exception:
            pass
        raise
    finally:
        cursor.close()
        conn.close()

    return total


# ──────────────────────────────────────────────
# High-level process_pair
# ──────────────────────────────────────────────

def process_pair(
    stats_path:    Path,
    customer_path: Path,
    report_date:   date,
) -> int:
    """Parse both files, merge, and upsert into bahigo_wettigo_stats."""
    log.info(
        "Processing pair for %s: stats=%s  customers=%s",
        report_date, stats_path.name, customer_path.name,
    )

    # ── Parse affiliate stats (reuse Youwin parser) ────────────────────────
    col_map      = load_column_map()
    raw_stats    = _read_file(stats_path)
    parsed_stats = parse_affiliate_stats_csv(raw_stats, col_map, report_date)
    pf           = _ParseFailures()
    aff_rows     = transform_affiliate_stats(parsed_stats, pf)
    if pf.summary():
        log.warning("[DQ affiliate stats] %s", pf.summary())
    log.info("Affiliate stats: %d rows parsed", len(aff_rows))

    # ── Parse customer report ──────────────────────────────────────────────
    cust_col_map   = load_customer_column_map()
    raw_customers  = _read_file(customer_path)
    cust_rows      = parse_customer_report(raw_customers, cust_col_map)
    customer_agg   = aggregate_customers(cust_rows, report_date)
    log.info(
        "Customer report: %d unique (affiliate, brand, geo) groups",
        len(customer_agg),
    )

    # ── Merge ──────────────────────────────────────────────────────────────
    records = merge(aff_rows, customer_agg, report_date)

    # ── Summary by brand/geo ───────────────────────────────────────────────
    from collections import Counter
    brand_geo_counts = Counter(
        (r["brand_name"], r["geo"]) for r in records
        if r["brand_name"] != "Unknown"
    )
    for (brand, geo), count in sorted(brand_geo_counts.items()):
        total_ftds = sum(
            r["first_depositors"] for r in records
            if r["brand_name"] == brand and r["geo"] == geo
        )
        log.info("  %s / %-12s  → %d affiliates, %d FTDs", brand, geo, count, total_ftds)

    # ── Load ───────────────────────────────────────────────────────────────
    source = f"bw:{customer_path.name}"
    upserted = load_to_mysql(records, source=source)
    log.info("Done: %d rows upserted", upserted)
    return upserted


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Bahigo & Wettigo dual-brand ETL — merges affiliate stats + customer report"
    )
    p.add_argument("--stats",     required=True,
                   help="Path to netrefer_YYYY-MM-DD.csv (affiliate stats)")
    p.add_argument("--customers", required=True,
                   help="Path to netrefer_custom_YYYY-MM-DD.csv (customer report)")
    p.add_argument("--date",      required=False,
                   help="Report date YYYY-MM-DD (auto-detected from customer filename if omitted)")
    args = p.parse_args()

    stats_path    = Path(args.stats)
    customer_path = Path(args.customers)

    report_date = None
    if args.date:
        report_date = date.fromisoformat(args.date)
    else:
        report_date = date_from_filename(customer_path) or date_from_filename(stats_path)

    if not report_date:
        p.error(
            "--date YYYY-MM-DD is required when the date cannot be extracted "
            "from the filename (expected netrefer_custom_YYYY-MM-DD.csv)"
        )

    process_pair(stats_path, customer_path, report_date)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.error("Fatal: %s", e, exc_info=True)
        sys.exit(1)
