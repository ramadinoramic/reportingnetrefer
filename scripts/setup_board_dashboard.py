#!/usr/bin/env python3
"""
setup_board_dashboard.py  –  Creates the "Board Report" Metabase dashboard.

Designed for C-Level / Board consumption:
  • Monthly financial KPIs (NGR, Gross Revenue, Deposits, Commission)
  • FTDs and active affiliates count
  • Monthly trend charts (revenue + FTDs)
  • Conversion funnel
  • Net Revenue by Country (pie)
  • Commission split: Rev Share vs CPA
  • Top 10 Affiliates by Net Revenue (table)

Dashboard has a From / To date filter so the board can compare any period.

Usage:
    python scripts/setup_board_dashboard.py \
        --host http://localhost:3000 \
        --user admin@example.com \
        --password yourpassword

Re-running is safe: existing cards are updated (not duplicated),
the dashboard is reused.

Makefile shortcut:
    make board-dashboard USER=admin@example.com PASSWORD=secret
"""

import argparse
import sys
import requests

# ──────────────────────────────────────────────────────────────────────────────
# Metabase client
# ──────────────────────────────────────────────────────────────────────────────

class MetabaseClient:
    def __init__(self, host: str, email: str, password: str):
        self.host = host.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        r = self.session.post(
            f"{self.host}/api/session",
            json={"username": email, "password": password},
        )
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


# ──────────────────────────────────────────────────────────────────────────────
# DB / table helpers  (identical pattern to the other setup scripts)
# ──────────────────────────────────────────────────────────────────────────────

def find_database(mb: MetabaseClient, name_fragment: str) -> int:
    dbs = mb.get("/api/database")
    items = dbs if isinstance(dbs, list) else dbs.get("data", [])
    for db in items:
        if name_fragment.lower() in db["name"].lower():
            return db["id"]
    raise RuntimeError(
        f"No Metabase database matching '{name_fragment}'. "
        "Add your MySQL connection in Metabase first (Admin → Databases)."
    )


def existing_cards(mb: MetabaseClient) -> dict:
    return {c["name"]: c["id"] for c in mb.get("/api/card")}


def existing_dashboards(mb: MetabaseClient) -> dict:
    return {d["name"]: d["id"] for d in mb.get("/api/dashboard")}


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard filter parameters (fixed UUIDs so re-runs are stable)
# ──────────────────────────────────────────────────────────────────────────────

PARAM_START = "bd000001-0001-0001-0001-000000000001"
PARAM_END   = "bd000002-0002-0002-0002-000000000002"

DASHBOARD_PARAMS = [
    {
        "id":      PARAM_START,
        "name":    "From Date",
        "slug":    "start_date",
        "type":    "date/single",
    },
    {
        "id":      PARAM_END,
        "name":    "To Date",
        "slug":    "end_date",
        "type":    "date/single",
    },
]

# Template-tag block injected into every filterable native query
TAGS = {
    "start_date": {
        "id":           "tt-bd-start",
        "name":         "start_date",
        "display-name": "From Date",
        "type":         "date",
        "required":     False,
    },
    "end_date": {
        "id":           "tt-bd-end",
        "name":         "end_date",
        "display-name": "To Date",
        "type":         "date",
        "required":     False,
    },
}

# Optional date-range clause — dropped automatically when no filter is set
WHERE = """
    WHERE 1=1
    [[AND report_date >= {{start_date}}]]
    [[AND report_date <= {{end_date}}]]
"""

