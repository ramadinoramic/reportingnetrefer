#!/usr/bin/env python3
"""
setup_bw_dashboard.py  —  Bahigo or Wettigo brand dashboard in Metabase.

Creates (or updates in-place) a dashboard for a single brand showing:
  Row 0 – KPI scalars: FTDs, Registrations, Clicks, NGR, Commission
  Row 1 – Daily FTDs trend (line)
  Row 2 – FTDs by Geo (horizontal bar)  |  NGR by Geo (horizontal bar)
  Row 3 – Top Affiliates by FTDs (horizontal bar)
  Row 4 – Full affiliate performance table

Filters: From Date, To Date, Geo, Affiliate Name

Usage:
    python scripts/setup_bw_dashboard.py --brand Bahigo \\
        --host http://localhost:3001 \\
        --user admin@example.com --password secret

    python scripts/setup_bw_dashboard.py --brand Wettigo \\
        --host http://localhost:3001 \\
        --user admin@example.com --password secret
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


# ──────────────────────────────────────────────
# Metabase client
# ──────────────────────────────────────────────

class MetabaseClient:
    def __init__(self, host, email, password):
        self.host  = host.rstrip("/")
        self.token = None
        resp = self._raw("POST", "/api/session", {"username": email, "password": password})
        self.token = resp["id"]

    def _raw(self, method, path, body=None):
        data    = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Metabase-Session"] = self.token
        req = urllib.request.Request(
            self.host + path, data=data, headers=headers, method=method)
        try:
            resp    = urllib.request.urlopen(req)
            content = resp.read()
            return json.loads(content) if content else {}
        except urllib.error.HTTPError as e:
            snippet = e.read().decode(errors="replace")[:400]
            raise RuntimeError(f"HTTP {e.code} {e.reason} on {method} {path}: {snippet}")

    def get(self, path, **_):    return self._raw("GET",    path)
    def post(self, path, **kw):  return self._raw("POST",   path, kw.get("json"))
    def put(self, path, **kw):   return self._raw("PUT",    path, kw.get("json"))
    def delete(self, path, **_): self._raw("DELETE", path)


def find_database(mb, name_fragment):
    dbs   = mb.get("/api/database")
    items = dbs if isinstance(dbs, list) else dbs.get("data", [])
    for db in items:
        if name_fragment.lower() in db["name"].lower():
            return db["id"]
    raise RuntimeError(f"No database matching '{name_fragment}'")


def get_or_create_dashboard(mb, name, params):
    """Return dashboard id, creating it if it doesn't already exist."""
    all_dash = mb.get("/api/dashboard")
    for d in all_dash:
        if d.get("name") == name and not d.get("archived"):
            dash_id = d["id"]
            print(f"  Updating existing dashboard '{name}' (id={dash_id})")
            mb.put(f"/api/dashboard/{dash_id}", json={"parameters": params, "dashcards": []})
            return dash_id
    print(f"  Creating new dashboard '{name}'")
    dash = mb.post("/api/dashboard", json={"name": name, "parameters": params})
    return dash["id"]


def make_card(mb, db_id, name, sql, display, viz_settings=None):
    """Create a native SQL card and return its id."""
    card = mb.post("/api/card", json={
        "name":            name,
        "display":         display,
        "dataset_query": {
            "type":     "native",
            "database": db_id,
            "native": {
                "query":          sql,
                "template-tags":  _extract_tags(sql),
            },
        },
        "visualization_settings": viz_settings or {},
    })
    return card["id"]


def _extract_tags(sql: str) -> dict:
    """Build Metabase template-tag dicts for every {{var}} in sql."""
    import re
    tags = {}
    for name in re.findall(r"\{\{(\w+)\}\}", sql):
        tags[name] = {
            "id":           name,
            "name":         name,
            "display-name": name.replace("_", " ").title(),
            "type":         "text",
        }
    # Override known date tags
    for name in re.findall(r"\{\{(\w*date\w*)\}\}", sql, re.IGNORECASE):
        tags[name] = {
            "id":           name,
            "name":         name,
            "display-name": name.replace("_", " ").title(),
            "type":         "date",
        }
    return tags


def add_card_to_dashboard(mb, dash_id, card_id, col, row, size_x=8, size_y=6,
                           param_mappings=None):
    payload = {
        "cardId":          card_id,
        "col":             col,
        "row":             row,
        "size_x":          size_x,
        "size_y":          size_y,
        "parameter_mappings": param_mappings or [],
        "visualization_settings": {},
    }
    mb.post(f"/api/dashboard/{dash_id}/cards", json=payload)


