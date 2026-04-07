#!/usr/bin/env python3
"""
setup_leaderboard_dashboard.py  –  Performance Leaderboard dashboard.

Designed to answer one question immediately: who is performing and who is not?

Layout:
  Filters    : From Date, To Date  (top — apply to all cards)
  Row 0      : Top 10 Sources by FTDs (bar) | Top 10 Sources by NGR (bar)
  Row 1      : Conversion Quality — Reg→FTD% by source (bar, sorted best first)
               Click→Reg% by source (bar, sorted best first)
  Row 2      : Full Scorecard table
               Cols: Rank · Source · Clicks · Regs · CR% · FTDs · FTD% · GGR · NGR · Commission
               Sorted by FTDs DESC — immediately see top to bottom

Usage:
    python scripts/setup_leaderboard_dashboard.py \
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
# Metabase client
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

    def get(self, path, **_):    return self._raw("GET",  path)
    def post(self, path, **kw):  return self._raw("POST", path, kw.get("json"))
    def put(self, path, **kw):   return self._raw("PUT",  path, kw.get("json"))
    def delete(self, path, **_): self._raw("DELETE", path)


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
# Parameters
# ──────────────────────────────────────────────

PARAM_FROM = "lb-0001-0001-0001-000000000001"
PARAM_TO   = "lb-0002-0002-0002-000000000002"

TAGS = {
    "from_date": {
        "id": "lb-tt-from", "name": "from_date",
        "display-name": "From Date", "type": "date", "required": False,
    },
    "to_date": {
        "id": "lb-tt-to", "name": "to_date",
        "display-name": "To Date", "type": "date", "required": False,
    },
}

WHERE = """
    WHERE 1=1
    [[AND report_date >= {{from_date}}]]
    [[AND report_date <= {{to_date}}]]
    AND campaign_name != ''
