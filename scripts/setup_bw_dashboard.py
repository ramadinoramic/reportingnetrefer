#!/usr/bin/env python3
"""
setup_bw_dashboard.py  —  Bahigo or Wettigo brand dashboard in Metabase.

Creates (or updates in-place) a dashboard for a single brand showing:
  Row 0 – KPI scalars: FTDs, Registrations, Clicks, NGR, Commission
  Row 1 – Daily FTDs trend (line)
  Row 2 – FTDs by Geo (horizontal bar)  |  NGR by Geo (horizontal bar)
  Row 3 – Top Affiliates by FTDs (horizontal bar)
  Row 4 – Full affiliate performance table

Filters: From Date, To Date, Geo (dropdown), Affiliate Name

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


def find_field_id(mb, db_id, table_name, field_name):
    """Look up Metabase's internal field ID for table_name.field_name."""
    try:
        fields = mb.get(f"/api/database/{db_id}/fields")
        for f in fields:
            if (f.get("table_name", "").lower() == table_name
                    and f.get("name", "").lower() == field_name):
                print(f"  {table_name}.{field_name} field id={f['id']} (via /database/fields)")
                return f["id"]
    except Exception:
        pass
    try:
        meta = mb.get(f"/api/database/{db_id}/metadata")
        for table in meta.get("tables", []):
            if table["name"].lower() == table_name:
                for field in table.get("fields", []):
                    if field["name"].lower() == field_name:
                        print(f"  {table_name}.{field_name} field id={field['id']} (via /database/metadata)")
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
                        print(f"  {table_name}.{field_name} field id={field['id']} (via /table/query_metadata)")
                        return field["id"]
    except Exception:
        pass
    raise RuntimeError(
        f"Could not find field '{field_name}' in table '{table_name}'.\n"
        "Run Admin → Databases → Sync database schema now, then retry."
    )


def configure_field_for_dropdown(mb, field_id):
    mb.put(f"/api/field/{field_id}", json={"has_field_values": "list"})
    mb.post(f"/api/field/{field_id}/rescan_values")
    print(f"  Field {field_id}: has_field_values=list, rescan triggered.")


def existing_cards(mb):
    """Return {card_name: card_id} for all active and archived cards."""
    active = mb.get("/api/card")
    try:
        archived = mb.get("/api/card?archived=true")
    except Exception:
        archived = []
    return {c["name"]: c["id"] for c in (active + archived)}


def existing_dashboards(mb):
    """Return {dashboard_name: dashboard_id} for all non-archived dashboards."""
    return {d["name"]: d["id"] for d in mb.get("/api/dashboard") if not d.get("archived")}


def _extract_tags(sql: str) -> dict:
    """Build Metabase template-tag dicts for every {{var}} in sql."""
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


def _build_tags(sql: str, geo_field_id=None) -> dict:
    """Extract template tags, upgrading geo to a field-filter dimension when possible."""
    tags = _extract_tags(sql)
    if geo_field_id and "geo" in tags:
        tags["geo"] = {
            "id":           "geo",
            "name":         "geo",
            "display-name": "Geo",
            "type":         "dimension",
            "dimension":    ["field", geo_field_id, None],
            "widget-type":  "string/=",
            "required":     False,
        }
    return tags


