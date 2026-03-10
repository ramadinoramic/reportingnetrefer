#!/usr/bin/env python3
"""
setup_etl_dashboard.py  –  ETL Health Dashboard in Metabase.

Shows run history, warnings, success rate, and daily load activity
from the etl_runs audit table.

Usage:
    python scripts/setup_etl_dashboard.py \
        --host http://localhost:3000 \
        --user admin@example.com \
        --password yourpassword
"""

import argparse
import sys
import requests


# ──────────────────────────────────────────────
# Metabase client (shared pattern)
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


def existing_dashboards(mb):
    items = mb.get("/api/dashboard?f=all")
    if isinstance(items, list):
        return {d["name"]: d["id"] for d in items if not d.get("archived")}
    return {}


# ──────────────────────────────────────────────
# Card definitions
# ──────────────────────────────────────────────

def build_cards(db_id: int):
    def q(sql):
        return {
            "type":     "native",
            "database": db_id,
            "native":   {"query": sql.strip()},
        }

    return [
        # ── KPI scalars ───────────────────────────────────────────────────
        {
            "name":    "ETL – Last Run Status",
            "display": "scalar",
            "dataset_query": q("""
                SELECT CONCAT(status, ' — ', source_detail)
                FROM etl_runs
                ORDER BY started_at DESC
                LIMIT 1
            """),
            "visualization_settings": {},
        },
        {
            "name":    "ETL – Runs (Last 7 Days)",
            "display": "scalar",
            "dataset_query": q("""
                SELECT COUNT(*) AS runs
                FROM etl_runs
                WHERE started_at >= NOW() - INTERVAL 7 DAY
                  AND status != 'running'
            """),
            "visualization_settings": {},
        },
        {
            "name":    "ETL – Success Rate (Last 30 Days)",
            "display": "scalar",
            "dataset_query": q("""
                SELECT CONCAT(
                    ROUND(
                        100.0 * SUM(status = 'success') / NULLIF(COUNT(*), 0),
                        1
                    ), '%'
                ) AS success_rate
                FROM etl_runs
                WHERE started_at >= NOW() - INTERVAL 30 DAY
                  AND status != 'running'
            """),
            "visualization_settings": {},
        },
        {
            "name":    "ETL – Rows Loaded (Last 7 Days)",
            "display": "scalar",
            "dataset_query": q("""
                SELECT SUM(rows_parsed) AS rows_loaded
                FROM etl_runs
                WHERE started_at >= NOW() - INTERVAL 7 DAY
                  AND status = 'success'
            """),
            "visualization_settings": {},
        },
        # ── Charts ────────────────────────────────────────────────────────
        {
            "name":    "ETL – Daily Load Activity",
            "display": "bar",
            "dataset_query": q("""
                SELECT
                    DATE(started_at)        AS load_date,
                    COUNT(*)                AS total_runs,
                    SUM(status = 'success') AS successful,
                    SUM(status = 'failed')  AS failed
                FROM etl_runs
                WHERE started_at >= NOW() - INTERVAL 30 DAY
                  AND status != 'running'
                GROUP BY DATE(started_at)
                ORDER BY load_date
            """),
            "visualization_settings": {
                "graph.dimensions":  ["load_date"],
                "graph.metrics":     ["successful", "failed"],
                "stackable.stack_type": "stacked",
                "graph.colors":      ["#10b981", "#ef4444"],
            },
        },
        {
            "name":    "ETL – Rows Parsed per Run",
            "display": "bar",
            "dataset_query": q("""
                SELECT
                    DATE(started_at)  AS load_date,
                    SUM(rows_parsed)  AS rows_parsed
                FROM etl_runs
                WHERE started_at >= NOW() - INTERVAL 30 DAY
                  AND status = 'success'
                GROUP BY DATE(started_at)
                ORDER BY load_date
            """),
            "visualization_settings": {
                "graph.dimensions": ["load_date"],
                "graph.metrics":    ["rows_parsed"],
            },
        },
        # ── Tables ────────────────────────────────────────────────────────
        {
            "name":    "ETL – Run History",
            "display": "table",
            "dataset_query": q("""
                SELECT
                    started_at,
                    source_detail                       AS file,
                    status,
                    rows_parsed,
                    rows_upserted,
                    TIMESTAMPDIFF(SECOND, started_at, finished_at) AS duration_sec,
                    warnings
                FROM etl_runs
                WHERE status != 'running'
                ORDER BY started_at DESC
                LIMIT 100
            """),
            "visualization_settings": {},
        },
        {
            "name":    "ETL – Warnings Log",
            "display": "table",
            "dataset_query": q("""
                SELECT
                    started_at,
                    source_detail  AS file,
                    status,
                    warnings
                FROM etl_runs
                WHERE warnings IS NOT NULL
                  AND warnings != ''
                ORDER BY started_at DESC
                LIMIT 50
            """),
            "visualization_settings": {},
        },
        {
            "name":    "ETL – Failed Runs",
            "display": "table",
            "dataset_query": q("""
                SELECT
                    started_at,
                    source_detail  AS file,
                    error_message,
                    warnings
                FROM etl_runs
                WHERE status = 'failed'
                ORDER BY started_at DESC
                LIMIT 50
            """),
            "visualization_settings": {},
        },
    ]


