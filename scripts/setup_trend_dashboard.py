#!/usr/bin/env python3
"""
setup_trend_dashboard.py  –  Creates the "Source Drop Detection" dashboard.

No filters needed — every card anchors itself to MAX(report_date) in the DB
and computes rolling windows automatically.  Just open and read.

Layout:
  Row 0 – KPI: Sources dropping 3d | Sources dropping 7d | Sources dropping 15d
  Row 1 – Last 3 days vs prior 3 days  (comparison table)
  Row 2 – Last 7 days vs prior 7 days  (comparison table)
  Row 3 – Last 15 days vs prior 15 days (comparison table)

Usage:
    python scripts/setup_trend_dashboard.py \
        --host http://localhost:3001 \
        --user admin@example.com \
        --password yourpassword
"""

import argparse
import sys
import json
import urllib.request
import urllib.error


# ──────────────────────────────────────────────
# Metabase client  (stdlib only – no pip deps)
# ──────────────────────────────────────────────

class MetabaseClient:
    def __init__(self, host, email, password):
        self.host = host.rstrip("/")
        self.token = None
        resp = self._raw("POST", "/api/session",
                         {"username": email, "password": password})
        self.token = resp["id"]

    def _raw(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Metabase-Session"] = self.token
        req = urllib.request.Request(
            self.host + path, data=data, headers=headers, method=method)
        try:
            resp = urllib.request.urlopen(req)
            content = resp.read()
            return json.loads(content) if content else {}
        except urllib.error.HTTPError as e:
            snippet = e.read().decode(errors="replace")[:400]
            raise RuntimeError(f"HTTP {e.code} {e.reason} on {method} {path}: {snippet}")

    def get(self, path, **_):       return self._raw("GET",    path)
    def post(self, path, **kw):     return self._raw("POST",   path, kw.get("json"))
    def put(self, path, **kw):      return self._raw("PUT",    path, kw.get("json"))
    def delete(self, path, **_):    self._raw("DELETE", path)


def find_database(mb, name_fragment):
    dbs   = mb.get("/api/database")
    items = dbs if isinstance(dbs, list) else dbs.get("data", [])
    for db in items:
        if name_fragment.lower() in db["name"].lower():
            return db["id"]
    raise RuntimeError(f"No database matching '{name_fragment}'")


def existing_cards(mb):
    active = mb.get("/api/card")
    try:
        archived = mb.get("/api/card?archived=true")
    except Exception:
        archived = []
    return {c["name"]: c["id"] for c in (active + archived)}


def existing_dashboards(mb):
    return {d["name"]: d["id"] for d in mb.get("/api/dashboard")}


# ──────────────────────────────────────────────
# SQL helpers
# ──────────────────────────────────────────────

def anchor_expr():
    """Subquery that returns the most recent report_date in the table."""
    return "(SELECT MAX(report_date) FROM netrefer_stats)"


def window_sql(days: int) -> str:
    """
    Returns a full SELECT comparing current <days>-day window vs prior window.
    Anchor = MAX(report_date).  No template tags — pure self-contained SQL.

    Window arithmetic:
      current  : anchor - (days-1) days  …  anchor
      prior    : anchor - (2*days-1) days … anchor - days days
    """
    anchor = anchor_expr()
    n      = days - 1
    p1     = days          # first day of prior window offset
    p2     = 2 * days - 1  # last  day of prior window offset

    return f"""
SELECT
    campaign_name                                           AS source,

    -- Current window
    SUM(CASE WHEN report_date BETWEEN
        DATE_SUB({anchor}, INTERVAL {n} DAY) AND {anchor}
        THEN clicks           ELSE 0 END)                  AS clicks_now,

    -- Prior window
    SUM(CASE WHEN report_date BETWEEN
        DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY)
        THEN clicks           ELSE 0 END)                  AS clicks_prev,

    -- Delta %
    ROUND(
        CASE WHEN SUM(CASE WHEN report_date BETWEEN
                DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY)
                THEN clicks ELSE 0 END) > 0
             THEN (
                SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor}, INTERVAL {n} DAY) AND {anchor} THEN clicks ELSE 0 END)
              - SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY) THEN clicks ELSE 0 END)
             ) * 100.0
              / SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY) THEN clicks ELSE 0 END)
             ELSE NULL
        END, 1)                                            AS clicks_delta_pct,

    -- Registrations
    SUM(CASE WHEN report_date BETWEEN
        DATE_SUB({anchor}, INTERVAL {n} DAY) AND {anchor}
        THEN registrations    ELSE 0 END)                  AS regs_now,

    SUM(CASE WHEN report_date BETWEEN
        DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY)
        THEN registrations    ELSE 0 END)                  AS regs_prev,

    ROUND(
        CASE WHEN SUM(CASE WHEN report_date BETWEEN
                DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY)
                THEN registrations ELSE 0 END) > 0
             THEN (
                SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor}, INTERVAL {n} DAY) AND {anchor} THEN registrations ELSE 0 END)
              - SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY) THEN registrations ELSE 0 END)
             ) * 100.0
              / SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY) THEN registrations ELSE 0 END)
             ELSE NULL
        END, 1)                                            AS regs_delta_pct,

    -- FTDs
    SUM(CASE WHEN report_date BETWEEN
        DATE_SUB({anchor}, INTERVAL {n} DAY) AND {anchor}
        THEN first_depositors ELSE 0 END)                  AS ftds_now,

    SUM(CASE WHEN report_date BETWEEN
        DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY)
        THEN first_depositors ELSE 0 END)                  AS ftds_prev,

    ROUND(
        CASE WHEN SUM(CASE WHEN report_date BETWEEN
                DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY)
                THEN first_depositors ELSE 0 END) > 0
             THEN (
                SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor}, INTERVAL {n} DAY) AND {anchor} THEN first_depositors ELSE 0 END)
              - SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY) THEN first_depositors ELSE 0 END)
             ) * 100.0
              / SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY) THEN first_depositors ELSE 0 END)
             ELSE NULL
        END, 1)                                            AS ftds_delta_pct

FROM netrefer_stats
WHERE report_date >= DATE_SUB({anchor}, INTERVAL {p2} DAY)
  AND campaign_name != ''
GROUP BY campaign_name
ORDER BY ftds_delta_pct ASC   -- worst drops at the top
"""


def dropping_count_sql(days: int, drop_threshold: int = 20) -> str:
    """Scalar: how many sources dropped FTDs by more than drop_threshold % in this window."""
    anchor = anchor_expr()
    n      = days - 1
    p1     = days
    p2     = 2 * days - 1
    return f"""
SELECT COUNT(*) AS sources_dropping
FROM (
    SELECT
        campaign_name,
        SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor}, INTERVAL {n} DAY) AND {anchor}
                 THEN first_depositors ELSE 0 END)  AS ftds_now,
        SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor}, INTERVAL {p2} DAY) AND DATE_SUB({anchor}, INTERVAL {p1} DAY)
                 THEN first_depositors ELSE 0 END)  AS ftds_prev
    FROM netrefer_stats
    WHERE report_date >= DATE_SUB({anchor}, INTERVAL {p2} DAY)
      AND campaign_name != ''
    GROUP BY campaign_name
    HAVING ftds_prev > 0
       AND (ftds_now - ftds_prev) * 100.0 / ftds_prev <= -{drop_threshold}
) t
"""


# ──────────────────────────────────────────────
# Card definitions
# ──────────────────────────────────────────────

def fixed_query(db_id, sql):
    """Native query with no template tags."""
    return {
        "type":     "native",
        "database": db_id,
        "native":   {"query": sql, "template-tags": {}},
    }


def card_defs(db_id):
    return [
        # ── Scalar KPIs ───────────────────────────────────────────────────
        {
            "name":    "TD – Sources Dropping (3d)",
            "display": "scalar",
            "dataset_query": fixed_query(db_id, dropping_count_sql(3)),
            "visualization_settings": {},
        },
        {
            "name":    "TD – Sources Dropping (7d)",
            "display": "scalar",
            "dataset_query": fixed_query(db_id, dropping_count_sql(7)),
            "visualization_settings": {},
        },
        {
            "name":    "TD – Sources Dropping (15d)",
            "display": "scalar",
            "dataset_query": fixed_query(db_id, dropping_count_sql(15)),
            "visualization_settings": {},
        },

        # ── Comparison tables ─────────────────────────────────────────────
        {
            "name":    "TD – 3-Day Source Comparison",
            "display": "table",
            "dataset_query": fixed_query(db_id, window_sql(3)),
            "visualization_settings": {
                "column_settings": {
                    '["name","ftds_delta_pct"]':   {"column_title": "FTDs Δ%"},
                    '["name","regs_delta_pct"]':   {"column_title": "Regs Δ%"},
                    '["name","clicks_delta_pct"]': {"column_title": "Clicks Δ%"},
                },
            },
        },
        {
            "name":    "TD – 7-Day Source Comparison",
            "display": "table",
            "dataset_query": fixed_query(db_id, window_sql(7)),
            "visualization_settings": {
                "column_settings": {
                    '["name","ftds_delta_pct"]':   {"column_title": "FTDs Δ%"},
                    '["name","regs_delta_pct"]':   {"column_title": "Regs Δ%"},
                    '["name","clicks_delta_pct"]': {"column_title": "Clicks Δ%"},
                },
            },
        },
        {
            "name":    "TD – 15-Day Source Comparison",
            "display": "table",
            "dataset_query": fixed_query(db_id, window_sql(15)),
            "visualization_settings": {
                "column_settings": {
                    '["name","ftds_delta_pct"]':   {"column_title": "FTDs Δ%"},
                    '["name","regs_delta_pct"]':   {"column_title": "Regs Δ%"},
                    '["name","clicks_delta_pct"]': {"column_title": "Clicks Δ%"},
                },
            },
        },

        # ── Bar chart: FTD delta by source for 7d (quick visual) ─────────
        {
            "name":    "TD – FTD Change % by Source (7d)",
            "display": "bar",
            "dataset_query": fixed_query(db_id, f"""
                SELECT
                    campaign_name AS source,
                    ROUND(
                        CASE WHEN SUM(CASE WHEN report_date BETWEEN
                                DATE_SUB({anchor_expr()}, INTERVAL 13 DAY)
                                AND DATE_SUB({anchor_expr()}, INTERVAL 7 DAY)
                                THEN first_depositors ELSE 0 END) > 0
                             THEN (
                                SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor_expr()}, INTERVAL 6 DAY) AND {anchor_expr()} THEN first_depositors ELSE 0 END)
                              - SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor_expr()}, INTERVAL 13 DAY) AND DATE_SUB({anchor_expr()}, INTERVAL 7 DAY) THEN first_depositors ELSE 0 END)
                             ) * 100.0
                              / SUM(CASE WHEN report_date BETWEEN DATE_SUB({anchor_expr()}, INTERVAL 13 DAY) AND DATE_SUB({anchor_expr()}, INTERVAL 7 DAY) THEN first_depositors ELSE 0 END)
                             ELSE NULL
                        END, 1)  AS ftd_change_pct
                FROM netrefer_stats
                WHERE report_date >= DATE_SUB({anchor_expr()}, INTERVAL 13 DAY)
                  AND campaign_name != ''
                GROUP BY campaign_name
                HAVING ftd_change_pct IS NOT NULL
                ORDER BY ftd_change_pct ASC
            """),
            "visualization_settings": {
                "graph.dimensions": ["source"],
                "graph.metrics":    ["ftd_change_pct"],
            },
        },
    ]


# ──────────────────────────────────────────────
# Dashboard layout
# ──────────────────────────────────────────────

LAYOUT = [
    # name,                              row, col, size_x, size_y
    # Row 0 — KPI scalars (sources dropping count)
    ("TD – Sources Dropping (3d)",         0,  0,  8, 3),
    ("TD – Sources Dropping (7d)",         0,  8,  8, 3),
    ("TD – Sources Dropping (15d)",        0, 16,  8, 3),
    # Row 1 — FTD change bar chart
    ("TD – FTD Change % by Source (7d)",   3,  0, 24, 7),
    # Row 2–4 — Comparison tables
    ("TD – 3-Day Source Comparison",      10,  0, 24, 8),
    ("TD – 7-Day Source Comparison",      18,  0, 24, 8),
    ("TD – 15-Day Source Comparison",     26,  0, 24, 8),
]


def build_dashcards(card_name_to_id):
    dashcards = []
    for idx, (name, row, col, size_x, size_y) in enumerate(LAYOUT):
        card_id = card_name_to_id.get(name)
        if card_id is None:
            print(f"  [warn] card '{name}' not found, skipping")
            continue
        dashcards.append({
            "id":                    -(idx + 1),
            "card_id":                card_id,
            "row":                    row,
            "col":                    col,
            "size_x":                 size_x,
            "size_y":                 size_y,
            "parameter_mappings":     [],
            "visualization_settings": {},
        })
    return dashcards


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host",     default="http://localhost:3001")
    parser.add_argument("--user",     required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--db-name",  default="netrefer")
    args = parser.parse_args()

    print(f"Connecting to {args.host} …")
    mb = MetabaseClient(args.host, args.user, args.password)
    print("  Logged in.")

    db_id = find_database(mb, args.db_name)
    print(f"  Database id={db_id}")

    # Upsert cards
    existing = existing_cards(mb)
    card_name_to_id = {}
    print("\nUpserting cards …")
    for card in card_defs(db_id):
        name = card["name"]
        payload = {
            "name":                   name,
            "display":                card["display"],
            "dataset_query":          card["dataset_query"],
            "visualization_settings": card.get("visualization_settings", {}),
            "archived":               False,
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

    dash_name = "Source Drop Detection"
    existing_dashes = existing_dashboards(mb)
    if dash_name in existing_dashes:
        dash_id = existing_dashes[dash_name]
        print(f"\n[updating] Dashboard '{dash_name}' in-place (id={dash_id})")
        mb.put(f"/api/dashboard/{dash_id}", json={"parameters": [], "dashcards": []})
    else:
        dash = mb.post("/api/dashboard", json={
            "name":        dash_name,
            "description": "Which sources are dropping? Compares last 3 / 7 / 15 days vs prior same-length window. Sorted worst-first.",
            "parameters":  [],
        })
        dash_id = dash["id"]
        print(f"\n[created] Dashboard '{dash_name}' (id={dash_id})")

    dashcards = build_dashcards(card_name_to_id)
    mb.put(f"/api/dashboard/{dash_id}", json={
        "parameters": [],
        "dashcards":  dashcards,
    })
    print(f"  {len(dashcards)} cards wired.")
    print(f"\nDone!  Open: {args.host}/dashboard/{dash_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
