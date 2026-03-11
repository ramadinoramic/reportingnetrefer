#!/usr/bin/env python3
"""
setup_daily_dashboard.py  (v4)
==============================
Netrefer daily dashboard – date range + affiliate filters.

Date filter  → field filter on report_date with "date/all-options":
               supports single day, custom range, "last 7 days", "this month", etc.
               When nothing is selected it shows data for the most recently loaded date.
Affiliate    → searchable dropdown (field filter on affiliate_name)
Data Date    → always reflects the currently selected date/range

Usage (first time – Metabase not yet initialised):
  python scripts/setup_daily_dashboard.py \\
    --host http://localhost:3001 \\
    --user admin@example.com --password yourpassword --setup

Usage (Metabase already configured):
  python scripts/setup_daily_dashboard.py \\
    --host http://localhost:3001 \\
    --user admin@example.com --password yourpassword
"""

import argparse, json, sys, time, urllib.request, urllib.error

DASH_NAME     = "Netrefer Daily Report"
DATE_PARAM_ID = "nr-date-param-001"   # fixed → idempotent re-runs
AFF_PARAM_ID  = "nr-aff-param-002"
DATE_TAG_UUID = "aaaaaaaa-date-tag-001"
AFF_TAG_UUID  = "bbbbbbbb-aff--tag-002"


# ── Metabase HTTP client ─────────────────────────────────────────────────────

