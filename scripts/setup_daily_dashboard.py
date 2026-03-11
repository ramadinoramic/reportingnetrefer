#!/usr/bin/env python3
"""
setup_daily_dashboard.py
========================
Creates (or rebuilds) a simple Netrefer daily dashboard in Metabase.

Workflow:
  1. Drop  netrefer_YYYY-MM-DD.csv  into the  drop/  folder
  2. The ETL watcher loads it automatically into MySQL
  3. Open this dashboard – it always shows the most-recently-loaded date

No date-filter widget.  No timezone issues.  Just works.

Usage (first time – Metabase needs initial setup):
  python scripts/setup_daily_dashboard.py \\
      --host http://localhost:3001 \\
      --user admin@example.com \\
      --password yourpassword \\
      --setup

Usage (Metabase already configured):
  python scripts/setup_daily_dashboard.py \\
      --host http://localhost:3001 \\
      --user admin@example.com \\
      --password yourpassword
"""

import argparse, json, sys, time, urllib.request, urllib.error

DASH_NAME = "Netrefer Daily Report"

# ── Metabase HTTP client ────────────────────────────────────────────────────

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
        """First-time Metabase setup: creates admin user + DB connection."""
        props = self._raw("GET", "/api/session/properties")
        token = props.get("setup-token") or props.get("setup_token")
        if not token:
            print("  Metabase already set up (no setup token) – skipping setup step.")
            return self.login(email, password)

        payload = {
            "token": token,
            "user": {
                "email":      email,
                "password":   password,
                "first_name": "Admin",
                "last_name":  "User",
                "site_name":  "Netrefer Reporting",
            },
            "database": {
                "engine": "mysql",
                "name":   "Netrefer Reporting",
                "details": {
                    "host":     db_host,
                    "port":     db_port,
                    "dbname":   db_name,
                    "user":     db_user,
                    "password": db_pass,
                    "ssl":      False,
                },
                "auto_run_queries": True,
                "is_full_sync":     True,
            },
            "prefs": {"site_name": "Netrefer Reporting", "allow_tracking": False},
        }
        resp = self._raw("POST", "/api/setup", payload)
        self.tok = resp.get("id") or resp.get("token")
        if not self.tok:
            # setup succeeded but session wasn't returned, log in normally
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
            resp = urllib.request.urlopen(req)
            content = resp.read()
            return json.loads(content) if content else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            raise RuntimeError(f"HTTP {e.code} {method} {path}: {msg[:400]}")

    def get(self, p):           return self._raw("GET",  p)
    def post(self, p, b=None):  return self._raw("POST", p, b or {})
    def put(self, p, b=None):   return self._raw("PUT",  p, b or {})


# ── Database helpers ────────────────────────────────────────────────────────

