#!/usr/bin/env python3
"""
setup_metabase.py  –  Creates Metabase cards + dashboard for Netrefer reporting.

Usage:
    python scripts/setup_metabase.py \
        --host http://localhost:3000 \
        --user admin@example.com \
        --password yourpassword

The script is idempotent: re-running it skips cards/dashboards that already exist.
"""

import argparse
import sys
import time
import requests

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

class MetabaseClient:
    def __init__(self, host: str, email: str, password: str):
        self.host = host.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        token = self._login(email, password)
        self.session.headers["X-Metabase-Session"] = token

    def _login(self, email: str, password: str) -> str:
        r = self.session.post(
            f"{self.host}/api/session",
            json={"username": email, "password": password},
        )
        r.raise_for_status()
        return r.json()["id"]

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


def find_database(mb: MetabaseClient, name_fragment: str) -> int:
    dbs = mb.get("/api/database")
    items = dbs if isinstance(dbs, list) else dbs.get("data", [])
    for db in items:
        if name_fragment.lower() in db["name"].lower():
            return db["id"]
    raise RuntimeError(
        f"No database matching '{name_fragment}'. "
        "Connect your MySQL DB in Metabase first."
    )


def sync_and_wait(mb: MetabaseClient, db_id: int, timeout: int = 60):
    """Trigger a metadata sync and wait until tables are visible."""
    mb.post(f"/api/database/{db_id}/sync_schema")
    deadline = time.time() + timeout
    while time.time() < deadline:
        tables = mb.get(f"/api/database/{db_id}/metadata").get("tables", [])
        if any(t["name"] == "netrefer_stats" for t in tables):
            return tables
        time.sleep(3)
    raise RuntimeError("Sync timed out – netrefer_stats table not found.")


def find_table_id(tables: list, name: str) -> int:
    for t in tables:
        if t["name"] == name:
            return t["id"]
    raise RuntimeError(f"Table '{name}' not found after sync.")


def existing_cards(mb: MetabaseClient) -> dict:
    """Return {name: id} for all existing questions."""
    cards = mb.get("/api/card")
    return {c["name"]: c["id"] for c in cards}


def existing_dashboards(mb: MetabaseClient) -> dict:
    """Return {name: id} for all existing dashboards."""
    dbs = mb.get("/api/dashboard")
    return {d["name"]: d["id"] for d in dbs}


# ──────────────────────────────────────────────
# Card definitions
# ──────────────────────────────────────────────

