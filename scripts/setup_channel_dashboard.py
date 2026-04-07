#!/usr/bin/env python3
"""
setup_channel_dashboard.py  –  Creates the Channel & Affiliate Overview dashboard.

Changes vs original:
  - CHANNEL filter now uses affiliate_id → channel group mapping
    (CPA/CPL, Direct, MB in-house, MB outsourced, SEO, Influencers, Social,
     unattributed, Affiliates) instead of raw campaign_name
  - All "by Channel" histograms use display="row" (horizontal bars)

Layout:
  Row 0 – KPI scorecards: Date, Clicks, Regs, FTDs, GGR, NGR
  Row 1 – Line charts: Clicks / Regs / FTDs by Day
  Row 2 – Horizontal bars: Clicks / Regs / FTDs by Channel
  Row 3 – Horizontal bars: GGR / NGR by Channel
  Row 4 – Affiliate Detail Table

Filters: From Date, To Date, Channel (group dropdown), Affiliate Name

Usage:
    python scripts/setup_channel_dashboard.py \
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
    active = mb.get("/api/card")
    try:
        archived = mb.get("/api/card?archived=true")
    except Exception:
        archived = []
    return {c["name"]: c["id"] for c in (active + archived)}


def existing_dashboards(mb):
    return {d["name"]: d["id"] for d in mb.get("/api/dashboard")}


# ──────────────────────────────────────────────
# Channel grouping (affiliate_id → channel name)
# ──────────────────────────────────────────────

CHANNEL_CASE = """CASE
    WHEN affiliate_id IN ('660062','660060','660052','659813','659787','659836','659921','659933','660005','659819','659989','659838','659943','660039','659848','660007','659772','659844','660010','659786','659818','659730','659233','659861','659804','659803','660003','659725','660027','659952','659864','660029','659929','659783','659713','660094','659815','659807') THEN 'CPA/CPL'
    WHEN affiliate_id IN ('657238','657239') THEN 'Direct'
    WHEN affiliate_id IN ('660116','660117','659660','659699','659637','659876','659891','660018','659593','659594','659595','659909','659923','659561','660172','660174') THEN 'MB in-house'
    WHEN affiliate_id IN ('659873','659481','659839','660032','656618','660138','660137','659831','656062','660084','659462','659757') THEN 'MB outsourced'
    WHEN affiliate_id IN ('660108','660109','660110','657236') THEN 'SEO'
    WHEN affiliate_id IN ('660064','660074','660077','660079','660085','660087','660091','660114','660050','660043','659906','658393','657523','659926','659888','659898','660015','660014','660118','659567','660148','660162','660141','660124','660147','660179','660178','660151','660183','660184','660185','660186','660187','660133') THEN 'Influencers'
    WHEN affiliate_id IN ('657237','659271','659752') THEN 'Social'
    WHEN affiliate_id IN ('0','659086') THEN 'unattributed'
    ELSE 'Affiliates'