# ──────────────────────────────────────────────
# Dashboard layout
# ──────────────────────────────────────────────

# (name, row, col, size_x, size_y)
LAYOUT = [
    ("ETL – Last Run Status",          0,  0,  6, 3),
    ("ETL – Runs (Last 7 Days)",        0,  6,  6, 3),
    ("ETL – Success Rate (Last 30 Days)", 0, 12,  6, 3),
    ("ETL – Rows Loaded (Last 7 Days)", 0, 18,  6, 3),
    ("ETL – Daily Load Activity",       3,  0, 12, 7),
    ("ETL – Rows Parsed per Run",       3, 12, 12, 7),
    ("ETL – Run History",              10,  0, 24, 8),
    ("ETL – Warnings Log",             18,  0, 12, 7),
    ("ETL – Failed Runs",              18, 12, 12, 7),
]


def build_dashcards(card_name_to_id):
    dashcards = []
    for idx, (name, row, col, size_x, size_y) in enumerate(LAYOUT):
        card_id = card_name_to_id.get(name)
        if not card_id:
            print(f"  [warn] Card not found for layout: {name}")
            continue
        dashcards.append({
            "id":                    -(idx + 1),
            "card_id":               card_id,
            "row":                   row,
            "col":                   col,
            "size_x":                size_x,
            "size_y":                size_y,
            "parameter_mappings":    [],
            "visualization_settings": {},
        })
    return dashcards


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Create / update ETL Health Dashboard")
    parser.add_argument("--host",     default="http://localhost:3000")
    parser.add_argument("--user",     required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--db-name",  default="netrefer",
                        help="Fragment of the Metabase database name")
    args = parser.parse_args()

    mb    = MetabaseClient(args.host, args.user, args.password)
    db_id = find_database(mb, args.db_name)
    print(f"Using database id={db_id}")

    cards = build_cards(db_id)

    # Upsert cards
    existing_cards = {c["name"]: c["id"] for c in mb.get("/api/card")}
    card_name_to_id = {}
    print("\nUpserting cards …")
    for card in cards:
        name    = card["name"]
        payload = {
            "name":                   name,
            "display":                card["display"],
            "dataset_query":          card["dataset_query"],
            "visualization_settings": card.get("visualization_settings", {}),
        }
        if name in existing_cards:
            card_id = existing_cards[name]
            mb.put(f"/api/card/{card_id}", json=payload)
            print(f"  [updated] {name} (id={card_id})")
        else:
            result  = mb.post("/api/card", json=payload)
            card_id = result["id"]
            print(f"  [created] {name} (id={card_id})")
        card_name_to_id[name] = card_id

    # Archive old dashboard, create fresh
    dash_name = "ETL Health"
    existing  = existing_dashboards(mb)
    if dash_name in existing:
        old_id = existing[dash_name]
        print(f"\n[deleting] Old dashboard '{dash_name}' (id={old_id}) …")
        mb.put(f"/api/dashboard/{old_id}", json={"archived": True})

    dash = mb.post("/api/dashboard", json={
        "name":        dash_name,
        "description": "ETL run history, data-quality warnings, and load activity",
        "parameters":  [],
    })
    dash_id = dash["id"]
    print(f"\n[created] Dashboard '{dash_name}' (id={dash_id})")

    dashcards = build_dashcards(card_name_to_id)
    mb.put(f"/api/dashboard/{dash_id}", json={"dashcards": dashcards})
    print(f"  {len(dashcards)} cards wired.")
    print(f"\nDone!  Open: {args.host}/dashboard/{dash_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