def find_or_add_db(mb, db_host, db_port, db_name, db_user, db_pass):
    dbs   = mb.get("/api/database")
    items = dbs if isinstance(dbs, list) else dbs.get("data", [])
    for db in items:
        details = json.dumps(db.get("details", {})).lower()
        if db_name.lower() in db["name"].lower() or db_name.lower() in details:
            print(f"  Found database: {db['name']} (id={db['id']})")
            return db["id"]

    print(f"  Adding MySQL connection → {db_host}:{db_port}/{db_name}")
    r = mb.post("/api/database", {
        "name":   "Netrefer Reporting",
        "engine": "mysql",
        "details": {
            "host":     db_host,
            "port":     db_port,
            "dbname":   db_name,
            "user":     db_user,
            "password": db_pass,
            "ssl":      False,
        },
        "auto_run_queries": True,
        "is_full_sync":     True,
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


# ── Card definitions ────────────────────────────────────────────────────────

LATEST = "(SELECT MAX(report_date) FROM netrefer_stats)"

def sql_card(db_id, name, display, sql, vis=None):
    return {
        "name":    name,
        "display": display,
        "dataset_query": {
            "type":     "native",
            "database": db_id,
            "native":   {"query": sql, "template-tags": {}},
        },
        "visualization_settings": vis or {},
    }


def make_cards(db_id):
    L = LATEST
    return [
        # ── date banner ──────────────────────────────────────────────────
        sql_card(db_id,
            "Data Date",
            "scalar",
            f"SELECT MAX(report_date) AS `Showing data for` FROM netrefer_stats",
        ),
        # ── KPIs for the latest loaded date ─────────────────────────────
        sql_card(db_id, "Clicks",        "scalar",
            f"SELECT SUM(clicks)          FROM netrefer_stats WHERE report_date = {L}"),
        sql_card(db_id, "Registrations", "scalar",
            f"SELECT SUM(registrations)   FROM netrefer_stats WHERE report_date = {L}"),
        sql_card(db_id, "FTDs",          "scalar",
            f"SELECT SUM(first_depositors) FROM netrefer_stats WHERE report_date = {L}"),
        sql_card(db_id, "Net Revenue",   "scalar",
            f"SELECT ROUND(SUM(net_revenue),2) FROM netrefer_stats WHERE report_date = {L}"),
        sql_card(db_id, "Deposits",      "scalar",
            f"SELECT ROUND(SUM(deposits),2)   FROM netrefer_stats WHERE report_date = {L}"),

        # ── 30-day trends ────────────────────────────────────────────────
        sql_card(db_id, "Daily Conversions (30d)", "line", """
            SELECT report_date,
                   SUM(clicks)           AS clicks,
                   SUM(registrations)    AS registrations,
                   SUM(first_depositors) AS ftds
            FROM netrefer_stats
            WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY report_date ORDER BY report_date
        """, {"graph.dimensions": ["report_date"],
               "graph.metrics":   ["clicks", "registrations", "ftds"]}),

        sql_card(db_id, "Daily Revenue (30d)", "line", """
            SELECT report_date,
                   ROUND(SUM(net_revenue),2) AS net_revenue,
                   ROUND(SUM(deposits),2)    AS deposits
            FROM netrefer_stats
            WHERE report_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY report_date ORDER BY report_date
        """, {"graph.dimensions": ["report_date"],
               "graph.metrics":   ["net_revenue", "deposits"]}),

        # ── breakdowns for the latest date ───────────────────────────────
        sql_card(db_id, "Revenue by Country", "pie", f"""
            SELECT country, ROUND(SUM(net_revenue),2) AS net_revenue
            FROM netrefer_stats
            WHERE report_date = {L} AND country != ''
            GROUP BY country ORDER BY net_revenue DESC
        """, {"pie.dimension": "country", "pie.metric": "net_revenue"}),

        sql_card(db_id, "Top Affiliates by Revenue", "bar", f"""
            SELECT affiliate_name, ROUND(SUM(net_revenue),2) AS net_revenue
            FROM netrefer_stats
            WHERE report_date = {L}
            GROUP BY affiliate_id, affiliate_name
            ORDER BY net_revenue DESC LIMIT 15
        """, {"graph.dimensions": ["affiliate_name"],
               "graph.metrics":   ["net_revenue"]}),

        sql_card(db_id, "Conversion Funnel", "bar", f"""
            SELECT 'Clicks'        AS stage, SUM(clicks)           AS total FROM netrefer_stats WHERE report_date = {L}
            UNION ALL
            SELECT 'Registrations',           SUM(registrations)             FROM netrefer_stats WHERE report_date = {L}
            UNION ALL
            SELECT 'FTDs',                    SUM(first_depositors)          FROM netrefer_stats WHERE report_date = {L}
        """, {"graph.dimensions": ["stage"], "graph.metrics": ["total"]}),

        # ── full affiliate table for the latest date ─────────────────────
        sql_card(db_id, "Affiliate Detail Table", "table", f"""
            SELECT
                affiliate_name,
                country,
                campaign_name,
                clicks,
                registrations,
                first_depositors           AS ftds,
                ROUND(deposits,    2)      AS deposits,
                ROUND(net_revenue, 2)      AS net_revenue,
                ROUND(total_reward,2)      AS commission
            FROM netrefer_stats
            WHERE report_date = {L}
              AND (clicks > 0 OR registrations > 0
                   OR first_depositors > 0 OR net_revenue != 0)
            ORDER BY net_revenue DESC
        """),
    ]


# ── Dashboard layout (24-col grid) ──────────────────────────────────────────

LAYOUT = [
    # name                          row  col  w   h
    ("Data Date",                     0,  0,  4,  2),
    ("Clicks",                        0,  4,  4,  2),
    ("Registrations",                 0,  8,  4,  2),
    ("FTDs",                          0, 12,  4,  2),
    ("Net Revenue",                   0, 16,  4,  2),
    ("Deposits",                      0, 20,  4,  2),
    ("Daily Conversions (30d)",       2,  0, 12,  6),
    ("Daily Revenue (30d)",           2, 12, 12,  6),
    ("Top Affiliates by Revenue",     8,  0,  8,  8),
    ("Revenue by Country",            8,  8,  8,  8),
    ("Conversion Funnel",             8, 16,  8,  8),
    ("Affiliate Detail Table",       16,  0, 24,  9),
]


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Create Netrefer daily dashboard")
    p.add_argument("--host",        default="http://localhost:3001",
                   help="Metabase URL (default: http://localhost:3001)")
    p.add_argument("--user",        required=True,  help="Admin email")
    p.add_argument("--password",    required=True,  help="Admin password")
    p.add_argument("--setup",       action="store_true",
                   help="Run first-time Metabase setup (creates admin + DB connection)")
    p.add_argument("--db-host",     default="db",
                   help="MySQL hostname as seen from inside Docker (default: db)")
    p.add_argument("--db-port",     default=3306,   type=int)
    p.add_argument("--db-name",     default="netrefer_reporting")
    p.add_argument("--db-user",     default="root")
    p.add_argument("--db-password", default="rootpassword")
    args = p.parse_args()

    print(f"\nConnecting to Metabase at {args.host} …")
    mb = MB(args.host)

    if args.setup:
        print("Running first-time setup …")
        mb.setup(args.user, args.password,
                 args.db_host, args.db_port, args.db_name,
                 args.db_user, args.db_password)
    else:
        mb.login(args.user, args.password)
        print("  Logged in ✓")

    db_id = find_or_add_db(mb, args.db_host, args.db_port,
                           args.db_name, args.db_user, args.db_password)
    wait_for_sync(mb, db_id)

    # ── upsert cards ────────────────────────────────────────────────────
    print("\nUpserting cards …")
    existing = {c["name"]: c["id"] for c in mb.get("/api/card")}
    name_to_id = {}

    for card in make_cards(db_id):
        name = card["name"]
        payload = {k: card[k] for k in ("name","display","dataset_query","visualization_settings")}
        if name in existing:
            cid = existing[name]
            mb.put(f"/api/card/{cid}", payload)
            name_to_id[name] = cid
            print(f"  [updated] {name}")
        else:
            r = mb.post("/api/card", payload)
            name_to_id[name] = r["id"]
            print(f"  [created] {name} (id={r['id']})")

    # ── archive old dashboard, create fresh ─────────────────────────────
    print("\nBuilding dashboard …")
    for d in mb.get("/api/dashboard"):
        if d["name"] == DASH_NAME and not d.get("archived"):
            mb.put(f"/api/dashboard/{d['id']}", {"archived": True})
            print(f"  Archived old dashboard id={d['id']}")

    dash    = mb.post("/api/dashboard", {
        "name":        DASH_NAME,
        "description": "Auto-shows the most recently loaded CSV date. Drop a file → refresh.",
    })
    dash_id = dash["id"]

    dashcards = []
    for i, (name, row, col, sx, sy) in enumerate(LAYOUT):
        cid = name_to_id.get(name)
        if cid is None:
            continue
        dashcards.append({
            "id": -(i + 1),
            "card_id": cid,
            "row": row, "col": col, "size_x": sx, "size_y": sy,
            "parameter_mappings": [],
            "visualization_settings": {},
        })

    try:
        mb.put(f"/api/dashboard/{dash_id}", {"dashcards": dashcards})
    except Exception:
        mb.post(f"/api/dashboard/{dash_id}/dashcards", {"cards": dashcards})

    print(f"\n{'='*50}")
    print(f"  Done!  Open: {args.host}/dashboard/{dash_id}")
    print(f"  The dashboard always shows data for the last loaded date.")
    print(f"  Drop a new CSV → wait ~60s → refresh the page.")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
