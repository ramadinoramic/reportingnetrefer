#!/usr/bin/env python3
"""
setup_affiliate_dashboard.py  –  Creates a filterable Affiliate Deep Dive dashboard.

Filters:
  • Affiliate Name  (dropdown – field filter on affiliate_name)
  • From Date       (date/single picker  → report_date >=)
  • To Date         (date/single picker  → report_date <=)

Usage:
    python scripts/setup_affiliate_dashboard.py \
        --host http://localhost:3000 \
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
            snippet = e.read().decode(errors="replace")[:300]
            raise RuntimeError(f"HTTP {e.code} {e.reason} on {method} {path}: {snippet}")

    def get(self, path, **_):
        return self._raw("GET", path)

    def post(self, path, **kw):
        return self._raw("POST", path, kw.get("json"))

    def put(self, path, **kw):
        return self._raw("PUT", path, kw.get("json"))

    def delete(self, path, **_):
        self._raw("DELETE", path)


def find_database(mb, name_fragment):
    dbs = mb.get("/api/database")
    items = dbs if isinstance(dbs, list) else dbs.get("data", [])
    for db in items:
        if name_fragment.lower() in db["name"].lower():
            return db["id"]
    raise RuntimeError(f"No database matching '{name_fragment}'")


def find_field_id(mb, db_id, table_name, field_name):
    """Return the Metabase field ID for table.field, trying three endpoints."""
    # 1. /api/database/{id}/fields  (flat list, older versions)
    try:
        fields = mb.get(f"/api/database/{db_id}/fields")
        for f in fields:
            if (f.get("table_name", "").lower() == table_name
                    and f.get("name", "").lower() == field_name):
                print(f"  {table_name}.{field_name} field id={f['id']} (via /database/fields)")
                return f["id"]
    except Exception:
        pass

    # 2. /api/database/{id}/metadata  (nested tables→fields)
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

    # 3. Walk /api/table list, then fetch query_metadata for the right table
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
    """Tell Metabase to cache all distinct values so the dropdown auto-loads."""
    mb.put(f"/api/field/{field_id}", json={"has_field_values": "list"})
    mb.post(f"/api/field/{field_id}/rescan_values")
    print(f"  Field {field_id}: has_field_values=list, rescan triggered.")


def configure_date_field(mb, field_id):
    """Ensure Metabase treats this field as a Date so date field-filters work."""
    try:
        mb.put(f"/api/field/{field_id}", json={"base_type": "type/Date"})
        print(f"  Field {field_id}: base_type set to type/Date.")
    except Exception as e:
        print(f"  [warn] Could not set base_type for field {field_id}: {e}")


def db_engine(mb, db_id):
    """Return the lowercase engine string for the database (e.g. 'postgres', 'mysql', 'h2')."""
    try:
        info = mb.get(f"/api/database/{db_id}")
        return info.get("engine", "postgres").lower()
    except Exception:
        return "postgres"


def last_7_days_expr(engine):
    """SQL expression for 'date 7 days ago', adapted to the DB engine."""
    if engine in ("h2", "sqlite"):
        return "date('now', '-7 days')"
    if engine in ("mysql", "mariadb"):
        return "DATE_SUB(CURDATE(), INTERVAL 7 DAY)"
    if engine in ("sqlserver",):
        return "DATEADD(day, -7, CAST(GETDATE() AS DATE))"
    # postgres, redshift, snowflake, bigquery, etc.
    return "CURRENT_DATE - INTERVAL '7 days'"


def existing_cards(mb):
    return {c["name"]: c["id"] for c in mb.get("/api/card")}


def existing_dashboards(mb):
    return {d["name"]: d["id"] for d in mb.get("/api/dashboard")}


# ──────────────────────────────────────────────
# Parameter IDs (fixed so re-runs are stable)
# ──────────────────────────────────────────────
PARAM_AFFILIATE  = "a1b2c3d4-0001-0001-0001-000000000001"
PARAM_FROM_DATE  = "a1b2c3d4-0002-0002-0002-000000000002"
PARAM_TO_DATE    = "a1b2c3d4-0003-0003-0003-000000000003"


def template_tags(affiliate_field_id):
    """
    affiliate_name  – field filter (Metabase auto-generates the equality SQL).
    from_date / to_date – simple date variables; Metabase substitutes the
      chosen date as a quoted 'YYYY-MM-DD' string directly into the SQL.
      This is more reliable than a date/range field filter because it does
      not depend on Metabase knowing the column's base_type.
    """
    return {
        "affiliate_name": {
            "id":           "tt-affiliate",
            "name":         "affiliate_name",
            "display-name": "Affiliate Name",
            "type":         "dimension",
            "dimension":    ["field", affiliate_field_id, None],
            "widget-type":  "string/=",
            "required":     False,
        },
        "from_date": {
            "id":           "tt-from-date",
            "name":         "from_date",
            "display-name": "From Date",
            "type":         "date",
            "required":     False,
        },
        "to_date": {
            "id":           "tt-to-date",
            "name":         "to_date",
            "display-name": "To Date",
            "type":         "date",
            "required":     False,
        },
    }


def param_mappings(card_id):
    """Parameter→template-tag mappings for every filterable card."""
    return [
        {
            "parameter_id": PARAM_AFFILIATE,
            "card_id":      card_id,
            "target":       ["dimension", ["template-tag", "affiliate_name"]],
        },
        {
            # Simple (non-field-filter) variables use ["variable", ...] target
            "parameter_id": PARAM_FROM_DATE,
            "card_id":      card_id,
            "target":       ["variable", ["template-tag", "from_date"]],
        },
        {
            "parameter_id": PARAM_TO_DATE,
            "card_id":      card_id,
            "target":       ["variable", ["template-tag", "to_date"]],
        },
    ]


# ──────────────────────────────────────────────
# Card definitions
# ──────────────────────────────────────────────

def native(db_id, sql, affiliate_field_id):
    return {
        "type":     "native",
        "database": db_id,
        "native":   {
            "query":         sql,
            "template-tags": template_tags(affiliate_field_id),
        },
    }


def native_fixed(db_id, sql):
    """Native query with no template tags (no dashboard filters apply)."""
    return {
        "type":     "native",
        "database": db_id,
        "native":   {
            "query":         sql,
            "template-tags": {},
        },
    }


# affiliate_name is a field filter  → [[AND {{affiliate_name}}]] is dropped when not set
# from_date / to_date are date vars → Metabase may inject ISO 8601 with time/tz suffix;
# DATE() strips any time component so MySQL compares cleanly against the DATE column.
WHERE = """
    WHERE 1=1
    [[AND {{affiliate_name}}]]
    [[AND report_date >= DATE({{from_date}})]]
    [[AND report_date <= DATE({{to_date}})]]
