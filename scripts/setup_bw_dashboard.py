#!/usr/bin/env python3
"""
setup_bw_dashboard.py  —  Bahigo or Wettigo brand dashboard in Metabase.

Creates (or updates in-place) a dashboard for a single brand showing:
  Row 0  – KPI scalars: FTDs, Registrations, Clicks, NGR, Commission
  Row 1  – Daily FTDs trend (line)
  Row 2  – FTDs by Geo (bar)       |  NGR by Geo (bar)
  Row 2b – Clicks by Geo (bar)     |  Signups by Geo (bar)
  Row 3  – Top Affiliates by FTDs  |  by Signups  |  by Clicks
  Row 4  – Full Scorecard table (aggregated across selected date range)
  Row 5  – Daily Scorecard table (one row per day — set From=To for a single day)

Filters: From Date, To Date, Geo (dropdown), Affiliate (dropdown)

Usage:
    python scripts/setup_bw_dashboard.py --brand Bahigo \\
        --host http://localhost:3001 \\
        --user admin@example.com --password secret
"""

import argparse
import json
import re
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


def sync_database(mb, db_id):
    """Trigger a full schema sync and wait for the table to appear."""
    import time
    print(f"  Triggering schema sync for database {db_id} …")
    try:
        mb.post(f"/api/database/{db_id}/sync_schema")
    except Exception as e:
        print(f"  (sync request: {e} — continuing anyway)")
    # Give Metabase time to discover new tables/fields
    for i in range(12):          # up to 60 s
        time.sleep(5)
        try:
            meta = mb.get(f"/api/database/{db_id}/metadata")
            for table in meta.get("tables", []):
                if table["name"].lower() == "bahigo_wettigo_stats":
                    print(f"  Table bahigo_wettigo_stats found after ~{(i+1)*5}s")
                    return
        except Exception:
            pass
    print("  Sync wait timed out — proceeding anyway")


def find_field_id(mb, db_id, table_name, field_name):
    """Try multiple Metabase API endpoints to find a field's numeric ID."""
    def _search():
        try:
            fields = mb.get(f"/api/database/{db_id}/fields")
            for f in fields:
                if (f.get("table_name", "").lower() == table_name
                        and f.get("name", "").lower() == field_name):
                    print(f"  {table_name}.{field_name} field id={f['id']}")
                    return f["id"]
        except Exception:
            pass
        try:
            meta = mb.get(f"/api/database/{db_id}/metadata")
            for table in meta.get("tables", []):
                if table["name"].lower() == table_name:
                    for field in table.get("fields", []):
                        if field["name"].lower() == field_name:
                            print(f"  {table_name}.{field_name} field id={field['id']}")
                            return field["id"]
        except Exception:
            pass
        try:
            tables = mb.get("/api/table")
            for t in tables:
                if t["name"].lower() == table_name and t.get("db_id") == db_id:
                    tmeta = mb.get(f"/api/table/{t['id']}/query_metadata")
                    for field in tmeta.get("fields", []):
                        if field["name"].lower() == field_name:
                            print(f"  {table_name}.{field_name} field id={field['id']}")
                            return field["id"]
        except Exception:
            pass
        return None

    result = _search()
    if result is not None:
        return result

    # Table not yet known to Metabase — trigger a sync and retry once
    print(f"  Field not found; triggering DB sync …")
    sync_database(mb, db_id)
    result = _search()
    if result is not None:
        return result

    raise RuntimeError(
        f"Could not find field '{field_name}' in table '{table_name}' "
        "even after syncing. Check that data has been loaded into the table."
    )


def configure_field_for_dropdown(mb, field_id):
    """Tell Metabase to scan & cache values so the filter shows a dropdown."""
    mb.put(f"/api/field/{field_id}", json={"has_field_values": "list"})
    mb.post(f"/api/field/{field_id}/rescan_values")
    print(f"  Field {field_id}: has_field_values=list, rescan triggered.")


def existing_cards(mb):
    active = mb.get("/api/card")
    try:
        archived = mb.get("/api/card?archived=true")
    except Exception:
        archived = []
    return {c["name"]: c["id"] for c in (active + archived)}


def existing_dashboards(mb):
    return {d["name"]: d["id"] for d in mb.get("/api/dashboard") if not d.get("archived")}


def _extract_tags(sql: str) -> dict:
    tags = {}
    for name in re.findall(r"\{\{(\w+)\}\}", sql):
        tags[name] = {
            "id":           name,
            "name":         name,
            "display-name": name.replace("_", " ").title(),
            "type":         "text",
        }
    for name in re.findall(r"\{\{(\w*date\w*)\}\}", sql, re.IGNORECASE):
        tags[name] = {
            "id":           name,
            "name":         name,
            "display-name": name.replace("_", " ").title(),
            "type":         "date",
        }
    return tags