# Parameter→tag mappings applied to each filterable dashcard
def param_mappings(card_id: int) -> list:
    return [
        {"parameter_id": PARAM_START, "card_id": card_id,
         "target": ["variable", ["template-tag", "start_date"]]},
        {"parameter_id": PARAM_END,   "card_id": card_id,
         "target": ["variable", ["template-tag", "end_date"]]},
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Card definitions
# ──────────────────────────────────────────────────────────────────────────────

def native_filtered(db_id: int, sql: str) -> dict:
    """Native query with the two date template-tags wired up."""
    return {
        "type":     "native",
        "database": db_id,
        "native":   {"query": sql, "template-tags": TAGS},
    }


def native_fixed(db_id: int, sql: str) -> dict:
    """Native query with no template-tags (ignores dashboard filters)."""
    return {
        "type":     "native",
        "database": db_id,
        "native":   {"query": sql, "template-tags": {}},
    }


def card_definitions(db_id: int) -> list:
    """
    Returns list of card specs.
    'no_params': True  →  card is not wired to the date filters.
    """
    def f(sql):   return native_filtered(db_id, sql)
    def fx(sql):  return native_fixed(db_id, sql)

    EUR = {"number.style": "currency", "currency": "EUR", "currency_in_header": True}

    return [
        # ── KPI scalars ────────────────────────────────────────────────────
        {
            "name": "Board – Net Gaming Revenue",
            "display": "scalar",
            "dataset_query": f(f"""
                SELECT ROUND(SUM(net_revenue), 2) AS `Net Gaming Revenue`
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": EUR,
        },
        {
            "name": "Board – Gross Revenue",
            "display": "scalar",
            "dataset_query": f(f"""
                SELECT ROUND(SUM(gross_revenue), 2) AS `Gross Revenue`
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": EUR,
        },
        {
            "name": "Board – Total Deposits",
            "display": "scalar",
            "dataset_query": f(f"""
                SELECT ROUND(SUM(deposits), 2) AS `Total Deposits`
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": EUR,
        },
        {
            "name": "Board – Total Commission Paid",
            "display": "scalar",
            "dataset_query": f(f"""
                SELECT ROUND(SUM(total_reward), 2) AS `Total Commission`
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": EUR,
        },
        {
            "name": "Board – First-Time Depositors",
            "display": "scalar",
            "dataset_query": f(f"""
                SELECT SUM(first_depositors) AS `FTDs`
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },
        {
            "name": "Board – Active Affiliates",
            "display": "scalar",
            "dataset_query": f(f"""
                SELECT COUNT(DISTINCT affiliate_id) AS `Active Affiliates`
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },

        # ── Monthly revenue + FTD trend ─────────────────────────────────────
        {
            "name": "Board – Monthly Revenue Trend",
            "display": "bar",
            "dataset_query": f(f"""
                SELECT
                    DATE_FORMAT(report_date, '%Y-%m') AS month,
                    ROUND(SUM(net_revenue),   2)      AS net_revenue,
                    ROUND(SUM(gross_revenue), 2)      AS gross_revenue,
                    ROUND(SUM(deposits),      2)      AS deposits
                FROM netrefer_stats {WHERE}
                GROUP BY DATE_FORMAT(report_date, '%Y-%m')
                ORDER BY month
            """),
            "visualization_settings": {
                "graph.dimensions": ["month"],
                "graph.metrics":    ["net_revenue", "gross_revenue", "deposits"],
                "stackable.stack_type": None,
            },
        },
        {
            "name": "Board – Monthly FTD Trend",
            "display": "line",
            "dataset_query": f(f"""
                SELECT
                    DATE_FORMAT(report_date, '%Y-%m') AS month,
                    SUM(first_depositors)             AS ftds,
                    SUM(registrations)                AS signups
                FROM netrefer_stats {WHERE}
                GROUP BY DATE_FORMAT(report_date, '%Y-%m')
                ORDER BY month
            """),
            "visualization_settings": {
                "graph.dimensions": ["month"],
                "graph.metrics":    ["ftds", "signups"],
            },
        },

        # ── Conversion funnel ───────────────────────────────────────────────
        {
            "name": "Board – Conversion Funnel",
            "display": "bar",
            "dataset_query": f(f"""
                SELECT 'Clicks'         AS stage, SUM(clicks)           AS volume FROM netrefer_stats {WHERE}
                UNION ALL
                SELECT 'Registrations',            SUM(registrations)            FROM netrefer_stats {WHERE}
                UNION ALL
                SELECT 'FTDs',                     SUM(first_depositors)         FROM netrefer_stats {WHERE}
                UNION ALL
                SELECT 'Depositing Customers',     SUM(depositing_customers)     FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {
                "graph.dimensions": ["stage"],
                "graph.metrics":    ["volume"],
            },
        },

        # ── Revenue by country ──────────────────────────────────────────────
        {
            "name": "Board – Net Revenue by Country",
            "display": "pie",
            "dataset_query": f(f"""
                SELECT
                    COALESCE(NULLIF(country, ''), 'Unknown') AS country,
                    ROUND(SUM(net_revenue), 2)               AS net_revenue
                FROM netrefer_stats {WHERE}
                GROUP BY country
                ORDER BY net_revenue DESC
                LIMIT 10
            """),
            "visualization_settings": {
                "pie.dimension": "country",
                "pie.metric":    "net_revenue",
            },
        },

        # ── Commission breakdown ────────────────────────────────────────────
        {
            "name": "Board – Commission Breakdown",
            "display": "pie",
            "dataset_query": f(f"""
                SELECT 'Revenue Share' AS type, ROUND(SUM(rev_share_reward), 2) AS amount FROM netrefer_stats {WHERE}
                UNION ALL
                SELECT 'CPA',                   ROUND(SUM(cpa_reward),       2)           FROM netrefer_stats {WHERE}
                UNION ALL
                SELECT 'Sub-Affiliate',         ROUND(SUM(sub_affiliate_reward), 2)       FROM netrefer_stats {WHERE}
                UNION ALL
                SELECT 'Other',                 ROUND(SUM(other_rewards),    2)           FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {
                "pie.dimension": "type",
                "pie.metric":    "amount",
            },
        },

        # ── Top 10 affiliates table ─────────────────────────────────────────
        {
            "name": "Board – Top 10 Affiliates by Net Revenue",
            "display": "table",
            "dataset_query": f(f"""
                SELECT
                    affiliate_name                                          AS Affiliate,
                    SUM(first_depositors)                                   AS FTDs,
                    ROUND(SUM(deposits),      2)                            AS Deposits,
                    ROUND(SUM(net_revenue),   2)                            AS `Net Revenue`,
                    ROUND(SUM(total_reward),  2)                            AS Commission,
                    ROUND(
                        100.0 * SUM(first_depositors)
                              / NULLIF(SUM(registrations), 0), 1
                    )                                                       AS `Reg → FTD %`,
                    ROUND(
                        100.0 * SUM(net_revenue)
                              / NULLIF(SUM(total_reward), 0), 1
                    )                                                       AS `NGR / Commission`
                FROM netrefer_stats {WHERE}
                GROUP BY affiliate_id, affiliate_name
                ORDER BY `Net Revenue` DESC
                LIMIT 10
            """),
            "visualization_settings": {
                "column_settings": {
                    '["name","Net Revenue"]':  {"number_style": "currency", "currency": "EUR"},
                    '["name","Deposits"]':     {"number_style": "currency", "currency": "EUR"},
                    '["name","Commission"]':   {"number_style": "currency", "currency": "EUR"},
                    '["name","Reg → FTD %"]':  {"number_style": "percent", "scale": 0.01},
                },
            },
        },
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard layout   (24-column grid)
# ──────────────────────────────────────────────────────────────────────────────

LAYOUT = [
    # name                                         row  col  w   h
    ("Board – Net Gaming Revenue",                   0,  0,  6,  3),
    ("Board – Gross Revenue",                        0,  6,  6,  3),
    ("Board – Total Deposits",                       0, 12,  6,  3),
    ("Board – Total Commission Paid",                0, 18,  6,  3),
    ("Board – First-Time Depositors",                3,  0,  6,  3),
    ("Board – Active Affiliates",                    3,  6,  6,  3),
    ("Board – Monthly Revenue Trend",                6,  0, 14,  8),
    ("Board – Monthly FTD Trend",                    6, 14, 10,  8),
    ("Board – Conversion Funnel",                   14,  0,  8,  7),
    ("Board – Net Revenue by Country",              14,  8,  8,  7),
    ("Board – Commission Breakdown",                14, 16,  8,  7),
    ("Board – Top 10 Affiliates by Net Revenue",    21,  0, 24,  8),
]


def build_dashcards(card_name_to_id: dict) -> list:
    dashcards = []
    for idx, (name, row, col, size_x, size_y) in enumerate(LAYOUT):
        card_id = card_name_to_id.get(name)
        if card_id is None:
            print(f"  [warn] card '{name}' not found – skipping")
            continue
        dashcards.append({
            "id":                    -(idx + 1),
            "card_id":                card_id,
            "row":                    row,
            "col":                    col,
            "size_x":                 size_x,
            "size_y":                 size_y,
            "parameter_mappings":     param_mappings(card_id),
            "visualization_settings": {},
        })
    return dashcards


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Create Board Report dashboard in Metabase")
    parser.add_argument("--host",     default="http://localhost:3000")
    parser.add_argument("--user",     required=True, help="Metabase admin email")
    parser.add_argument("--password", required=True, help="Metabase admin password")
    parser.add_argument("--db-name",  default="netrefer", help="Fragment of DB name in Metabase")
    args = parser.parse_args()

    print(f"Connecting to Metabase at {args.host} …")
    mb = MetabaseClient(args.host, args.user, args.password)
    print("  Logged in.")

    db_id = find_database(mb, args.db_name)
    print(f"  Found database id={db_id}")

    # ── Upsert cards ──────────────────────────────────────────────────────────
    existing = existing_cards(mb)
    card_name_to_id = {}

    cards = card_definitions(db_id)
    print(f"\nUpserting {len(cards)} cards …")
    for card in cards:
        name = card["name"]
        payload = {
            "name":                   name,
            "display":                card["display"],
            "dataset_query":          card["dataset_query"],
            "visualization_settings": card.get("visualization_settings", {}),
        }
        if name in existing:
            card_id = existing[name]
            mb.put(f"/api/card/{card_id}", json=payload)
            card_name_to_id[name] = card_id
            print(f"  [updated] {name} (id={card_id})")
        else:
            result = mb.post("/api/card", json=payload)
            card_name_to_id[name] = result["id"]
            print(f"  [created] {name} (id={result['id']})")

    # ── Create or reuse dashboard ─────────────────────────────────────────────
    dash_name = "Board Report"
    existing_dashes = existing_dashboards(mb)

    if dash_name in existing_dashes:
        dash_id = existing_dashes[dash_name]
        print(f"\n[existing] Dashboard '{dash_name}' (id={dash_id})")
    else:
        dash = mb.post("/api/dashboard", json={
            "name":        dash_name,
            "description": "C-Level & Board: financial KPIs, revenue trends, top affiliates and geo breakdown",
            "parameters":  DASHBOARD_PARAMS,
        })
        dash_id = dash["id"]
        print(f"\n[created] Dashboard '{dash_name}' (id={dash_id})")

    # ── Wire cards + filters into the dashboard ───────────────────────────────
    dashcards = build_dashcards(card_name_to_id)
    print(f"  Updating dashboard layout ({len(dashcards)} cards) …")
    mb.put(f"/api/dashboard/{dash_id}", json={
        "parameters": DASHBOARD_PARAMS,
        "dashcards":  dashcards,
    })

    print(f"\nDone!  Open your Board Report: {args.host}/dashboard/{dash_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