"""

def card_defs(db_id, affiliate_field_id, engine="postgres"):
    def q(sql):
        return native(db_id, sql, affiliate_field_id)

    def q_fixed(sql):
        return native_fixed(db_id, sql)

    seven_days_ago = last_7_days_expr(engine)

    return [
        # ── KPI scalars ──────────────────────────────────────────────────
        {
            "name":    "AF – Clicks",
            "display": "scalar",
            "dataset_query": q(f"""
                SELECT COALESCE(SUM(clicks), 0) AS clicks
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },
        {
            "name":    "AF – Registrations",
            "display": "scalar",
            "dataset_query": q(f"""
                SELECT COALESCE(SUM(registrations), 0) AS registrations
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },
        {
            "name":    "AF – FTDs",
            "display": "scalar",
            "dataset_query": q(f"""
                SELECT COALESCE(SUM(first_depositors), 0) AS ftds
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },
        {
            "name":    "AF – Net Revenue",
            "display": "scalar",
            "dataset_query": q(f"""
                SELECT ROUND(COALESCE(SUM(net_revenue), 0), 2) AS net_revenue
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {"number.style": "currency", "currency": "EUR"},
        },
        {
            "name":    "AF – Deposits",
            "display": "scalar",
            "dataset_query": q(f"""
                SELECT ROUND(COALESCE(SUM(deposits), 0), 2) AS deposits
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {"number.style": "currency", "currency": "EUR"},
        },
        # ── Daily trend ───────────────────────────────────────────────────
        {
            "name":    "AF – Daily Revenue Trend",
            "display": "line",
            "dataset_query": q(f"""
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
            "dataset_query": q(f"""
                SELECT
                    report_date,
                    SUM(clicks)           AS clicks,
                    SUM(registrations)    AS registrations,
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
            "dataset_query": q(f"""
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
            "dataset_query": q(f"""
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
            "dataset_query": q(f"""
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
        # ── Top 10 affiliates – last 7 days (fixed, no dashboard filters) ─
        {
            "name":    "AF – Top 10 Affiliates (Last 7 Days)",
            "display": "table",
            "no_params": True,
            "dataset_query": q_fixed(f"""
                SELECT
                    affiliate_name,
                    SUM(clicks)                                     AS clicks,
                    SUM(registrations)                              AS signups,
                    SUM(first_depositors)                           AS ftds,
                    ROUND(SUM(deposits),     2)                     AS deposits,
                    ROUND(SUM(net_revenue),  2)                     AS net_revenue,
                    CASE WHEN SUM(clicks) > 0
                         THEN ROUND(SUM(registrations) * 100.0 / SUM(clicks), 2)
                         ELSE 0
                    END                                             AS click_to_reg_pct,
                    CASE WHEN SUM(registrations) > 0
                         THEN ROUND(SUM(first_depositors) * 100.0 / SUM(registrations), 2)
                         ELSE 0
                    END                                             AS reg_to_ftd_pct
                FROM netrefer_stats
                WHERE report_date >= {seven_days_ago}
                GROUP BY affiliate_name
                ORDER BY ftds DESC
                LIMIT 10
            """),
            "visualization_settings": {},
        },
        # ── Affiliate summary — one row per affiliate, totals for selected range ──
        # Use this when no affiliate filter is set: see all affiliates side-by-side.
        {
            "name":    "AF – Affiliate Summary",
            "display": "table",
            "dataset_query": q(f"""
                SELECT
                    affiliate_name,
                    SUM(clicks)                                     AS clicks,
                    SUM(registrations)                              AS signups,
                    SUM(first_depositors)                           AS ftds,
                    ROUND(SUM(deposits),     2)                     AS deposits,
                    ROUND(SUM(net_revenue),  2)                     AS net_revenue,
                    CASE WHEN SUM(clicks) > 0
                         THEN ROUND(SUM(registrations) * 100.0 / SUM(clicks), 2)
                         ELSE 0
                    END                                             AS click_to_reg_pct,
                    CASE WHEN SUM(registrations) > 0
                         THEN ROUND(SUM(first_depositors) * 100.0 / SUM(registrations), 2)
                         ELSE 0
                    END                                             AS reg_to_ftd_pct
                FROM netrefer_stats {WHERE}
                GROUP BY affiliate_name
                ORDER BY ftds DESC, net_revenue DESC
            """),
            "visualization_settings": {},
        },
        # ── Daily detail table — one row per affiliate per day ───────────
        # Use this after picking a single affiliate: see day-by-day breakdown.
        {
            "name":    "AF – Daily Detail Table",
            "display": "table",
            "dataset_query": q(f"""
                SELECT
                    report_date,
                    affiliate_name,
                    SUM(clicks)                                     AS clicks,
                    SUM(registrations)                              AS signups,
                    SUM(first_depositors)                           AS ftds,
                    ROUND(SUM(deposits),     2)                     AS deposits,
                    ROUND(SUM(net_revenue),  2)                     AS net_revenue,
                    CASE WHEN SUM(clicks) > 0
                         THEN ROUND(SUM(registrations) * 100.0 / SUM(clicks), 2)
                         ELSE 0
                    END                                             AS click_to_reg_pct,
                    CASE WHEN SUM(registrations) > 0
                         THEN ROUND(SUM(first_depositors) * 100.0 / SUM(registrations), 2)
                         ELSE 0
                    END                                             AS reg_to_ftd_pct
                FROM netrefer_stats {WHERE}
                GROUP BY report_date, affiliate_name
                ORDER BY report_date DESC, ftds DESC, net_revenue DESC
            """),
            "visualization_settings": {},
        },
    ]


# ──────────────────────────────────────────────
# Dashboard layout
# ──────────────────────────────────────────────

LAYOUT = [
    # name,                                    row, col, size_x, size_y
    ("AF – Clicks",                               0,  0,  4, 3),
    ("AF – Registrations",                        0,  4,  4, 3),
    ("AF – FTDs",                                 0,  8,  4, 3),
    ("AF – Net Revenue",                          0, 12,  4, 3),
    ("AF – Deposits",                             0, 16,  4, 3),
    ("AF – Daily Revenue Trend",                  3,  0, 12, 7),
    ("AF – Daily Conversions",                    3, 12, 12, 7),
    ("AF – Conversion Funnel",                   10,  0,  8, 7),
    ("AF – Revenue by Campaign",                 10,  8,  8, 7),
    ("AF – Revenue by Country",                  10, 16,  8, 7),
    ("AF – Affiliate Summary",                   17,  0, 24, 8),
    ("AF – Daily Detail Table",                  25,  0, 24, 8),
    ("AF – Top 10 Affiliates (Last 7 Days)",     33,  0, 24, 8),
]


def build_dashcards(card_name_to_id, card_no_params, card_detail_params):
    dashcards = []
    for idx, (name, row, col, size_x, size_y) in enumerate(LAYOUT):
        card_id = card_name_to_id.get(name)
        if card_id is None:
            print(f"  [warn] card '{name}' not found, skipping")
            continue
        mappings = [] if name in card_no_params else param_mappings(card_id)
        dashcards.append({
            "id":                      -(idx + 1),
            "card_id":                  card_id,
            "row":                      row,
            "col":                      col,
            "size_x":                   size_x,
            "size_y":                   size_y,
            "parameter_mappings":       mappings,
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
    parser.add_argument("--timezone", default="Europe/Istanbul",
                        help="Metabase report-timezone (default: Europe/Istanbul / UTC+3)")
    args = parser.parse_args()

    print(f"Connecting to {args.host} …")
    mb = MetabaseClient(args.host, args.user, args.password)
    print("  Logged in.")

    # Fix date-picker timezone: Metabase runs in UTC but users are in a different
    # timezone. Without this, picking "March 10" sends March 9 to the SQL.
    try:
        mb.put("/api/setting/report-timezone", json={"value": args.timezone})
        print(f"  Timezone → {args.timezone}")
    except Exception as e:
        print(f"  [warn] Could not set timezone: {e}")

    db_id = find_database(mb, args.db_name)
    print(f"  Database id={db_id}")

    # Look up field IDs for filters
    affiliate_field_id = find_field_id(mb, db_id, "netrefer_stats", "affiliate_name")
    configure_field_for_dropdown(mb, affiliate_field_id)
    engine = db_engine(mb, db_id)
    seven_days_sql = last_7_days_expr(engine)
    print(f"  DB engine: {engine}  →  last-7-days expr: {seven_days_sql}")

    # Upsert all cards (always PUT existing ones so SQL + template-tags stay current)
    existing = existing_cards(mb)
    card_name_to_id  = {}
    card_no_params   = set()
    card_detail_params = set()
    print(f"\nUpserting cards …")
    for card in card_defs(db_id, affiliate_field_id, engine):
        name = card["name"]
        if card.get("no_params"):
            card_no_params.add(name)
        if card.get("detail_params"):
            card_detail_params.add(name)
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

    # Dashboard parameters
    dashboard_params = [
        {"id": PARAM_AFFILIATE,  "name": "Affiliate Name", "slug": "affiliate_name", "type": "string/="},
        {"id": PARAM_FROM_DATE,  "name": "From Date",      "slug": "from_date",      "type": "date/single"},
        {"id": PARAM_TO_DATE,    "name": "To Date",        "slug": "to_date",        "type": "date/single"},
    ]

    # Delete the existing dashboard so stale dashcard/parameter mappings are cleared
    dash_name = "Affiliate Deep Dive"
    existing_dashes = existing_dashboards(mb)

    if dash_name in existing_dashes:
        old_id = existing_dashes[dash_name]
        print(f"\n[deleting] Old dashboard '{dash_name}' (id={old_id}) to clear stale state …")
        mb.put(f"/api/dashboard/{old_id}", json={"archived": True})
        print(f"  Archived.")

    # Always create a fresh dashboard so parameter mappings are clean
    dash = mb.post("/api/dashboard", json={
        "name":        dash_name,
        "description": "Per-affiliate KPIs, trends and breakdown — use filters to drill down",
        "parameters":  dashboard_params,
    })
    dash_id = dash["id"]
    print(f"\n[created] Dashboard '{dash_name}' (id={dash_id})")

    dashcards = build_dashcards(card_name_to_id, card_no_params, card_detail_params)
    print("  Wiring cards …")
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