def _build_dim_tags(sql: str, geo_field_id: int, aff_field_id: int) -> dict:
    """
    Build template-tags for a card.  geo and affiliate_name become field filters
    (type=dimension) so Metabase generates the WHERE clause and shows a dropdown.
    All other tags are auto-extracted as text / date variables.
    """
    tags = _extract_tags(sql)
    if "geo" in tags:
        tags["geo"] = {
            "id":           "geo",
            "name":         "geo",
            "display-name": "Geo",
            "type":         "dimension",
            "dimension":    ["field", geo_field_id, None],
            "widget-type":  "string/=",
        }
    if "affiliate_name" in tags:
        tags["affiliate_name"] = {
            "id":           "affiliate_name",
            "name":         "affiliate_name",
            "display-name": "Affiliate Name",
            "type":         "dimension",
            "dimension":    ["field", aff_field_id, None],
            "widget-type":  "string/=",
        }
    return tags


def upsert_card(mb, db_id, name, sql, display, viz_settings, existing, tags=None):
    payload = {
        "name":    name,
        "display": display,
        "dataset_query": {
            "type":     "native",
            "database": db_id,
            "native": {
                "query":         sql,
                "template-tags": tags if tags is not None else _extract_tags(sql),
            },
        },
        "visualization_settings": viz_settings or {},
        "archived": False,
    }
    if name in existing:
        card_id = existing[name]
        mb.put(f"/api/card/{card_id}", json=payload)
        print(f"  [updated] {name} (id={card_id})")
        return card_id
    else:
        card = mb.post("/api/card", json=payload)
        print(f"  [created] {name} (id={card['id']})")
        return card["id"]


# ──────────────────────────────────────────────
# SQL queries
# ──────────────────────────────────────────────