# ──────────────────────────────────────────────
# SQL queries (brand_name is baked-in per brand)
# ──────────────────────────────────────────────

def build_queries(brand: str) -> dict:
    B = brand   # e.g. 'Bahigo' or 'Wettigo'

    WHERE = f"""
    WHERE brand_name = '{B}'
      [[AND geo               = {{{{geo}}}}]]
      [[AND affiliate_name    LIKE {{{{affiliate_name}}}}]]
      [[AND report_date      >= {{{{from_date}}}}]]
      [[AND report_date      <= {{{{to_date}}}}]]"""

    return {
        "ftds_scalar": f"""
            SELECT SUM(first_depositors) AS ftds
            FROM bahigo_wettigo_stats
            {WHERE}
        """,
        "regs_scalar": f"""
            SELECT SUM(registrations) AS registrations
            FROM bahigo_wettigo_stats
            {WHERE}
        """,
        "clicks_scalar": f"""
            SELECT SUM(clicks) AS clicks
            FROM bahigo_wettigo_stats
            {WHERE}
        """,
        "ngr_scalar": f"""
            SELECT ROUND(SUM(net_revenue), 0) AS ngr
            FROM bahigo_wettigo_stats
            {WHERE}
        """,
        "commission_scalar": f"""
            SELECT ROUND(SUM(total_reward), 0) AS commission
            FROM bahigo_wettigo_stats
            {WHERE}
        """,
        "daily_ftds": f"""
            SELECT report_date, SUM(first_depositors) AS ftds
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY report_date
            ORDER BY report_date
        """,
        "ftds_by_geo": f"""
            SELECT geo, SUM(first_depositors) AS ftds
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY geo
            ORDER BY ftds DESC
        """,
        "ngr_by_geo": f"""
            SELECT geo, ROUND(SUM(net_revenue), 0) AS ngr
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY geo
            ORDER BY ngr DESC
        """,
        "top_affiliates": f"""
            SELECT affiliate_name,
                   SUM(first_depositors)      AS ftds,
                   SUM(registrations)         AS regs,
                   ROUND(SUM(net_revenue), 0) AS ngr,
                   ROUND(SUM(total_reward), 0) AS commission,
                   ROUND(SUM(first_depositors)*100.0/NULLIF(SUM(registrations),0),1) AS ftd_rate_pct
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY affiliate_name
            ORDER BY ftds DESC
            LIMIT 15
        """,
        "scorecard": f"""
            SELECT affiliate_name,
                   geo,
                   SUM(clicks)                AS clicks,
                   SUM(registrations)         AS regs,
                   SUM(first_depositors)      AS ftds,
                   ROUND(SUM(net_revenue), 0) AS ngr,
                   ROUND(SUM(total_reward), 0) AS commission,
                   ROUND(SUM(first_depositors)*100.0/NULLIF(SUM(registrations),0),1) AS ftd_rate_pct,
                   ROUND(SUM(net_revenue)/NULLIF(SUM(first_depositors),0),0) AS ngr_per_ftd
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY affiliate_name, geo
            ORDER BY ftds DESC
        """,
    }


# ──────────────────────────────────────────────
# Dashboard builder
# ──────────────────────────────────────────────

