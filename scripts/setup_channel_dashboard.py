#!/usr/bin/env python3
"""
setup_channel_dashboard.py  –  Creates the Channel & Affiliate Overview dashboard.

Layout (matches wireframe):
  Row 0 – KPI scorecards: Max Date, Clicks, Regs, FTDs, GGR, NGR
  Row 1 – Line charts: Clicks by Day | Regs by Day | FTDs by Day
  Row 2 – Horizontal bars: Clicks/Regs/FTDs by Channel
  Row 3 – Horizontal bars: GGR by Channel | NGR by Channel
  Row 4 – Affiliate Detail Table

Filters: Date From, Date To, Channel (campaign_name), Affiliate Name

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
    """Tell Metabase to cache all distinct values so the dropdown auto-loads."""
    mb.put(f"/api/field/{field_id}", json={"has_field_values": "list"})
    mb.post(f"/api/field/{field_id}/rescan_values")
    print(f"  Field {field_id}: has_field_values=list, rescan triggered.")


def existing_cards(mb):
    active   = mb.get("/api/card")
    try:
        archived = mb.get("/api/card?archived=true")
    except Exception:
        archived = []
    return {c["name"]: c["id"] for c in (active + archived)}


def existing_dashboards(mb):
    return {d["name"]: d["id"] for d in mb.get("/api/dashboard")}


# ──────────────────────────────────────────────
# Parameter IDs (fixed so re-runs are stable)
# ──────────────────────────────────────────────
PARAM_FROM_DATE  = "ch-0001-0001-0001-000000000001"
PARAM_TO_DATE    = "ch-0002-0002-0002-000000000002"
PARAM_CHANNEL    = "ch-0003-0003-0003-000000000003"
PARAM_AFFILIATE  = "ch-0004-0004-0004-000000000004"


def template_tags(channel_field_id, affiliate_field_id):
    return {
        "from_date": {
            "id":           "ch-tt-from-date",
            "name":         "from_date",
            "display-name": "From Date",
            "type":         "date",
            "required":     False,
        },
        "to_date": {
            "id":           "ch-tt-to-date",
            "name":         "to_date",
            "display-name": "To Date",
            "type":         "date",
            "required":     False,
        },
        "channel": {
            "id":           "ch-tt-channel",
            "name":         "channel",
            "display-name": "Channel",
            "type":         "dimension",
            "dimension":    ["field", channel_field_id, None],
            "widget-type":  "string/=",
            "required":     False,
        },
        "affiliate_name": {
            "id":           "ch-tt-affiliate",
            "name":         "affiliate_name",
            "display-name": "Affiliate",
            "type":         "dimension",
            "dimension":    ["field", affiliate_field_id, None],
            "widget-type":  "string/=",
            "required":     False,
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
            "parameter_id": PARAM_CHANNEL,
            "card_id":      card_id,
            "target":       ["dimension", ["template-tag", "channel"]],
        },
        {
            "parameter_id": PARAM_AFFILIATE,
            "card_id":      card_id,
            "target":       ["dimension", ["template-tag", "affiliate_name"]],
        },
    ]


# ──────────────────────────────────────────────
# Card definitions
# ──────────────────────────────────────────────

WHERE = """
    WHERE 1=1
    [[AND {{channel}}]]
    [[AND {{affiliate_name}}]]
    [[AND report_date >= {{from_date}}]]
    [[AND report_date <= {{to_date}}]]