def build_queries(brand: str) -> dict:
    B = brand

    # geo and affiliate_name use dimension (field) filters.
    # Metabase generates the full "column = value" SQL from {{geo}} / {{affiliate_name}}.
    # Do NOT write geo = {{geo}} — the column reference is injected automatically.
    WHERE = f"""
    WHERE brand_name = '{B}'
      [[AND {{{{geo}}}}]]
      [[AND {{{{affiliate_name}}}}]]
      [[AND report_date   >= {{{{from_date}}}}]]
      [[AND report_date   <= {{{{to_date}}}}]]"""

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
                   SUM(first_depositors)       AS ftds,
                   SUM(registrations)          AS regs,
                   ROUND(SUM(net_revenue),  0) AS ngr,
                   ROUND(SUM(total_reward), 0) AS commission,
                   ROUND(SUM(first_depositors)*100.0/NULLIF(SUM(registrations),0),1) AS ftd_rate_pct
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY affiliate_name
            ORDER BY ftds DESC
            LIMIT 15
        """,
        "top_affiliates_signup": f"""
            SELECT affiliate_name,
                   SUM(registrations)          AS regs,
                   SUM(first_depositors)       AS ftds,
                   ROUND(SUM(net_revenue),  0) AS ngr,
                   ROUND(SUM(first_depositors)*100.0/NULLIF(SUM(registrations),0),1) AS ftd_rate_pct
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY affiliate_name
            ORDER BY regs DESC
            LIMIT 15
        """,
        "top_affiliates_clicks": f"""
            SELECT affiliate_name,
                   SUM(clicks)                 AS clicks,
                   SUM(registrations)          AS regs,
                   SUM(first_depositors)       AS ftds,
                   ROUND(SUM(first_depositors)*100.0/NULLIF(SUM(registrations),0),1) AS ftd_rate_pct
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY affiliate_name
            ORDER BY clicks DESC
            LIMIT 15
        """,
        "clicks_by_geo": f"""
            SELECT geo, SUM(clicks) AS clicks
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY geo
            ORDER BY clicks DESC
        """,
        "regs_by_geo": f"""
            SELECT geo, SUM(registrations) AS regs
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY geo
            ORDER BY regs DESC
        """,
        "scorecard": f"""
            SELECT affiliate_name,
                   geo,
                   SUM(clicks)                 AS clicks,
                   SUM(registrations)          AS regs,
                   SUM(first_depositors)       AS ftds,
                   ROUND(SUM(net_revenue),  0) AS ngr,
                   ROUND(SUM(total_reward), 0) AS commission,
                   ROUND(SUM(first_depositors)*100.0/NULLIF(SUM(registrations),0),1) AS ftd_rate_pct,
                   ROUND(SUM(net_revenue)/NULLIF(SUM(first_depositors),0),0)         AS ngr_per_ftd
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY affiliate_name, geo
            ORDER BY ftds DESC
        """,
        "scorecard_daily": f"""
            SELECT report_date,
                   affiliate_name,
                   geo,
                   SUM(clicks)                 AS clicks,
                   SUM(registrations)          AS regs,
                   SUM(first_depositors)       AS ftds,
                   ROUND(SUM(net_revenue),  0) AS ngr,
                   ROUND(SUM(total_reward), 0) AS commission,
                   ROUND(SUM(first_depositors)*100.0/NULLIF(SUM(registrations),0),1) AS ftd_rate_pct
            FROM bahigo_wettigo_stats
            {WHERE}
            GROUP BY report_date, affiliate_name, geo
            ORDER BY report_date DESC, ftds DESC
        """,
    }


# ──────────────────────────────────────────────
# Dashboard builder
# ──────────────────────────────────────────────

def _params(brand):
    B = brand.lower()[:2]
    return [
        {"id": f"{B}-from-date",      "name": "From Date", "slug": "from_date",      "type": "date/single"},
        {"id": f"{B}-to-date",        "name": "To Date",   "slug": "to_date",        "type": "date/single"},
        {"id": f"{B}-geo",            "name": "Geo",       "slug": "geo",            "type": "string/="},
        {"id": f"{B}-affiliate-name", "name": "Affiliate", "slug": "affiliate_name", "type": "string/="},
    ]


def _all_maps(param_ids, card_id):
    from_id, to_id, geo_id, aff_id = param_ids
    return [
        {"parameter_id": from_id, "card_id": card_id, "target": ["variable",  ["template-tag", "from_date"]]},
        {"parameter_id": to_id,   "card_id": card_id, "target": ["variable",  ["template-tag", "to_date"]]},
        {"parameter_id": geo_id,  "card_id": card_id, "target": ["dimension", ["template-tag", "geo"]]},
        {"parameter_id": aff_id,  "card_id": card_id, "target": ["dimension", ["template-tag", "affiliate_name"]]},
    ]


def build_dashcards(cards, param_ids):
    layout = [
        # Row 0: KPI scalars
        ("ftds",              0,  0,  4,  4),
        ("regs",              0,  4,  4,  4),
        ("clicks",            0,  8,  4,  4),
        ("ngr",               0, 12,  4,  4),
        ("commission",        0, 16,  4,  4),
        # Row 1: Daily FTDs trend
        ("daily",             4,  0, 20,  6),
        # Row 2: FTDs and NGR by Geo
        ("ftds_geo",         10,  0, 10,  7),
        ("ngr_geo",          10, 10, 10,  7),
        # Row 2b: Clicks and Signups by Geo
        ("clicks_geo",       17,  0, 10,  7),
        ("regs_geo",         17, 10, 10,  7),

        # Row 3: Top Affiliates — FTDs | Signups | Clicks  (3 charts)
        ("top_aff",          24,  0,  7,  8),
        ("top_aff_signup",   24,  7,  7,  8),
        ("top_aff_clicks",   24, 14,  6,  8),
        # Row 4: Full scorecard (aggregated across date range)
        ("scorecard",        32,  0, 20, 10),
        # Row 5: Daily scorecard (one row per day — filter to a single date for day view)
        ("scorecard_daily",  42,  0, 20, 10),
    ]
    dashcards = []
    for idx, (key, row, col, size_x, size_y) in enumerate(layout):
        dashcards.append({
            "id":                    -(idx + 1),
            "card_id":                cards[key],
            "row":                    row,
            "col":                    col,
            "size_x":                 size_x,
            "size_y":                 size_y,
            "parameter_mappings":     _all_maps(param_ids, cards[key]),
            "visualization_settings": {},
        })
    return dashcards


def build_dashboard(mb, db_id, brand):
    dash_name = f"{brand} Performance Overview"
    print(f"\nBuilding dashboard: {dash_name}")

    # Resolve field IDs needed for dimension (dropdown) filters
    print("  Finding field IDs for dropdown filters …")
    geo_field_id = find_field_id(mb, db_id, "bahigo_wettigo_stats", "geo")
    aff_field_id = find_field_id(mb, db_id, "bahigo_wettigo_stats", "affiliate_name")

    # Tell Metabase to scan and cache values for both fields (enables the dropdown)
    configure_field_for_dropdown(mb, geo_field_id)
    configure_field_for_dropdown(mb, aff_field_id)

    queries   = build_queries(brand)
    params    = _params(brand)
    param_ids = [p["id"] for p in params]

    print("  Upserting cards...")
    existing = existing_cards(mb)

    def t(sql):
        return _build_dim_tags(sql, geo_field_id, aff_field_id)

    num_viz   = {"number.style": "decimal"}
    num_money = {"number.style": "currency", "currency": "EUR", "currency_style": "symbol"}

    geo_bar  = lambda m: {"graph.dimensions": ["geo"],            "graph.metrics": [m]}
    aff_bar  = lambda m: {"graph.dimensions": ["affiliate_name"], "graph.metrics": [m]}

    cards = {
        "ftds":             upsert_card(mb, db_id, f"{brand} — FTDs",                      queries["ftds_scalar"],           "scalar", num_viz,        existing, tags=t(queries["ftds_scalar"])),
        "regs":             upsert_card(mb, db_id, f"{brand} — Registrations",              queries["regs_scalar"],           "scalar", num_viz,        existing, tags=t(queries["regs_scalar"])),
        "clicks":           upsert_card(mb, db_id, f"{brand} — Clicks",                     queries["clicks_scalar"],         "scalar", num_viz,        existing, tags=t(queries["clicks_scalar"])),
        "ngr":              upsert_card(mb, db_id, f"{brand} — NGR",                        queries["ngr_scalar"],            "scalar", num_money,      existing, tags=t(queries["ngr_scalar"])),
        "commission":       upsert_card(mb, db_id, f"{brand} — Commission",                 queries["commission_scalar"],     "scalar", num_money,      existing, tags=t(queries["commission_scalar"])),
        "daily":            upsert_card(mb, db_id, f"{brand} — Daily FTDs Trend",           queries["daily_ftds"],            "line",   {"graph.dimensions": ["report_date"], "graph.metrics": ["ftds"]}, existing, tags=t(queries["daily_ftds"])),
        "ftds_geo":         upsert_card(mb, db_id, f"{brand} — FTDs by Geo",                queries["ftds_by_geo"],           "row",    geo_bar("ftds"),  existing, tags=t(queries["ftds_by_geo"])),
        "ngr_geo":          upsert_card(mb, db_id, f"{brand} — NGR by Geo",                 queries["ngr_by_geo"],            "row",    geo_bar("ngr"),   existing, tags=t(queries["ngr_by_geo"])),
        "clicks_geo":       upsert_card(mb, db_id, f"{brand} — Clicks by Geo",              queries["clicks_by_geo"],         "row",    geo_bar("clicks"),existing, tags=t(queries["clicks_by_geo"])),
        "regs_geo":         upsert_card(mb, db_id, f"{brand} — Signups by Geo",             queries["regs_by_geo"],           "row",    geo_bar("regs"),  existing, tags=t(queries["regs_by_geo"])),
        "top_aff":          upsert_card(mb, db_id, f"{brand} — Top Affiliates by FTDs",     queries["top_affiliates"],        "row",    aff_bar("ftds"),  existing, tags=t(queries["top_affiliates"])),
        "top_aff_signup":   upsert_card(mb, db_id, f"{brand} — Top Affiliates by Signups",  queries["top_affiliates_signup"], "row",    aff_bar("regs"),  existing, tags=t(queries["top_affiliates_signup"])),
        "top_aff_clicks":   upsert_card(mb, db_id, f"{brand} — Top Affiliates by Clicks",   queries["top_affiliates_clicks"], "row",    aff_bar("clicks"),existing, tags=t(queries["top_affiliates_clicks"])),
        "scorecard":        upsert_card(mb, db_id, f"{brand} — Full Scorecard",             queries["scorecard"],             "table",  {},              existing, tags=t(queries["scorecard"])),
        "scorecard_daily":  upsert_card(mb, db_id, f"{brand} — Daily Scorecard",            queries["scorecard_daily"],       "table",  {},              existing, tags=t(queries["scorecard_daily"])),
    }

    print(f"  Cards ready: {list(cards.keys())}")

    existing_dashes = existing_dashboards(mb)
    if dash_name in existing_dashes:
        dash_id = existing_dashes[dash_name]
        print(f"  Updating existing dashboard (id={dash_id})")
        mb.put(f"/api/dashboard/{dash_id}", json={"parameters": params, "dashcards": []})
    else:
        dash    = mb.post("/api/dashboard", json={"name": dash_name, "parameters": params})
        dash_id = dash["id"]
        print(f"  Created dashboard (id={dash_id})")

    dashcards = build_dashcards(cards, param_ids)
    mb.put(f"/api/dashboard/{dash_id}", json={"parameters": params, "dashcards": dashcards})
    print(f"  {len(dashcards)} cards wired.")
    print(f"  Dashboard ready: {mb.host}/dashboard/{dash_id}")
    return dash_id


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Create/update a Bahigo or Wettigo dashboard")
    p.add_argument("--brand",    required=True, choices=["Bahigo", "Wettigo"])
    p.add_argument("--host",     default="http://localhost:3001")
    p.add_argument("--user",     required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--db-name",  default="netrefer")
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