def upsert_card(mb, db_id, name, sql, display, viz_settings, existing,
                geo_field_id=None):
    """Create or update a native SQL card; returns its id."""
    payload = {
        "name":    name,
        "display": display,
        "dataset_query": {
            "type":     "native",
            "database": db_id,
            "native": {
                "query":         sql,
                "template-tags": _build_tags(sql, geo_field_id),
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
# SQL queries (brand_name is baked-in per brand)
# ──────────────────────────────────────────────

def build_queries(brand: str, geo_dim: bool = False) -> dict:
    """
    Build SQL query strings for all dashboard cards.

    geo_dim=True  → use [[AND {{geo}}]] (field-filter dimension; Metabase
                    injects the equality SQL and shows a dropdown).
    geo_dim=False → use [[AND geo = {{geo}}]] (plain text variable).
    """
    B = brand   # e.g. 'Bahigo' or 'Wettigo'

    geo_clause = "[[AND {{geo}}]]" if geo_dim else "[[AND geo = '{{geo}}']]"

    WHERE = f"""
    WHERE brand_name = '{B}'
      {geo_clause}
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

def _params(brand, geo_field_id=None):
    """Dashboard-level filter parameters (fixed IDs so re-runs are stable)."""
    B = brand.lower()[:2]   # 'ba' or 'we'
    # Use string/= (dropdown) for geo when the field is configured; else category (text input)
    geo_type = "string/=" if geo_field_id else "category"
    return [
        {"id": f"{B}-from-date",      "name": "From Date",  "slug": "from_date",      "type": "date/single"},
        {"id": f"{B}-to-date",        "name": "To Date",    "slug": "to_date",        "type": "date/single"},
        {"id": f"{B}-geo",            "name": "Geo",        "slug": "geo",            "type": geo_type},
        {"id": f"{B}-affiliate-name", "name": "Affiliate",  "slug": "affiliate_name", "type": "category"},
    ]


def _all_maps(param_ids, card_id, use_geo_dim=False):
    from_id, to_id, geo_id, aff_id = param_ids
    geo_target = (
        ["dimension", ["template-tag", "geo"]]
        if use_geo_dim
        else ["variable", ["template-tag", "geo"]]
    )
    return [
        {"parameter_id": from_id, "card_id": card_id, "target": ["variable",  ["template-tag", "from_date"]]},
        {"parameter_id": to_id,   "card_id": card_id, "target": ["variable",  ["template-tag", "to_date"]]},
        {"parameter_id": geo_id,  "card_id": card_id, "target": geo_target},
        {"parameter_id": aff_id,  "card_id": card_id, "target": ["variable",  ["template-tag", "affiliate_name"]]},
    ]


def build_dashcards(cards, param_ids, use_geo_dim=False):
    """Return the dashcards list for PUT /api/dashboard/{id}."""
    # (key, row, col, size_x, size_y)
    layout = [
        ("ftds",       0,  0,  4,  4),
        ("regs",       0,  4,  4,  4),
        ("clicks",     0,  8,  4,  4),
        ("ngr",        0, 12,  4,  4),
        ("commission", 0, 16,  4,  4),
        ("daily",      4,  0, 20,  6),
        ("ftds_geo",  10,  0, 10,  7),
        ("ngr_geo",   10, 10, 10,  7),
        ("top_aff",   17,  0, 20,  8),
        ("scorecard", 25,  0, 20, 10),
    ]

    dashcards = []
    for idx, (key, row, col, size_x, size_y) in enumerate(layout):
        card_id = cards[key]
        dashcards.append({
            "id":                    -(idx + 1),   # negative = new placement
            "card_id":                card_id,
            "row":                    row,
            "col":                    col,
            "size_x":                 size_x,
            "size_y":                 size_y,
            "parameter_mappings":     _all_maps(param_ids, card_id, use_geo_dim),
            "visualization_settings": {},
        })
    return dashcards


def build_dashboard(mb, db_id, brand, geo_field_id=None):
    dash_name    = f"{brand} Performance Overview"
    use_geo_dim  = geo_field_id is not None
    print(f"\nBuilding dashboard: {dash_name}  (geo dropdown: {'yes' if use_geo_dim else 'no'})")

    queries  = build_queries(brand, geo_dim=use_geo_dim)
    params   = _params(brand, geo_field_id)
    param_ids = [p["id"] for p in params]   # [from_id, to_id, geo_id, aff_id]

    # ── Upsert cards ──────────────────────────────────────────────────────
    print("  Upserting cards...")
    existing = existing_cards(mb)

    num_viz   = {"number.style": "decimal", "column_settings": {}}
    num_money = {"number.style": "currency", "currency": "EUR", "currency_style": "symbol"}

    kw = dict(existing=existing, geo_field_id=geo_field_id)
    cards = {
        "ftds":       upsert_card(mb, db_id, f"{brand} — FTDs",              queries["ftds_scalar"],       "scalar", num_viz,   **kw),
        "regs":       upsert_card(mb, db_id, f"{brand} — Registrations",     queries["regs_scalar"],       "scalar", num_viz,   **kw),
        "clicks":     upsert_card(mb, db_id, f"{brand} — Clicks",            queries["clicks_scalar"],     "scalar", num_viz,   **kw),
        "ngr":        upsert_card(mb, db_id, f"{brand} — NGR",               queries["ngr_scalar"],        "scalar", num_money, **kw),
        "commission": upsert_card(mb, db_id, f"{brand} — Commission",        queries["commission_scalar"], "scalar", num_money, **kw),
        "daily":      upsert_card(mb, db_id, f"{brand} — Daily FTDs Trend",  queries["daily_ftds"],        "line",   {
            "graph.dimensions": ["report_date"], "graph.metrics": ["ftds"],
        }, **kw),
        "ftds_geo":   upsert_card(mb, db_id, f"{brand} — FTDs by Geo",      queries["ftds_by_geo"],       "row",    {
            "graph.dimensions": ["geo"], "graph.metrics": ["ftds"],
        }, **kw),
        "ngr_geo":    upsert_card(mb, db_id, f"{brand} — NGR by Geo",       queries["ngr_by_geo"],        "row",    {
            "graph.dimensions": ["geo"], "graph.metrics": ["ngr"],
        }, **kw),
        "top_aff":    upsert_card(mb, db_id, f"{brand} — Top Affiliates by FTDs", queries["top_affiliates"], "row", {
            "graph.dimensions": ["affiliate_name"], "graph.metrics": ["ftds"],
        }, **kw),
        "scorecard":  upsert_card(mb, db_id, f"{brand} — Full Scorecard",   queries["scorecard"],         "table",  {}, **kw),
    }

    print(f"  Cards ready: {list(cards.keys())}")

    # ── Create or update dashboard ────────────────────────────────────────
    existing_dashes = existing_dashboards(mb)
    if dash_name in existing_dashes:
        dash_id = existing_dashes[dash_name]
        print(f"  Updating existing dashboard '{dash_name}' (id={dash_id}) — URL preserved")
        mb.put(f"/api/dashboard/{dash_id}", json={"parameters": params, "dashcards": []})
    else:
        print(f"  Creating new dashboard '{dash_name}'")
        dash    = mb.post("/api/dashboard", json={"name": dash_name, "parameters": params})
        dash_id = dash["id"]

    # ── Wire cards to dashboard in one PUT ───────────────────────────────
    dashcards = build_dashcards(cards, param_ids, use_geo_dim=use_geo_dim)
    mb.put(f"/api/dashboard/{dash_id}", json={
        "parameters": params,
        "dashcards":  dashcards,
    })
    print(f"  {len(dashcards)} cards wired.")
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

    # Configure geo field as a dropdown (requires table to be synced in Metabase)
    geo_field_id = None
    try:
        geo_field_id = find_field_id(mb, db_id, "bahigo_wettigo_stats", "geo")
        configure_field_for_dropdown(mb, geo_field_id)
    except Exception as e:
        print(f"  [warn] Could not configure geo dropdown: {e}")
        print("  → Geo filter will be a text input. Re-run after Metabase syncs the table.")

    build_dashboard(mb, db_id, args.brand, geo_field_id=geo_field_id)
    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