END"""


# ──────────────────────────────────────────────
# Parameter IDs (fixed so re-runs are stable)
# ──────────────────────────────────────────────

PARAM_FROM_DATE = "ch-0001-0001-0001-000000000001"
PARAM_TO_DATE   = "ch-0002-0002-0002-000000000002"
PARAM_CHANNEL   = "ch-0003-0003-0003-000000000003"
PARAM_AFFILIATE = "ch-0004-0004-0004-000000000004"


def template_tags(affiliate_field_id):
    """
    channel    – plain text variable; SQL filters on the CASE expression value.
    affiliate  – field filter (dimension); Metabase generates equality SQL.
    from/to    – simple date variables.
    """
    return {
        "from_date": {
            "id": "ch-tt-from-date", "name": "from_date",
            "display-name": "From Date", "type": "date", "required": False,
        },
        "to_date": {
            "id": "ch-tt-to-date", "name": "to_date",
            "display-name": "To Date", "type": "date", "required": False,
        },
        # text variable — value injected literally into SQL as '{{channel}}'
        "channel": {
            "id": "ch-tt-channel", "name": "channel",
            "display-name": "Channel", "type": "text", "required": False,
        },
        "affiliate_name": {
            "id": "ch-tt-affiliate", "name": "affiliate_name",
            "display-name": "Affiliate", "type": "dimension",
            "dimension": ["field", affiliate_field_id, None],
            "widget-type": "string/=", "required": False,
        },
    }


def param_mappings(card_id):
    return [
        {
            "parameter_id": PARAM_FROM_DATE,
            "card_id":      card_id,
            "target":       ["variable", ["template-tag", "from_date"]],
        },
        {
            "parameter_id": PARAM_TO_DATE,
            "card_id":      card_id,
            "target":       ["variable", ["template-tag", "to_date"]],
        },
        {
            # text variable — not a dimension
            "parameter_id": PARAM_CHANNEL,
            "card_id":      card_id,
            "target":       ["variable", ["template-tag", "channel"]],
        },
        {
            "parameter_id": PARAM_AFFILIATE,
            "card_id":      card_id,
            "target":       ["dimension", ["template-tag", "affiliate_name"]],
        },
    ]


# ──────────────────────────────────────────────
# WHERE clause
# channel filter wraps the CASE expression so it compares the computed
# group name against the text value the user picks in the filter.
# Single quotes in SQL handle the string comparison; Metabase injects
# the raw text value of {{channel}} between them.
# ──────────────────────────────────────────────

WHERE = (
    "\n    WHERE 1=1"
    "\n    [[AND {{affiliate_name}}]]"
    "\n    [[AND report_date >= {{from_date}}]]"
    "\n    [[AND report_date <= {{to_date}}]]"
    "\n    [[AND " + CHANNEL_CASE + " = '{{channel}}']]"
)


# ──────────────────────────────────────────────
# Card definitions
# ──────────────────────────────────────────────

def card_defs(db_id, affiliate_field_id):
    tags = template_tags(affiliate_field_id)

    def q(sql):
        return {
            "type":     "native",
            "database": db_id,
            "native":   {"query": sql, "template-tags": tags},
        }

    return [
        # ── KPI Scorecards ────────────────────────────────────────────────
        {
            "name": "CH – Latest Date", "display": "scalar",
            "dataset_query": q("SELECT MAX(report_date) AS latest_date FROM netrefer_stats" + WHERE),
            "visualization_settings": {},
        },
        {
            "name": "CH – Total Clicks", "display": "scalar",
            "dataset_query": q("SELECT COALESCE(SUM(clicks), 0) AS clicks FROM netrefer_stats" + WHERE),
            "visualization_settings": {},
        },
        {
            "name": "CH – Total Regs", "display": "scalar",
            "dataset_query": q("SELECT COALESCE(SUM(registrations), 0) AS registrations FROM netrefer_stats" + WHERE),
            "visualization_settings": {},
        },
        {
            "name": "CH – Total FTDs", "display": "scalar",
            "dataset_query": q("SELECT COALESCE(SUM(first_depositors), 0) AS ftds FROM netrefer_stats" + WHERE),
            "visualization_settings": {},
        },
        {
            "name": "CH – Total GGR", "display": "scalar",
            "dataset_query": q("SELECT ROUND(COALESCE(SUM(gross_revenue), 0), 2) AS ggr FROM netrefer_stats" + WHERE),
            "visualization_settings": {"number.style": "currency", "currency": "EUR"},
        },
        {
            "name": "CH – Total NGR", "display": "scalar",
            "dataset_query": q("SELECT ROUND(COALESCE(SUM(net_revenue), 0), 2) AS ngr FROM netrefer_stats" + WHERE),
            "visualization_settings": {"number.style": "currency", "currency": "EUR"},
        },

        # ── Line Charts ───────────────────────────────────────────────────
        {
            "name": "CH – Clicks by Day", "display": "line",
            "dataset_query": q(
                "SELECT report_date, SUM(clicks) AS clicks"
                " FROM netrefer_stats" + WHERE +
                " GROUP BY report_date ORDER BY report_date"
            ),
            "visualization_settings": {
                "graph.dimensions": ["report_date"], "graph.metrics": ["clicks"],
            },
        },
        {
            "name": "CH – Regs by Day", "display": "line",
            "dataset_query": q(
                "SELECT report_date, SUM(registrations) AS registrations"
                " FROM netrefer_stats" + WHERE +
                " GROUP BY report_date ORDER BY report_date"
            ),
            "visualization_settings": {
                "graph.dimensions": ["report_date"], "graph.metrics": ["registrations"],
            },
        },
        {
            "name": "CH – FTDs by Day", "display": "line",
            "dataset_query": q(
                "SELECT report_date, SUM(first_depositors) AS ftds"
                " FROM netrefer_stats" + WHERE +
                " GROUP BY report_date ORDER BY report_date"
            ),
            "visualization_settings": {
                "graph.dimensions": ["report_date"], "graph.metrics": ["ftds"],
            },
        },

        # ── Horizontal bar charts by Channel group ────────────────────────
        # display="row" = horizontal bars in Metabase
        {
            "name": "CH – Clicks by Channel", "display": "row",
            "dataset_query": q(
                "SELECT " + CHANNEL_CASE + " AS channel, SUM(clicks) AS clicks"
                " FROM netrefer_stats" + WHERE +
                " GROUP BY channel ORDER BY clicks DESC"
            ),
            "visualization_settings": {
                "graph.dimensions": ["channel"], "graph.metrics": ["clicks"],
            },
        },
        {
            "name": "CH – Regs by Channel", "display": "row",
            "dataset_query": q(
                "SELECT " + CHANNEL_CASE + " AS channel, SUM(registrations) AS registrations"
                " FROM netrefer_stats" + WHERE +
                " GROUP BY channel ORDER BY registrations DESC"
            ),
            "visualization_settings": {
                "graph.dimensions": ["channel"], "graph.metrics": ["registrations"],
            },
        },
        {
            "name": "CH – FTDs by Channel", "display": "row",
            "dataset_query": q(
                "SELECT " + CHANNEL_CASE + " AS channel, SUM(first_depositors) AS ftds"
                " FROM netrefer_stats" + WHERE +
                " GROUP BY channel ORDER BY ftds DESC"
            ),
            "visualization_settings": {
                "graph.dimensions": ["channel"], "graph.metrics": ["ftds"],
            },
        },
        {
            "name": "CH – GGR by Channel", "display": "row",
            "dataset_query": q(
                "SELECT " + CHANNEL_CASE + " AS channel, ROUND(SUM(gross_revenue), 2) AS ggr"
                " FROM netrefer_stats" + WHERE +
                " GROUP BY channel ORDER BY ggr DESC"
            ),
            "visualization_settings": {
                "graph.dimensions": ["channel"], "graph.metrics": ["ggr"],
                "number.style": "currency", "currency": "EUR",
            },
        },
        {
            "name": "CH – NGR by Channel", "display": "row",
            "dataset_query": q(
                "SELECT " + CHANNEL_CASE + " AS channel, ROUND(SUM(net_revenue), 2) AS ngr"
                " FROM netrefer_stats" + WHERE +
                " GROUP BY channel ORDER BY ngr DESC"
            ),
            "visualization_settings": {
                "graph.dimensions": ["channel"], "graph.metrics": ["ngr"],
                "number.style": "currency", "currency": "EUR",
            },
        },

        # ── Affiliate Detail Table ────────────────────────────────────────
        {
            "name": "CH – Affiliate Detail Table", "display": "table",
            "dataset_query": q(
                "SELECT"
                "    affiliate_id                                 AS id,"
                "    " + CHANNEL_CASE + "                        AS channel,"
                "    affiliate_name                               AS name,"
                "    SUM(clicks)                                  AS clicks,"
                "    SUM(registrations)                           AS regs,"
                "    SUM(first_depositors)                        AS ftds,"
                "    ROUND(SUM(deposits),    2)                   AS deposits,"
                "    ROUND(SUM(net_revenue), 2)                   AS ngr,"
                "    ROUND(SUM(total_reward),2)                   AS commission"
                " FROM netrefer_stats" + WHERE +
                " GROUP BY affiliate_id, channel, affiliate_name"
                " ORDER BY ftds DESC, ngr DESC"
            ),
            "visualization_settings": {"table.column_formatting": []},
        },
    ]


# ──────────────────────────────────────────────
# Dashboard layout
# ──────────────────────────────────────────────

LAYOUT = [
    # name,                      row, col, size_x, size_y
    ("CH – Latest Date",           0,  0,  4, 3),
    ("CH – Total Clicks",          0,  4,  4, 3),
    ("CH – Total Regs",            0,  8,  4, 3),
    ("CH – Total FTDs",            0, 12,  4, 3),
    ("CH – Total GGR",             0, 16,  4, 3),
    ("CH – Total NGR",             0, 20,  4, 3),
    ("CH – Clicks by Day",         3,  0,  8, 6),
    ("CH – Regs by Day",           3,  8,  8, 6),
    ("CH – FTDs by Day",           3, 16,  8, 6),
    ("CH – Clicks by Channel",     9,  0,  8, 7),
    ("CH – Regs by Channel",       9,  8,  8, 7),
    ("CH – FTDs by Channel",       9, 16,  8, 7),
    ("CH – GGR by Channel",       16,  0, 12, 7),
    ("CH – NGR by Channel",       16, 12, 12, 7),
    ("CH – Affiliate Detail Table",23,  0, 24, 9),
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
        print(f"  Timezone → {args.timezone}")
    except Exception as e:
        print(f"  [warn] Could not set timezone: {e}")

    db_id = find_database(mb, args.db_name)
    print(f"  Database id={db_id}")

    # Only affiliate_name needs a field-filter dropdown now
    affiliate_field_id = find_field_id(mb, db_id, "netrefer_stats", "affiliate_name")
    configure_field_for_dropdown(mb, affiliate_field_id)

    existing = existing_cards(mb)
    card_name_to_id = {}
    print("\nUpserting cards …")
    for card in card_defs(db_id, affiliate_field_id):
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
        {"id": PARAM_FROM_DATE, "name": "From Date",  "slug": "from_date",  "type": "date/single"},
        {"id": PARAM_TO_DATE,   "name": "To Date",    "slug": "to_date",    "type": "date/single"},
        # category type gives a searchable text input in the dashboard filter bar
        {"id": PARAM_CHANNEL,   "name": "Channel",    "slug": "channel",    "type": "category"},
        {"id": PARAM_AFFILIATE, "name": "Affiliate",  "slug": "affiliate",  "type": "string/="},
    ]

    dash_name = "Channel & Affiliate Overview"
    existing_dashes = existing_dashboards(mb)
    if dash_name in existing_dashes:
        dash_id = existing_dashes[dash_name]
        print(f"\n[updating] Dashboard '{dash_name}' in-place (id={dash_id}) — URL stays the same")
        # Clear existing dashcards first so stale cards/mappings are removed
        mb.put(f"/api/dashboard/{dash_id}", json={"parameters": dashboard_params, "dashcards": []})
    else:
        dash = mb.post("/api/dashboard", json={
            "name":        dash_name,
            "description": "Channel and affiliate performance — KPIs, daily trends, channel breakdowns",
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