class MB:
    def __init__(self, host):
        self.host = host.rstrip("/")
        self.tok  = None

    def login(self, email, password):
        resp = self._raw("POST", "/api/session",
                         {"username": email, "password": password})
        self.tok = resp["id"]
        return self

    def setup(self, email, password, db_host, db_port, db_name, db_user, db_pass):
        props = self._raw("GET", "/api/session/properties")
        token = props.get("setup-token") or props.get("setup_token")
        if not token:
            print("  No setup token – already initialised. Logging in …")
            return self.login(email, password)
        payload = {
            "token": token,
            "user": {
                "email": email, "password": password,
                "first_name": "Admin", "last_name": "User",
                "site_name":  "Netrefer Reporting",
            },
            "database": {
                "engine": "mysql", "name": "Netrefer Reporting",
                "details": {
                    "host": db_host, "port": db_port,
                    "dbname": db_name, "user": db_user,
                    "password": db_pass, "ssl": False,
                },
                "auto_run_queries": True, "is_full_sync": True,
            },
            "prefs": {"site_name": "Netrefer Reporting", "allow_tracking": False},
        }
        resp = self._raw("POST", "/api/setup", payload)
        self.tok = resp.get("id") or resp.get("token")
        if not self.tok:
            self.login(email, password)
        print("  Metabase setup complete ✓")
        return self

    def _raw(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.tok:
            headers["X-Metabase-Session"] = self.tok
        req = urllib.request.Request(
            self.host + path, data=data, headers=headers, method=method)
        try:
            content = urllib.request.urlopen(req).read()
            return json.loads(content) if content else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            raise RuntimeError(f"HTTP {e.code} {method} {path}: {msg[:400]}")

    def get(self, p):          return self._raw("GET",  p)
    def post(self, p, b=None): return self._raw("POST", p, b or {})
    def put(self, p, b=None):  return self._raw("PUT",  p, b or {})


# ── Database helpers ─────────────────────────────────────────────────────────

def find_or_add_db(mb, db_host, db_port, db_name, db_user, db_pass):
    items = mb.get("/api/database")
    items = items if isinstance(items, list) else items.get("data", [])
    for db in items:
        if db_name.lower() in db["name"].lower() or \
           db_name.lower() in json.dumps(db.get("details", {})).lower():
            print(f"  Found database: {db['name']} (id={db['id']})")
            return db["id"]
    print(f"  Adding MySQL → {db_host}:{db_port}/{db_name}")
    r = mb.post("/api/database", {
        "name": "Netrefer Reporting", "engine": "mysql",
        "details": {"host": db_host, "port": db_port, "dbname": db_name,
                    "user": db_user, "password": db_pass, "ssl": False},
        "auto_run_queries": True, "is_full_sync": True,
    })
    print(f"  Added database id={r['id']}")
    return r["id"]


def wait_for_sync(mb, db_id, timeout=120):
    mb.post(f"/api/database/{db_id}/sync_schema")
    print("  Waiting for table sync", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        tables = mb.get(f"/api/database/{db_id}/metadata").get("tables", [])
        if any(t["name"] == "netrefer_stats" for t in tables):
            print(" ✓")
            return
        print(".", end="", flush=True)
        time.sleep(4)
    print()
    raise RuntimeError("Sync timed out – is netrefer_stats table created?")


def get_field_id(mb, db_id, table_name, field_name):
    meta = mb.get(f"/api/database/{db_id}/metadata")
    for table in meta.get("tables", []):
        if table["name"] == table_name:
            for field in table.get("fields", []):
                if field["name"] == field_name:
                    return field["id"]
    return None


def configure_field(mb, field_id, has_field_values, semantic_type=None):
    body = {"has_field_values": has_field_values}
    if semantic_type:
        body["semantic_type"] = semantic_type
    mb.put(f"/api/field/{field_id}", body)


# ── Template-tag builders ────────────────────────────────────────────────────
#
# Both filters use "dimension" (field filter) so Metabase:
#   • generates the SQL WHERE fragment automatically
#   • supports single-value, multi-value, ranges, relative dates
#   • dict KEY must equal the {{variable_name}} used in the SQL

def date_field_tag(date_field_id):
    """
    Field filter on report_date.
    widget-type "date/all-options" gives the user:
      – single day picker
      – date range picker
      – relative shortcuts (last 7 days, this month, …)
    SQL usage: [[AND {{date_filter}}]]
    """
    return {
        "date_filter": {
            "id":           DATE_TAG_UUID,
            "name":         "date_filter",
            "display-name": "Date",
            "type":         "dimension",
            "dimension":    ["field", date_field_id, None],
            "widget-type":  "date/all-options",
            "required":     False,
            "default":      None,
        }
    }


def aff_field_tag(aff_field_id):
    """
    Field filter on affiliate_name – searchable dropdown.
    SQL usage: [[AND {{affiliate_filter}}]]
    """
    return {
        "affiliate_filter": {
            "id":           AFF_TAG_UUID,
            "name":         "affiliate_filter",
            "display-name": "Affiliate",
            "type":         "dimension",
            "dimension":    ["field", aff_field_id, None],
            "widget-type":  "string/=",
            "required":     False,
            "default":      None,
        }
    }


def both_tags(date_field_id, aff_field_id):
    return {**date_field_tag(date_field_id), **aff_field_tag(aff_field_id)}


# ── SQL helpers ──────────────────────────────────────────────────────────────
#
# Both filters use [[AND {{var}}]] – the optional block is dropped when the
# filter has no value, so no date = full-time totals visible.
#
# For the "no date selected → show latest date" default behaviour, the
# Data Date card uses its own fallback subquery so it never shows blank.

def dc():  # date clause  (optional)
    return "[[AND {{date_filter}}]]"

def ac():  # affiliate clause (optional)
    return "[[AND {{affiliate_filter}}]]"


def sql_card(db_id, name, display, sql, tags=None, vis=None):
    return {
        "name":    name,
        "display": display,
        "dataset_query": {
            "type":     "native",
            "database": db_id,
            "native":   {"query": sql, "template-tags": tags or {}},
        },
        "visualization_settings": vis or {},
    }


def make_cards(db_id, date_field_id, aff_field_id):
    d = dc()
    a = ac()

    return [
        # ── period banner ─────────────────────────────────────────────────
        # Shows "2026-03-07" for a single day, "2026-03-01 → 2026-03-07"
        # for a range, and the overall latest date when nothing is selected.
        sql_card(db_id, "Data Date", "scalar", f"""
SELECT CASE
    WHEN MIN(report_date) = MAX(report_date)
        THEN CAST(MAX(report_date) AS CHAR)
    ELSE CONCAT(CAST(MIN(report_date) AS CHAR),
                ' → ',
                CAST(MAX(report_date) AS CHAR))
END AS `Period`
FROM netrefer_stats
WHERE 1=1 {d} {a}""",
            tags=both_tags(date_field_id, aff_field_id)),

        # ── KPI scalars ───────────────────────────────────────────────────
        sql_card(db_id, "Clicks", "scalar",
            f"SELECT SUM(clicks) FROM netrefer_stats WHERE 1=1 {d} {a}",
            tags=both_tags(date_field_id, aff_field_id)),

        sql_card(db_id, "Registrations", "scalar",
            f"SELECT SUM(registrations) FROM netrefer_stats WHERE 1=1 {d} {a}",
            tags=both_tags(date_field_id, aff_field_id)),

        sql_card(db_id, "FTDs", "scalar",
            f"SELECT SUM(first_depositors) FROM netrefer_stats WHERE 1=1 {d} {a}",
            tags=both_tags(date_field_id, aff_field_id)),

        sql_card(db_id, "Net Revenue", "scalar",
            f"SELECT ROUND(SUM(net_revenue),2) FROM netrefer_stats WHERE 1=1 {d} {a}",
            tags=both_tags(date_field_id, aff_field_id)),

        sql_card(db_id, "Deposits", "scalar",
            f"SELECT ROUND(SUM(deposits),2) FROM netrefer_stats WHERE 1=1 {d} {a}",
            tags=both_tags(date_field_id, aff_field_id)),

        # ── 30-day trend charts (date filter not applied – always 30d view;
        #    affiliate filter applied so you can zoom in on one affiliate)
        sql_card(db_id, "Daily Conversions (30d)", "line", f"""
SELECT report_date,
       SUM(clicks)           AS clicks,
       SUM(registrations)    AS registrations,
       SUM(first_depositors) AS ftds
FROM   netrefer_stats
WHERE  report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
{a}
GROUP  BY report_date
ORDER  BY report_date""",
            tags=aff_field_tag(aff_field_id),
            vis={"graph.dimensions": ["report_date"],
                 "graph.metrics":   ["clicks", "registrations", "ftds"]}),

        sql_card(db_id, "Daily Revenue (30d)", "line", f"""
SELECT report_date,
       ROUND(SUM(net_revenue),2) AS net_revenue,
       ROUND(SUM(deposits),2)    AS deposits
FROM   netrefer_stats
WHERE  report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
{a}
GROUP  BY report_date
ORDER  BY report_date""",
            tags=aff_field_tag(aff_field_id),
            vis={"graph.dimensions": ["report_date"],
                 "graph.metrics":   ["net_revenue", "deposits"]}),

        # ── breakdowns ────────────────────────────────────────────────────
        sql_card(db_id, "Revenue by Country", "pie", f"""
SELECT country, ROUND(SUM(net_revenue),2) AS net_revenue
FROM   netrefer_stats
WHERE  1=1 AND country != '' {d} {a}
GROUP  BY country
ORDER  BY net_revenue DESC""",
            tags=both_tags(date_field_id, aff_field_id),
            vis={"pie.dimension": "country", "pie.metric": "net_revenue"}),

        sql_card(db_id, "Top Affiliates by Revenue", "bar", f"""
SELECT affiliate_name, ROUND(SUM(net_revenue),2) AS net_revenue
FROM   netrefer_stats
WHERE  1=1 {d} {a}
GROUP  BY affiliate_id, affiliate_name
ORDER  BY net_revenue DESC
LIMIT  15""",
            tags=both_tags(date_field_id, aff_field_id),
            vis={"graph.dimensions": ["affiliate_name"],
                 "graph.metrics":   ["net_revenue"]}),

        sql_card(db_id, "Conversion Funnel", "bar", f"""
SELECT 'Clicks'        AS stage, SUM(clicks)            AS total FROM netrefer_stats WHERE 1=1 {d} {a}
UNION ALL
SELECT 'Registrations',           SUM(registrations)             FROM netrefer_stats WHERE 1=1 {d} {a}
UNION ALL
SELECT 'FTDs',                    SUM(first_depositors)          FROM netrefer_stats WHERE 1=1 {d} {a}""",
            tags=both_tags(date_field_id, aff_field_id),
            vis={"graph.dimensions": ["stage"], "graph.metrics": ["total"]}),

        # ── detail table ──────────────────────────────────────────────────
        sql_card(db_id, "Affiliate Detail Table", "table", f"""
SELECT affiliate_name,
       country,
       campaign_name,
       clicks,
       registrations,
       first_depositors           AS ftds,
       ROUND(deposits,    2)      AS deposits,
       ROUND(net_revenue, 2)      AS net_revenue,
       ROUND(total_reward,2)      AS commission
FROM   netrefer_stats
WHERE  1=1
  AND  (clicks > 0 OR registrations > 0
        OR first_depositors > 0 OR net_revenue != 0)
{d}
{a}
ORDER  BY net_revenue DESC""",
            tags=both_tags(date_field_id, aff_field_id)),
    ]


# ── Dashboard layout ──────────────────────────────────────────────────────────

LAYOUT = [
    # card name                      row  col   w   h
    ("Data Date",                      0,  0,   4,  2),
    ("Clicks",                         0,  4,   4,  2),
    ("Registrations",                  0,  8,   4,  2),
    ("FTDs",                           0, 12,   4,  2),
    ("Net Revenue",                    0, 16,   4,  2),
    ("Deposits",                       0, 20,   4,  2),
    ("Daily Conversions (30d)",        2,  0,  12,  6),
    ("Daily Revenue (30d)",            2, 12,  12,  6),
    ("Top Affiliates by Revenue",      8,  0,   8,  8),
    ("Revenue by Country",             8,  8,   8,  8),
    ("Conversion Funnel",              8, 16,   8,  8),
    ("Affiliate Detail Table",        16,  0,  24,  9),
]

DATE_CARDS = {
    "Data Date", "Clicks", "Registrations", "FTDs", "Net Revenue", "Deposits",
    "Revenue by Country", "Top Affiliates by Revenue",
    "Conversion Funnel", "Affiliate Detail Table",
}
AFF_CARDS = {
    "Data Date", "Clicks", "Registrations", "FTDs", "Net Revenue", "Deposits",
    "Daily Conversions (30d)", "Daily Revenue (30d)",
    "Revenue by Country", "Top Affiliates by Revenue",
    "Conversion Funnel", "Affiliate Detail Table",
}


# ── Dashboard parameters ──────────────────────────────────────────────────────

DASH_PARAMS = [
    {
        "id":        DATE_PARAM_ID,
        "type":      "date/all-options",   # single day + range + relative presets
        "name":      "Date",
        "slug":      "date_filter",
        "default":   None,
        "sectionId": "date",
    },
    {
        "id":        AFF_PARAM_ID,
        "type":      "string/=",
        "name":      "Affiliate",
        "slug":      "affiliate_filter",
        "default":   None,
        "sectionId": "string",
    },
]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host",        default="http://localhost:3001")
    p.add_argument("--user",        required=True)
    p.add_argument("--password",    required=True)
    p.add_argument("--setup",       action="store_true",
                   help="First-time Metabase setup (creates admin + DB connection)")
    p.add_argument("--db-host",     default="db")
    p.add_argument("--db-port",     default=3306,  type=int)
    p.add_argument("--db-name",     default="netrefer_reporting")
    p.add_argument("--db-user",     default="root")
    p.add_argument("--db-password", default="rootpassword")
    args = p.parse_args()

    print(f"\nConnecting to Metabase at {args.host} …")
    mb = MB(args.host)

    if args.setup:
        mb.setup(args.user, args.password,
                 args.db_host, args.db_port, args.db_name,
                 args.db_user, args.db_password)
    else:
        mb.login(args.user, args.password)
        print("  Logged in ✓")

    db_id = find_or_add_db(mb, args.db_host, args.db_port,
                           args.db_name, args.db_user, args.db_password)
    wait_for_sync(mb, db_id)

    # Look up field IDs
    print("  Looking up field IDs …", end="", flush=True)
    date_field_id = get_field_id(mb, db_id, "netrefer_stats", "report_date")
    aff_field_id  = get_field_id(mb, db_id, "netrefer_stats", "affiliate_name")
    if not date_field_id or not aff_field_id:
        sys.exit(f"\n  ERROR: field not found "
                 f"(report_date={date_field_id}, affiliate_name={aff_field_id})")
    print(f" report_date={date_field_id}  affiliate_name={aff_field_id} ✓")

    # Configure fields for Metabase
    configure_field(mb, date_field_id, "none")           # date col – no value list needed
    configure_field(mb, aff_field_id,  "search", "type/Name")  # dropdown search
    print("  Field settings applied ✓")

    # ── upsert cards ──────────────────────────────────────────────────────
    print("\nUpserting cards …")
    existing = {c["name"]: c["id"] for c in mb.get("/api/card")}
    name_to_id = {}

    for card in make_cards(db_id, date_field_id, aff_field_id):
        name = card["name"]
        payload = {k: card[k] for k in
                   ("name", "display", "dataset_query", "visualization_settings")}
        if name in existing:
            cid = existing[name]
            mb.put(f"/api/card/{cid}", payload)
            name_to_id[name] = cid
            print(f"  [updated] {name}")
        else:
            r = mb.post("/api/card", payload)
            name_to_id[name] = r["id"]
            print(f"  [created] {name} (id={r['id']})")

    # ── archive old, create fresh dashboard ───────────────────────────────
    print("\nBuilding dashboard …")
    for d in mb.get("/api/dashboard"):
        if d["name"] == DASH_NAME and not d.get("archived"):
            mb.put(f"/api/dashboard/{d['id']}", {"archived": True})
            print(f"  Archived old dashboard id={d['id']}")

    dash = mb.post("/api/dashboard", {
        "name":        DASH_NAME,
        "description": "Drop a CSV → ETL loads it → pick a date or range to filter.",
        "parameters":  DASH_PARAMS,
    })
    dash_id = dash["id"]

    dashcards = []
    for i, (name, row, col, sx, sy) in enumerate(LAYOUT):
        cid = name_to_id.get(name)
        if cid is None:
            continue

        mappings = []
        if name in DATE_CARDS:
            mappings.append({
                "parameter_id": DATE_PARAM_ID,
                "card_id":      cid,
                "target":       ["dimension", ["template-tag", "date_filter"]],
            })
        if name in AFF_CARDS:
            mappings.append({
                "parameter_id": AFF_PARAM_ID,
                "card_id":      cid,
                "target":       ["dimension", ["template-tag", "affiliate_filter"]],
            })

        dashcards.append({
            "id": -(i + 1), "card_id": cid,
            "row": row, "col": col, "size_x": sx, "size_y": sy,
            "parameter_mappings":     mappings,
            "visualization_settings": {},
        })

    try:
        mb.put(f"/api/dashboard/{dash_id}", {
            "parameters": DASH_PARAMS,
            "dashcards":  dashcards,
        })
    except Exception:
        mb.post(f"/api/dashboard/{dash_id}/dashcards", {"cards": dashcards})

    print(f"\n{'='*56}")
    print(f"  Done!  Open: {args.host}/dashboard/{dash_id}")
    print()
    print(f"  Filters:")
    print(f"    Date     → single day | custom range | 'last 7 days' | etc.")
    print(f"    Affiliate → searchable dropdown")
    print()
    print(f"  Daily workflow:")
    print(f"    1. Drop netrefer_YYYY-MM-DD.csv into drop/")
    print(f"    2. Wait ~60s for the ETL watcher")
    print(f"    3. Refresh the page – pick the new date to see it")
    print(f"{'='*56}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