def card_definitions(db_id: int, stats_table_id: int) -> list:
    """
    Returns a list of card spec dicts.
    Each has: name, description, display, dataset_query, visualization_settings
    """
    base_query = {
        "type": "query",
        "database": db_id,
        "query": {"source-table": stats_table_id},
    }

    def native(sql: str) -> dict:
        return {
            "type": "native",
            "database": db_id,
            "native": {"query": sql},
        }

    return [
        # ── 1. Total Clicks (last 30 days) ──────────────────────────────
        {
            "name": "Total Clicks – Last 30 Days",
            "description": "Sum of clicks over the past 30 days",
            "display": "scalar",
            "dataset_query": native("""
                SELECT SUM(clicks) AS total_clicks
                FROM netrefer_stats
                WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            """),
            "visualization_settings": {},
        },
        # ── 2. Total Registrations – Last 30 Days ───────────────────────
        {
            "name": "Total Registrations – Last 30 Days",
            "description": "Sum of registrations over the past 30 days",
            "display": "scalar",
            "dataset_query": native("""
                SELECT SUM(registrations) AS total_registrations
                FROM netrefer_stats
                WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            """),
            "visualization_settings": {},
        },
        # ── 3. Total FTDs – Last 30 Days ────────────────────────────────
        {
            "name": "Total FTDs – Last 30 Days",
            "description": "First-time depositors over the past 30 days",
            "display": "scalar",
            "dataset_query": native("""
                SELECT SUM(first_depositors) AS total_ftds
                FROM netrefer_stats
                WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            """),
            "visualization_settings": {},
        },
        # ── 4. Net Revenue – Last 30 Days ───────────────────────────────
        {
            "name": "Net Revenue – Last 30 Days",
            "description": "Total net gaming revenue over the past 30 days",
            "display": "scalar",
            "dataset_query": native("""
                SELECT ROUND(SUM(net_revenue), 2) AS net_revenue
                FROM netrefer_stats
                WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            """),
            "visualization_settings": {"number.style": "currency", "currency": "EUR"},
        },
        # ── 5. Daily Clicks & Registrations (time series) ───────────────
        {
            "name": "Daily Clicks & Registrations",
            "description": "Trend of clicks and registrations by day",
            "display": "line",
            "dataset_query": native("""
                SELECT
                    report_date,
                    SUM(clicks)        AS clicks,
                    SUM(registrations) AS registrations
                FROM netrefer_stats
                WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
                GROUP BY report_date
                ORDER BY report_date
            """),
            "visualization_settings": {
                "graph.dimensions": ["report_date"],
                "graph.metrics": ["clicks", "registrations"],
            },
        },
        # ── 6. Daily Net Revenue (time series) ──────────────────────────
        {
            "name": "Daily Net Revenue",
            "description": "Net gaming revenue by day (90 days)",
            "display": "line",
            "dataset_query": native("""
                SELECT
                    report_date,
                    ROUND(SUM(net_revenue), 2) AS net_revenue
                FROM netrefer_stats
                WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
                GROUP BY report_date
                ORDER BY report_date
            """),
            "visualization_settings": {
                "graph.dimensions": ["report_date"],
                "graph.metrics": ["net_revenue"],
            },
        },
        # ── 7. Top 10 Affiliates by Net Revenue ─────────────────────────
        {
            "name": "Top 10 Affiliates by Net Revenue",
            "description": "Last 30 days",
            "display": "bar",
            "dataset_query": native("""
                SELECT
                    affiliate_name,
                    ROUND(SUM(net_revenue), 2)      AS net_revenue,
                    SUM(first_depositors)            AS ftds,
                    SUM(registrations)               AS signups
                FROM netrefer_stats
                WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                GROUP BY affiliate_id, affiliate_name
                ORDER BY net_revenue DESC
                LIMIT 10
            """),
            "visualization_settings": {
                "graph.dimensions": ["affiliate_name"],
                "graph.metrics": ["net_revenue"],
            },
        },
        # ── 8. Revenue by Country ────────────────────────────────────────
        {
            "name": "Net Revenue by Country",
            "description": "Last 30 days breakdown by country",
            "display": "pie",
            "dataset_query": native("""
                SELECT
                    country,
                    ROUND(SUM(net_revenue), 2) AS net_revenue
                FROM netrefer_stats
                WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                  AND country != ''
                GROUP BY country
                ORDER BY net_revenue DESC
            """),
            "visualization_settings": {
                "pie.dimension": "country",
                "pie.metric": "net_revenue",
            },
        },
        # ── 9. Conversion Funnel ─────────────────────────────────────────
        {
            "name": "Conversion Funnel",
            "description": "Clicks → Registrations → FTDs (last 30 days)",
            "display": "bar",
            "dataset_query": native("""
                SELECT 'Clicks'        AS stage, SUM(clicks)          AS total FROM netrefer_stats WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                UNION ALL
                SELECT 'Registrations' AS stage, SUM(registrations)   AS total FROM netrefer_stats WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                UNION ALL
                SELECT 'FTDs'          AS stage, SUM(first_depositors) AS total FROM netrefer_stats WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            """),
            "visualization_settings": {
                "graph.dimensions": ["stage"],
                "graph.metrics": ["total"],
            },
        },
        # ── 10. Revenue by Campaign ──────────────────────────────────────
        {
            "name": "Net Revenue by Campaign",
            "description": "Last 30 days breakdown by campaign",
            "display": "bar",
            "dataset_query": native("""
                SELECT
                    campaign_name,
                    ROUND(SUM(net_revenue), 2) AS net_revenue,
                    SUM(first_depositors)       AS ftds
                FROM netrefer_stats
                WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                  AND campaign_name != ''
                GROUP BY campaign_name
                ORDER BY net_revenue DESC
                LIMIT 15
            """),
            "visualization_settings": {
                "graph.dimensions": ["campaign_name"],
                "graph.metrics": ["net_revenue"],
            },
        },
        # ── 11. Affiliate Performance Table ─────────────────────────────
        {
            "name": "Affiliate Performance Table",
            "description": "Full per-affiliate KPI breakdown (last 30 days)",
            "display": "table",
            "dataset_query": native("""
                SELECT
                    affiliate_name,
                    affiliate_status,
                    country,
                    SUM(clicks)                                     AS clicks,
                    SUM(registrations)                              AS signups,
                    SUM(first_depositors)                           AS ftds,
                    ROUND(SUM(deposits),    2)                      AS deposits,
                    ROUND(SUM(net_revenue), 2)                      AS net_revenue,
                    ROUND(SUM(total_reward),2)                      AS commission,
                    CASE WHEN SUM(clicks) > 0
                         THEN ROUND(SUM(registrations)/SUM(clicks)*100,2)
                         ELSE 0
                    END                                             AS click_to_reg_pct,
                    CASE WHEN SUM(registrations) > 0
                         THEN ROUND(SUM(first_depositors)/SUM(registrations)*100,2)
                         ELSE 0
                    END                                             AS reg_to_ftd_pct
                FROM netrefer_stats
                WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                GROUP BY affiliate_id, affiliate_name, affiliate_status, country
                ORDER BY net_revenue DESC
            """),
            "visualization_settings": {},
        },
    ]


# ──────────────────────────────────────────────
# Dashboard layout
# ──────────────────────────────────────────────

