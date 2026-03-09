#!/usr/bin/env python3
"""
setup_affiliate_dashboard.py  –  Creates a filterable Affiliate Deep Dive dashboard.

Filters:
  • Affiliate Name  (text search / dropdown)
  • Date Range      (start date → end date)

Usage:
    python scripts/setup_affiliate_dashboard.py \
        --host http://localhost:3000 \
        --user admin@example.com \
        --password yourpassword
"""

import argparse
import sys
import time
import uuid
import requests

# ──────────────────────────────────────────────
# Metabase client
# ──────────────────────────────────────────────

class MetabaseClient:
    def __init__(self, host, email, password):
        self.host = host.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        r = self.session.post(f"{self.host}/api/session",
                              json={"username": email, "password": password})
        r.raise_for_status()
        self.session.headers["X-Metabase-Session"] = r.json()["id"]

    def get(self, path, **kw):
        r = self.session.get(f"{self.host}{path}", **kw)
        r.raise_for_status()
        return r.json()

    def post(self, path, **kw):
        r = self.session.post(f"{self.host}{path}", **kw)
        r.raise_for_status()
        return r.json()

    def put(self, path, **kw):
        r = self.session.put(f"{self.host}{path}", **kw)
        r.raise_for_status()
        return r.json()


def find_database(mb, name_fragment):
    dbs = mb.get("/api/database")
    items = dbs if isinstance(dbs, list) else dbs.get("data", [])
    for db in items:
        if name_fragment.lower() in db["name"].lower():
            return db["id"]
    raise RuntimeError(f"No database matching '{name_fragment}'")


def existing_cards(mb):
    return {c["name"]: c["id"] for c in mb.get("/api/card")}


def existing_dashboards(mb):
    return {d["name"]: d["id"] for d in mb.get("/api/dashboard")}


# ──────────────────────────────────────────────
# Parameter IDs (fixed so re-runs are stable)
# ──────────────────────────────────────────────
PARAM_AFFILIATE = "a1b2c3d4-0001-0001-0001-000000000001"
PARAM_START     = "a1b2c3d4-0002-0002-0002-000000000002"
PARAM_END       = "a1b2c3d4-0003-0003-0003-000000000003"


def template_tags():
    return {
        "affiliate_name": {
            "id":           "tt-affiliate",
            "name":         "affiliate_name",
            "display-name": "Affiliate Name",
            "type":         "text",
            "required":     False,
        },
        "start_date": {
            "id":           "tt-start",
            "name":         "start_date",
            "display-name": "Start Date",
            "type":         "date",
            "required":     False,
        },
        "end_date": {
            "id":           "tt-end",
            "name":         "end_date",
            "display-name": "End Date",
            "type":         "date",
            "required":     False,
        },
    }


def param_mappings(card_id):
    """Standard parameter→template-tag mappings for every card."""
    return [
        {
            "parameter_id": PARAM_AFFILIATE,
            "card_id":      card_id,
            "target":       ["variable", ["template-tag", "affiliate_name"]],
        },
        {
            "parameter_id": PARAM_START,
            "card_id":      card_id,
            "target":       ["variable", ["template-tag", "start_date"]],
        },
        {
            "parameter_id": PARAM_END,
            "card_id":      card_id,
            "target":       ["variable", ["template-tag", "end_date"]],
        },
    ]


# ──────────────────────────────────────────────
# Card definitions
# ──────────────────────────────────────────────

def native(db_id, sql):
    return {
        "type":     "native",
        "database": db_id,
        "native":   {"query": sql, "template-tags": template_tags()},
    }


WHERE = """
    WHERE 1=1
    [[AND affiliate_name = {{affiliate_name}}]]
    [[AND report_date >= {{start_date}}]]
    [[AND report_date <= {{end_date}}]]
"""