"""


def q(db_id, sql):
    return {
        "type": "native", "database": db_id,
        "native": {"query": sql, "template-tags": TAGS},
    }


def param_mappings(card_id):
    return [
        {"parameter_id": PARAM_FROM, "card_id": card_id,
         "target": ["variable", ["template-tag", "from_date"]]},
        {"parameter_id": PARAM_TO,   "card_id": card_id,
         "target": ["variable", ["template-tag", "to_date"]]},
    ]


# ──────────────────────────────────────────────
# Card SQL
# ──────────────────────────────────────────────

def card_defs(db_id):
    return [

        # ── Top 10 by FTDs ────────────────────────────────────────────────
        {
            "name":    "LB – Top 10 Sources by FTDs",
            "display": "bar",
            "dataset_query": q(db_id, f"""
                SELECT
                    campaign_name                   AS source,
                    SUM(first_depositors)           AS ftds
                FROM netrefer_stats {WHERE}
                GROUP BY campaign_name
                ORDER BY ftds DESC
                LIMIT 10
            """),
            "visualization_settings": {
                "graph.dimensions": ["source"],
                "graph.metrics":    ["ftds"],
                "graph.series_labels": ["FTDs"],
            },
        },

        # ── Top 10 by NGR ─────────────────────────────────────────────────
        {
            "name":    "LB – Top 10 Sources by NGR",
            "display": "bar",
            "dataset_query": q(db_id, f"""
                SELECT
                    campaign_name                       AS source,
                    ROUND(SUM(net_revenue), 2)          AS ngr
                FROM netrefer_stats {WHERE}
                GROUP BY campaign_name
                ORDER BY ngr DESC
                LIMIT 10
            """),
            "visualization_settings": {
                "graph.dimensions": ["source"],
                "graph.metrics":    ["ngr"],
                "number.style": "currency", "currency": "EUR",
            },
        },

        # ── Conversion quality: Reg→FTD% ─────────────────────────────────
        # Only sources with >=10 regs to filter out noise
        {
            "name":    "LB – Reg→FTD% by Source (quality)",
            "display": "bar",
            "dataset_query": q(db_id, f"""
                SELECT
                    campaign_name AS source,
                    ROUND(
                        SUM(first_depositors) * 100.0 / NULLIF(SUM(registrations), 0)
                    , 1) AS reg_to_ftd_pct
                FROM netrefer_stats {WHERE}
                GROUP BY campaign_name
                HAVING SUM(registrations) >= 10
                ORDER BY reg_to_ftd_pct DESC
            """),
            "visualization_settings": {
                "graph.dimensions": ["source"],
                "graph.metrics":    ["reg_to_ftd_pct"],
                "graph.series_labels": ["Reg→FTD %"],
            },
        },

        # ── Conversion quality: Click→Reg% ───────────────────────────────
        {
            "name":    "LB – Click→Reg% by Source (traffic quality)",
            "display": "bar",
            "dataset_query": q(db_id, f"""
                SELECT
                    campaign_name AS source,
                    ROUND(
                        SUM(registrations) * 100.0 / NULLIF(SUM(clicks), 0)
                    , 2) AS click_to_reg_pct
                FROM netrefer_stats {WHERE}
                GROUP BY campaign_name
                HAVING SUM(clicks) >= 100
                ORDER BY click_to_reg_pct DESC
            """),
            "visualization_settings": {
                "graph.dimensions": ["source"],
                "graph.metrics":    ["click_to_reg_pct"],
                "graph.series_labels": ["Click→Reg %"],
            },
        },

        # ── Full scorecard table ──────────────────────────────────────────
        # The single table that answers everything: volume + quality + revenue
        {
            "name":    "LB – Full Performance Scorecard",
            "display": "table",
            "dataset_query": q(db_id, f"""
                SELECT
                    ROW_NUMBER() OVER (ORDER BY SUM(first_depositors) DESC) AS rank,
                    campaign_name                                             AS source,
                    SUM(clicks)                                               AS clicks,
                    SUM(registrations)                                        AS regs,
                    ROUND(
                        SUM(registrations) * 100.0 / NULLIF(SUM(clicks), 0)
                    , 1)                                                      AS click_to_reg_pct,
                    SUM(first_depositors)                                     AS ftds,
                    ROUND(
                        SUM(first_depositors) * 100.0 / NULLIF(SUM(registrations), 0)
                    , 1)                                                      AS reg_to_ftd_pct,
                    ROUND(SUM(deposits),     2)                               AS deposits,
                    ROUND(SUM(gross_revenue), 2)                              AS ggr,
                    ROUND(SUM(net_revenue),  2)                               AS ngr,
                    ROUND(SUM(total_reward), 2)                               AS commission
                FROM netrefer_stats {WHERE}
                GROUP BY campaign_name
                ORDER BY ftds DESC
            """),
            "visualization_settings": {
                "table.column_formatting": [],
                "column_settings": {
                    '["name","click_to_reg_pct"]': {"column_title": "Click→Reg%"},
                    '["name","reg_to_ftd_pct"]':   {"column_title": "Reg→FTD%"},
                    '["name","rank"]':              {"column_title": "#"},
                },
            },
        },

        # ── Bubble: Volume vs Quality ─────────────────────────────────────
        # X=clicks, Y=FTDs, shows who has scale AND quality
        {
            "name":    "LB – Volume vs FTDs by Source",
            "display": "scatter",
            "dataset_query": q(db_id, f"""
                SELECT
                    campaign_name           AS source,
                    SUM(clicks)             AS clicks,
                    SUM(first_depositors)   AS ftds
                FROM netrefer_stats {WHERE}
                GROUP BY campaign_name
                HAVING SUM(clicks) > 0
                ORDER BY ftds DESC
            """),
            "visualization_settings": {
                "graph.dimensions": ["source", "clicks"],
                "graph.metrics":    ["ftds"],
            },
        },
    ]


# ──────────────────────────────────────────────
# Layout
# ──────────────────────────────────────────────

LAYOUT = [
    # name,                                     row, col, size_x, size_y
    # Row 0 — Top 10 by FTDs | Top 10 by NGR
    ("LB – Top 10 Sources by FTDs",               0,  0, 12, 7),
    ("LB – Top 10 Sources by NGR",                0, 12, 12, 7),
    # Row 1 — Conversion quality bars
    ("LB – Reg→FTD% by Source (quality)",         7,  0, 12, 7),
    ("LB – Click→Reg% by Source (traffic quality)",7,12, 12, 7),
    # Row 2 — Volume vs FTDs scatter
    ("LB – Volume vs FTDs by Source",            14,  0, 24, 7),
    # Row 3 — Full scorecard table
    ("LB – Full Performance Scorecard",          21,  0, 24,10),
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
            "parameter_mappings":     param_mappings(card_id),
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
    parser.add_argument("--timezone", default="Europe/Istanbul")
    args = parser.parse_args()

    print(f"Connecting to {args.host} …")
    mb = MetabaseClient(args.host, args.user, args.password)
    print("  Logged in.")

    try:
        mb.put("/api/setting/report-timezone", json={"value": args.timezone})
    except Exception:
        pass

    db_id = find_database(mb, args.db_name)
    print(f"  Database id={db_id}")

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

    dashboard_params = [
        {"id": PARAM_FROM, "name": "From Date", "slug": "from_date", "type": "date/single"},
        {"id": PARAM_TO,   "name": "To Date",   "slug": "to_date",   "type": "date/single"},
    ]

    dash_name = "Performance Leaderboard"
    existing_dashes = existing_dashboards(mb)
    if dash_name in existing_dashes:
        old_id = existing_dashes[dash_name]
        print(f"\n[archiving] Old '{dash_name}' (id={old_id}) …")
        mb.put(f"/api/dashboard/{old_id}", json={"archived": True})

    dash = mb.post("/api/dashboard", json={
        "name":        dash_name,
        "description": "Who is performing and who is not — ranked by FTDs with conversion rates and revenue",
        "parameters":  dashboard_params,
    })
    dash_id = dash["id"]
    print(f"\n[created] Dashboard '{dash_name}' (id={dash_id})")

    dashcards = build_dashcards(card_name_to_id)
    mb.put(f"/api/dashboard/{dash_id}", json={
        "parameters": dashboard_params,
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