def dashboard_layout(card_name_to_id: dict) -> list:
    """
    Returns dashcards list. Grid is 24 columns wide.
    size_x/size_y are in grid units; row/col are 0-indexed.
    """
    layout = [
        # Row 0 – KPI scalars (4 cards × 6 wide)
        ("Total Clicks – Last 30 Days",        0,  0, 6, 3),
        ("Total Registrations – Last 30 Days", 0,  6, 6, 3),
        ("Total FTDs – Last 30 Days",          0, 12, 6, 3),
        ("Net Revenue – Last 30 Days",         0, 18, 6, 3),
        # Row 3 – time-series charts
        ("Daily Clicks & Registrations",       3,  0, 12, 6),
        ("Daily Net Revenue",                  3, 12, 12, 6),
        # Row 9 – funnel + country pie
        ("Conversion Funnel",                  9,  0, 12, 7),
        ("Net Revenue by Country",             9, 12, 12, 7),
        # Row 16 – top affiliates + by campaign
        ("Top 10 Affiliates by Net Revenue",  16,  0, 12, 7),
        ("Net Revenue by Campaign",           16, 12, 12, 7),
        # Row 23 – full table
        ("Affiliate Performance Table",       23,  0, 24, 8),
    ]

    dashcards = []
    for idx, (name, row, col, size_x, size_y) in enumerate(layout):
        card_id = card_name_to_id.get(name)
        if card_id is None:
            print(f"  [warn] card '{name}' not found, skipping in layout")
            continue
        dashcards.append({
            "id": -(idx + 1),          # temporary negative id for new cards
            "card_id": card_id,
            "row": row,
            "col": col,
            "size_x": size_x,
            "size_y": size_y,
            "parameter_mappings": [],
            "visualization_settings": {},
        })
    return dashcards


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Set up Netrefer Metabase dashboards")
    parser.add_argument("--host",     default="http://localhost:3000")
    parser.add_argument("--user",     required=True, help="Metabase admin email")
    parser.add_argument("--password", required=True, help="Metabase admin password")
    parser.add_argument("--db-name",  default="netrefer", help="Fragment of DB name in Metabase")
    args = parser.parse_args()

    print(f"Connecting to Metabase at {args.host} …")
    mb = MetabaseClient(args.host, args.user, args.password)
    print("  Logged in.")

    # ── Find the database ──────────────────────────────────────
    db_id = find_database(mb, args.db_name)
    print(f"  Found database id={db_id}")

    # ── Sync schema ────────────────────────────────────────────
    print("  Syncing schema …")
    tables = sync_and_wait(mb, db_id)
    stats_table_id = find_table_id(tables, "netrefer_stats")
    print(f"  netrefer_stats table id={stats_table_id}")

    # ── Create / skip cards ────────────────────────────────────
    existing = existing_cards(mb)
    card_name_to_id = {}
    cards = card_definitions(db_id, stats_table_id)

    print(f"\nCreating {len(cards)} cards …")
    for card in cards:
        name = card["name"]
        if name in existing:
            print(f"  [skip] {name}")
            card_name_to_id[name] = existing[name]
            continue
        payload = {
            "name":                   name,
            "description":            card.get("description", ""),
            "display":                card["display"],
            "dataset_query":          card["dataset_query"],
            "visualization_settings": card.get("visualization_settings", {}),
        }
        result = mb.post("/api/card", json=payload)
        card_name_to_id[name] = result["id"]
        print(f"  [created] {name} (id={result['id']})")

    # ── Create / skip dashboard ────────────────────────────────
    dash_name = "Netrefer Affiliate Performance"
    existing_dashes = existing_dashboards(mb)

    if dash_name in existing_dashes:
        dash_id = existing_dashes[dash_name]
        print(f"\n[skip] Dashboard '{dash_name}' already exists (id={dash_id})")
    else:
        dash = mb.post("/api/dashboard", json={
            "name":        dash_name,
            "description": "Affiliate KPIs, revenue, conversions and campaign breakdown",
        })
        dash_id = dash["id"]
        print(f"\n[created] Dashboard '{dash_name}' (id={dash_id})")

    # ── Add cards to dashboard ─────────────────────────────────
    print("  Adding cards to dashboard …")
    dashcards = dashboard_layout(card_name_to_id)

    # Verify dashboard exists
    dash_info = mb.get(f"/api/dashboard/{dash_id}")
    print(f"  Dashboard confirmed: {dash_info.get('name')} (id={dash_id})")

    # Metabase v0.50+: PUT /api/dashboard/:id with dashcards array
    try:
        mb.put(f"/api/dashboard/{dash_id}", json={"dashcards": dashcards})
        print(f"  Added {len(dashcards)} cards via PUT /dashboard.")
    except Exception as e1:
        print(f"  PUT failed ({e1}), trying POST /dashcards …")
        try:
            mb.post(f"/api/dashboard/{dash_id}/dashcards", json={"cards": dashcards})
            print(f"  Added {len(dashcards)} cards via POST /dashcards.")
        except Exception as e2:
            print(f"  POST /dashcards failed ({e2}), trying legacy POST /cards …")
            for dc in dashcards:
                legacy = {
                    "cardId": dc["card_id"],
                    "col":    dc["col"],
                    "row":    dc["row"],
                    "sizeX":  dc["size_x"],
                    "sizeY":  dc["size_y"],
                }
                mb.post(f"/api/dashboard/{dash_id}/cards", json=legacy)
            print(f"  Added {len(dashcards)} cards via legacy POST /cards.")

    print(f"\nDone!  Open: {args.host}/dashboard/{dash_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