def build_dashboard(mb, db_id, brand):
    dash_name = f"{brand} Performance Overview"
    print(f"\nBuilding dashboard: {dash_name}")

    queries = build_queries(brand)

    # Dashboard-level filter parameters
    params = [
        {"id": "from_date",       "name": "From Date",       "slug": "from_date",       "type": "date/single"},
        {"id": "to_date",         "name": "To Date",         "slug": "to_date",         "type": "date/single"},
        {"id": "geo",             "name": "Geo",             "slug": "geo",             "type": "category"},
        {"id": "affiliate_name",  "name": "Affiliate",       "slug": "affiliate_name",  "type": "category"},
    ]

    dash_id = get_or_create_dashboard(mb, dash_name, params)

    # ── Create cards ──────────────────────────────────────────────────────
    print("  Creating cards...")

    num_viz = {
        "number.style": "decimal",
        "column_settings": {},
    }
    num_money = {
        "number.style": "currency",
        "currency": "EUR",
        "currency_style": "symbol",
    }
    bar_viz = {
        "graph.dimensions": ["geo"],
        "graph.metrics":    ["ftds"],
    }

    cards = {
        "ftds":       make_card(mb, db_id, f"{brand} — FTDs",         queries["ftds_scalar"],       "scalar", num_viz),
        "regs":       make_card(mb, db_id, f"{brand} — Registrations", queries["regs_scalar"],       "scalar", num_viz),
        "clicks":     make_card(mb, db_id, f"{brand} — Clicks",        queries["clicks_scalar"],      "scalar", num_viz),
        "ngr":        make_card(mb, db_id, f"{brand} — NGR",           queries["ngr_scalar"],         "scalar", num_money),
        "commission": make_card(mb, db_id, f"{brand} — Commission",    queries["commission_scalar"],  "scalar", num_money),
        "daily":      make_card(mb, db_id, f"{brand} — Daily FTDs Trend", queries["daily_ftds"],      "line", {
            "graph.dimensions": ["report_date"],
            "graph.metrics":    ["ftds"],
        }),
        "ftds_geo":   make_card(mb, db_id, f"{brand} — FTDs by Geo",  queries["ftds_by_geo"],        "row", {
            "graph.dimensions": ["geo"],
            "graph.metrics":    ["ftds"],
        }),
        "ngr_geo":    make_card(mb, db_id, f"{brand} — NGR by Geo",   queries["ngr_by_geo"],         "row", {
            "graph.dimensions": ["geo"],
            "graph.metrics":    ["ngr"],
        }),
        "top_aff":    make_card(mb, db_id, f"{brand} — Top Affiliates by FTDs", queries["top_affiliates"], "row", {
            "graph.dimensions": ["affiliate_name"],
            "graph.metrics":    ["ftds"],
        }),
        "scorecard":  make_card(mb, db_id, f"{brand} — Full Scorecard", queries["scorecard"],         "table", {}),
    }

    print(f"  Cards created: {list(cards.keys())}")

    # ── Layout ────────────────────────────────────────────────────────────
    # Helper: param_mapping for a date tag
    def date_map(param_id, card_id, tag_name):
        return [{
            "parameter_id": param_id,
            "card_id":      card_id,
            "target":       ["variable", ["template-tag", tag_name]],
        }]

    def cat_map(param_id, card_id, tag_name):
        return [{
            "parameter_id": param_id,
            "card_id":      card_id,
            "target":       ["variable", ["template-tag", tag_name]],
        }]

    def all_maps(card_id):
        return (
            date_map("from_date",      card_id, "from_date") +
            date_map("to_date",        card_id, "to_date") +
            cat_map("geo",             card_id, "geo") +
            cat_map("affiliate_name",  card_id, "affiliate_name")
        )

    # Row 0: KPI scalars (5 × width-4, height-4)
    row = 0
    for i, key in enumerate(["ftds", "regs", "clicks", "ngr", "commission"]):
        add_card_to_dashboard(mb, dash_id, cards[key],
                              col=i * 4, row=row, size_x=4, size_y=4,
                              param_mappings=all_maps(cards[key]))

    # Row 1: Daily trend (full width)
    row = 4
    add_card_to_dashboard(mb, dash_id, cards["daily"],
                          col=0, row=row, size_x=20, size_y=6,
                          param_mappings=all_maps(cards["daily"]))

    # Row 2: FTDs by Geo | NGR by Geo (half each)
    row = 10
    add_card_to_dashboard(mb, dash_id, cards["ftds_geo"],
                          col=0, row=row, size_x=10, size_y=7,
                          param_mappings=all_maps(cards["ftds_geo"]))
    add_card_to_dashboard(mb, dash_id, cards["ngr_geo"],
                          col=10, row=row, size_x=10, size_y=7,
                          param_mappings=all_maps(cards["ngr_geo"]))

    # Row 3: Top affiliates (full width)
    row = 17
    add_card_to_dashboard(mb, dash_id, cards["top_aff"],
                          col=0, row=row, size_x=20, size_y=8,
                          param_mappings=all_maps(cards["top_aff"]))

    # Row 4: Full scorecard table
    row = 25
    add_card_to_dashboard(mb, dash_id, cards["scorecard"],
                          col=0, row=row, size_x=20, size_y=10,
                          param_mappings=all_maps(cards["scorecard"]))

    print(f"  Dashboard ready: {mb.host}/dashboard/{dash_id}")
    return dash_id


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Create/update a Bahigo or Wettigo dashboard")
    p.add_argument("--brand",    required=True, choices=["Bahigo", "Wettigo"],
                   help="Which brand dashboard to build")
    p.add_argument("--host",     default="http://localhost:3001")
    p.add_argument("--user",     required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--db-name",  default="netrefer",
                   help="Fragment of the Metabase database name (default: netrefer)")
    args = p.parse_args()

    print(f"Connecting to {args.host} …")
    mb    = MetabaseClient(args.host, args.user, args.password)
    db_id = find_database(mb, args.db_name)
    print(f"Using database id={db_id}")

    build_dashboard(mb, db_id, args.brand)
    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