"""


def card_defs(db_id, channel_field_id, affiliate_field_id):
    tags = template_tags(channel_field_id, affiliate_field_id)

    def q(sql):
        return {
            "type":     "native",
            "database": db_id,
            "native":   {"query": sql, "template-tags": tags},
        }

    return [
        # ── KPI Scorecards ────────────────────────────────────────────────
        {
            "name":    "CH – Latest Date",
            "display": "scalar",
            "dataset_query": q(f"""
                SELECT MAX(report_date) AS latest_date
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },
        {
            "name":    "CH – Total Clicks",
            "display": "scalar",
            "dataset_query": q(f"""
                SELECT COALESCE(SUM(clicks), 0) AS clicks
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },
        {
            "name":    "CH – Total Regs",
            "display": "scalar",
            "dataset_query": q(f"""
                SELECT COALESCE(SUM(registrations), 0) AS registrations
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },
        {
            "name":    "CH – Total FTDs",
            "display": "scalar",
            "dataset_query": q(f"""
                SELECT COALESCE(SUM(first_depositors), 0) AS ftds
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {},
        },
        {
            "name":    "CH – Total GGR",
            "display": "scalar",
            "dataset_query": q(f"""
                SELECT ROUND(COALESCE(SUM(gross_revenue), 0), 2) AS ggr
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {"number.style": "currency", "currency": "EUR"},
        },
        {
            "name":    "CH – Total NGR",
            "display": "scalar",
            "dataset_query": q(f"""
                SELECT ROUND(COALESCE(SUM(net_revenue), 0), 2) AS ngr
                FROM netrefer_stats {WHERE}
            """),
            "visualization_settings": {"number.style": "currency", "currency": "EUR"},
        },

        # ── Line Charts: daily trends ─────────────────────────────────────
        {
            "name":    "CH – Clicks by Day",
            "display": "line",
            "dataset_query": q(f"""
                SELECT report_date, SUM(clicks) AS clicks
                FROM netrefer_stats {WHERE}
                GROUP BY report_date
                ORDER BY report_date
            """),
            "visualization_settings": {
                "graph.dimensions": ["report_date"],
                "graph.metrics":    ["clicks"],
            },
        },
        {
            "name":    "CH – Regs by Day",
            "display": "line",
            "dataset_query": q(f"""
                SELECT report_date, SUM(registrations) AS registrations
                FROM netrefer_stats {WHERE}
                GROUP BY report_date
                ORDER BY report_date
            """),
            "visualization_settings": {
                "graph.dimensions": ["report_date"],
                "graph.metrics":    ["registrations"],
            },
        },
        {
            "name":    "CH – FTDs by Day",
            "display": "line",
            "dataset_query": q(f"""
                SELECT report_date, SUM(first_depositors) AS ftds
                FROM netrefer_stats {WHERE}
                GROUP BY report_date
                ORDER BY report_date
            """),
            "visualization_settings": {
                "graph.dimensions": ["report_date"],
                "graph.metrics":    ["ftds"],
            },
        },

        # ── Horizontal bar charts: by Channel ────────────────────────────
        {
            "name":    "CH – Clicks by Channel",
            "display": "bar",
            "dataset_query": q(f"""
                SELECT campaign_name AS channel, SUM(clicks) AS clicks
                FROM netrefer_stats {WHERE}
                  AND campaign_name != ''
                GROUP BY campaign_name
                ORDER BY clicks DESC
                LIMIT 20
            """),
            "visualization_settings": {
                "graph.dimensions": ["channel"],
                "graph.metrics":    ["clicks"],
                "graph.x_axis.scale": "ordinal",
                "stackable.stack_type": None,
            },
        },
        {
            "name":    "CH – Regs by Channel",
            "display": "bar",
            "dataset_query": q(f"""
                SELECT campaign_name AS channel, SUM(registrations) AS registrations
                FROM netrefer_stats {WHERE}
                  AND campaign_name != ''
                GROUP BY campaign_name
                ORDER BY registrations DESC
                LIMIT 20
            """),
            "visualization_settings": {
                "graph.dimensions": ["channel"],
                "graph.metrics":    ["registrations"],
            },
        },
        {
            "name":    "CH – FTDs by Channel",
            "display": "bar",
            "dataset_query": q(f"""
                SELECT campaign_name AS channel, SUM(first_depositors) AS ftds
                FROM netrefer_stats {WHERE}
                  AND campaign_name != ''
                GROUP BY campaign_name
                ORDER BY ftds DESC
                LIMIT 20
            """),
            "visualization_settings": {
                "graph.dimensions": ["channel"],
                "graph.metrics":    ["ftds"],
            },
        },
        {
            "name":    "CH – GGR by Channel",
            "display": "bar",
            "dataset_query": q(f"""
                SELECT campaign_name AS channel, ROUND(SUM(gross_revenue), 2) AS ggr
                FROM netrefer_stats {WHERE}
                  AND campaign_name != ''
                GROUP BY campaign_name
                ORDER BY ggr DESC
                LIMIT 20
            """),
            "visualization_settings": {
                "graph.dimensions": ["channel"],
                "graph.metrics":    ["ggr"],
                "number.style": "currency", "currency": "EUR",
            },
        },
        {
            "name":    "CH – NGR by Channel",
            "display": "bar",
            "dataset_query": q(f"""
                SELECT campaign_name AS channel, ROUND(SUM(net_revenue), 2) AS ngr
                FROM netrefer_stats {WHERE}
                  AND campaign_name != ''
                GROUP BY campaign_name
                ORDER BY ngr DESC
                LIMIT 20
            """),
            "visualization_settings": {
                "graph.dimensions": ["channel"],
                "graph.metrics":    ["ngr"],
                "number.style": "currency", "currency": "EUR",
            },
        },

        # ── Affiliate Detail Table ────────────────────────────────────────
        {
            "name":    "CH – Affiliate Detail Table",
            "display": "table",
            "dataset_query": q(f"""
                SELECT
                    affiliate_id                                    AS id,
                    campaign_name                                   AS channel,
                    affiliate_name                                  AS name,
                    SUM(clicks)                                     AS clicks,
                    SUM(registrations)                              AS regs,
                    SUM(first_depositors)                           AS ftds,
                    ROUND(SUM(deposits),     2)                     AS deposits,
                    ROUND(SUM(net_revenue),  2)                     AS ngr,
                    ROUND(SUM(total_reward), 2)                     AS commission
                FROM netrefer_stats {WHERE}
                GROUP BY affiliate_id, campaign_name, affiliate_name
                ORDER BY ftds DESC, ngr DESC
            """),
            "visualization_settings": {
                "table.column_formatting": [],
            },
        },
    ]


# ──────────────────────────────────────────────
# Dashboard layout
# ──────────────────────────────────────────────
# Metabase grid is 24 columns wide.

LAYOUT = [
    # name,                      row, col, size_x, size_y
    # Row 0 — KPI scorecards (6 cards × 4 cols)
    ("CH – Latest Date",           0,  0,  4, 3),
    ("CH – Total Clicks",          0,  4,  4, 3),
    ("CH – Total Regs",            0,  8,  4, 3),
    ("CH – Total FTDs",            0, 12,  4, 3),
    ("CH – Total GGR",             0, 16,  4, 3),
    ("CH – Total NGR",             0, 20,  4, 3),
    # Row 1 — Line charts (3 × 8 cols)
    ("CH – Clicks by Day",         3,  0,  8, 6),
    ("CH – Regs by Day",           3,  8,  8, 6),
    ("CH – FTDs by Day",           3, 16,  8, 6),
    # Row 2 — Horizontal bars by channel (3 × 8 cols)
    ("CH – Clicks by Channel",     9,  0,  8, 7),
    ("CH – Regs by Channel",       9,  8,  8, 7),
    ("CH – FTDs by Channel",       9, 16,  8, 7),
    # Row 3 — GGR / NGR by channel (2 × 12 cols)
    ("CH – GGR by Channel",       16,  0, 12, 7),
    ("CH – NGR by Channel",       16, 12, 12, 7),
    # Row 4 — Affiliate detail table
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

    channel_field_id   = find_field_id(mb, db_id, "netrefer_stats", "campaign_name")
    affiliate_field_id = find_field_id(mb, db_id, "netrefer_stats", "affiliate_name")

    configure_field_for_dropdown(mb, channel_field_id)
    configure_field_for_dropdown(mb, affiliate_field_id)

    # Upsert all cards
    existing = existing_cards(mb)
    card_name_to_id = {}
    print("\nUpserting cards …")
    for card in card_defs(db_id, channel_field_id, affiliate_field_id):
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

    # Dashboard parameters
    dashboard_params = [
        {"id": PARAM_FROM_DATE, "name": "From Date",  "slug": "from_date",  "type": "date/single"},
        {"id": PARAM_TO_DATE,   "name": "To Date",    "slug": "to_date",    "type": "date/single"},
        {"id": PARAM_CHANNEL,   "name": "Channel",    "slug": "channel",    "type": "string/="},
        {"id": PARAM_AFFILIATE, "name": "Affiliate",  "slug": "affiliate",  "type": "string/="},
    ]

    dash_name = "Channel & Affiliate Overview"
    existing_dashes = existing_dashboards(mb)

    if dash_name in existing_dashes:
        old_id = existing_dashes[dash_name]
        print(f"\n[archiving] Old dashboard '{dash_name}' (id={old_id}) …")
        mb.put(f"/api/dashboard/{old_id}", json={"archived": True})

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