def card_defs(db_id):
    return [
        # ── KPI scalars ──────────────────────────────────────────────────
        {
            "name":    "AF – Clicks",
            "display": "scalar",
            "dataset_query": native(db_id, f"""
                SELECT SUM(clicks) AS clicks
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },
        {
            "name":    "AF – Registrations",
            "display": "scalar",
            "dataset_query": native(db_id, f"""
                SELECT SUM(registrations) AS registrations
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },
        {
            "name":    "AF – FTDs",
            "display": "scalar",
            "dataset_query": native(db_id, f"""
                SELECT SUM(first_depositors) AS ftds
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },
        {
            "name":    "AF – Net Revenue",
            "display": "scalar",
            "dataset_query": native(db_id, f"""
                SELECT ROUND(SUM(net_revenue), 2) AS net_revenue
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {"number.style": "currency", "currency": "EUR"},
        },
        {
            "name":    "AF – Total Commission",
            "display": "scalar",
            "dataset_query": native(db_id, f"""
                SELECT ROUND(SUM(total_reward), 2) AS commission
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {"number.style": "currency", "currency": "EUR"},
        },
        {
            "name":    "AF – Deposits",
            "display": "scalar",
            "dataset_query": native(db_id, f"""
                SELECT ROUND(SUM(deposits), 2) AS deposits
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {"number.style": "currency", "currency": "EUR"},
        },
        # ── Daily trend ───────────────────────────────────────────────────
        {
            "name":    "AF – Daily Revenue Trend",
            "display": "line",
            "dataset_query": native(db_id, f"""
                SELECT
                    report_date,
                    ROUND(SUM(net_revenue), 2) AS net_revenue,
                    ROUND(SUM(deposits),    2) AS deposits
                FROM netrefer_stats {WHERE}
                GROUP BY report_date
                ORDER BY report_date
            """),
            "visualization_settings": {
                "graph.dimensions": ["report_date"],
                "graph.metrics":    ["net_revenue", "deposits"],
            },
        },
        {
            "name":    "AF – Daily Conversions",
            "display": "line",
            "dataset_query": native(db_id, f"""
                SELECT
                    report_date,
                    SUM(clicks)          AS clicks,
                    SUM(registrations)   AS registrations,
                    SUM(first_depositors) AS ftds
                FROM netrefer_stats {WHERE}
                GROUP BY report_date
                ORDER BY report_date
            """),
            "visualization_settings": {
                "graph.dimensions": ["report_date"],
                "graph.metrics":    ["clicks", "registrations", "ftds"],
            },
        },
        # ── Conversion funnel ─────────────────────────────────────────────
        {
            "name":    "AF – Conversion Funnel",
            "display": "bar",
            "dataset_query": native(db_id, f"""
                SELECT 'Clicks'        AS stage, SUM(clicks)           AS total FROM netrefer_stats {WHERE}
                UNION ALL
                SELECT 'Registrations',          SUM(registrations)            FROM netrefer_stats {WHERE}
                UNION ALL
                SELECT 'FTDs',                   SUM(first_depositors)         FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {
                "graph.dimensions": ["stage"],
                "graph.metrics":    ["total"],
            },
        },
        # ── Campaign breakdown ────────────────────────────────────────────
        {
            "name":    "AF – Revenue by Campaign",
            "display": "bar",
            "dataset_query": native(db_id, f"""
                SELECT
                    campaign_name,
                    ROUND(SUM(net_revenue), 2) AS net_revenue,
                    SUM(first_depositors)       AS ftds
                FROM netrefer_stats {WHERE}
                  AND campaign_name != ''
                GROUP BY campaign_name
                ORDER BY net_revenue DESC
            """),
            "visualization_settings": {
                "graph.dimensions": ["campaign_name"],
                "graph.metrics":    ["net_revenue"],
            },
        },
        # ── Country breakdown ─────────────────────────────────────────────
        {
            "name":    "AF – Revenue by Country",
            "display": "pie",
            "dataset_query": native(db_id, f"""
                SELECT
                    country,
                    ROUND(SUM(net_revenue), 2) AS net_revenue
                FROM netrefer_stats {WHERE}
                  AND country != ''
                GROUP BY country
                ORDER BY net_revenue DESC
            """),
            "visualization_settings": {
                "pie.dimension": "country",
                "pie.metric":    "net_revenue",
            },
        },
        # ── Daily detail table ────────────────────────────────────────────
        {
            "name":    "AF – Daily Detail Table",
            "display": "table",
            "dataset_query": native(db_id, f"""
                SELECT
                    report_date,
                    campaign_name,
                    country,
                    clicks,
                    registrations                               AS signups,
                    first_depositors                            AS ftds,
                    ROUND(deposits,     2)                      AS deposits,
                    ROUND(net_revenue,  2)                      AS net_revenue,
                    ROUND(total_reward, 2)                      AS commission,
                    CASE WHEN clicks > 0
                         THEN ROUND(registrations/clicks*100, 2) ELSE 0
                    END                                         AS click_to_reg_pct,
                    CASE WHEN registrations > 0
                         THEN ROUND(first_depositors/registrations*100, 2) ELSE 0
                    END                                         AS reg_to_ftd_pct
                FROM netrefer_stats {WHERE}
                ORDER BY report_date DESC, net_revenue DESC
            """),
            "visualization_settings": {},
        },
    ]


# ──────────────────────────────────────────────
# Dashboard layout
# ──────────────────────────────────────────────

LAYOUT = [
    # name,                       row, col, size_x, size_y
    ("AF – Clicks",                  0,  0,  4, 3),
    ("AF – Registrations",           0,  4,  4, 3),
    ("AF – FTDs",                    0,  8,  4, 3),
    ("AF – Net Revenue",             0, 12,  4, 3),
    ("AF – Deposits",                0, 16,  4, 3),
    ("AF – Total Commission",        0, 20,  4, 3),
    ("AF – Daily Revenue Trend",     3,  0, 12, 7),
    ("AF – Daily Conversions",       3, 12, 12, 7),
    ("AF – Conversion Funnel",      10,  0,  8, 7),
    ("AF – Revenue by Campaign",    10,  8,  8, 7),
    ("AF – Revenue by Country",     10, 16,  8, 7),
    ("AF – Daily Detail Table",     17,  0, 24, 8),
]


def build_dashcards(card_name_to_id):
    dashcards = []
    for idx, (name, row, col, size_x, size_y) in enumerate(LAYOUT):
        card_id = card_name_to_id.get(name)
        if card_id is None:
            print(f"  [warn] card '{name}' not found, skipping")
            continue
        dashcards.append({
            "id":                      -(idx + 1),
            "card_id":                  card_id,
            "row":                      row,
            "col":                      col,
            "size_x":                   size_x,
            "size_y":                   size_y,
            "parameter_mappings":       param_mappings(card_id),
            "visualization_settings":   {},
        })
    return dashcards


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host",     default="http://localhost:3000")
    parser.add_argument("--user",     required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--db-name",  default="netrefer")
    args = parser.parse_args()

    print(f"Connecting to {args.host} …")
    mb = MetabaseClient(args.host, args.user, args.password)
    print("  Logged in.")

    db_id = find_database(mb, args.db_name)
    print(f"  Database id={db_id}")

    # Create cards
    existing = existing_cards(mb)
    card_name_to_id = {}
    print(f"\nCreating cards …")
    for card in card_defs(db_id):
        name = card["name"]
        if name in existing:
            print(f"  [skip] {name}")
            card_name_to_id[name] = existing[name]
            continue
        result = mb.post("/api/card", json={
            "name":                   name,
            "display":                card["display"],
            "dataset_query":          card["dataset_query"],
            "visualization_settings": card.get("visualization_settings", {}),
        })
        card_name_to_id[name] = result["id"]
        print(f"  [created] {name} (id={result['id']})")

    # Create dashboard
    dash_name = "Affiliate Deep Dive"
    existing_dashes = existing_dashboards(mb)

    dashboard_params = [
        {
            "id":      PARAM_AFFILIATE,
            "name":    "Affiliate Name",
            "slug":    "affiliate_name",
            "type":    "category",
        },
        {
            "id":      PARAM_START,
            "name":    "Start Date",
            "slug":    "start_date",
            "type":    "date/single",
        },
        {
            "id":      PARAM_END,
            "name":    "End Date",
            "slug":    "end_date",
            "type":    "date/single",
        },
    ]

    if dash_name in existing_dashes:
        dash_id = existing_dashes[dash_name]
        print(f"\n[skip] Dashboard '{dash_name}' already exists (id={dash_id})")
    else:
        dash = mb.post("/api/dashboard", json={
            "name":        dash_name,
            "description": "Per-affiliate KPIs, trends and breakdown — use filters to drill down",
            "parameters":  dashboard_params,
        })
        dash_id = dash["id"]
        print(f"\n[created] Dashboard '{dash_name}' (id={dash_id})")

    # Add cards
    dashcards = build_dashcards(card_name_to_id)
    print("  Adding cards …")
    mb.put(f"/api/dashboard/{dash_id}", json={
        "parameters": dashboard_params,
        "dashcards":  dashcards,
    })
    print(f"  Added {len(dashcards)} cards.")
    print(f"\nDone!  Open: {args.host}/dashboard/{dash_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
