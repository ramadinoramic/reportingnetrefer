#!/usr/bin/env python3
"""Quick diagnostic: shows AF card SQL and timezone currently in Metabase."""
import sys, json, argparse, urllib.request

def get(host, tok, path):
    r = urllib.request.Request(host + path,
        headers={"X-Metabase-Session": tok, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r).read())

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host",     default="http://localhost:3000")
    p.add_argument("--user",     required=True)
    p.add_argument("--password", required=True)
    args = p.parse_args()

    req = urllib.request.Request(args.host + "/api/session",
        data=json.dumps({"username": args.user, "password": args.password}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        tok = json.loads(urllib.request.urlopen(req).read())["id"]
    except Exception as e:
        print("LOGIN FAILED:", e); sys.exit(1)

    try:
        tz = get(args.host, tok, "/api/setting/report-timezone")
        print("=== Metabase report-timezone:", tz.get("value", "(not set)"))
    except Exception as e:
        print("=== Metabase report-timezone: (could not read —", e, ")")

    cards = get(args.host, tok, "/api/card")
    af = [c for c in cards if c["name"].startswith("AF \u2013")]
    if not af:
        print("\nNO 'AF \u2013' cards found \u2014 setup script has never been run!")
        sys.exit(0)

    print(f"\nFound {len(af)} AF cards:\n")
    for c in sorted(af, key=lambda x: x["name"]):
        sql  = c.get("dataset_query", {}).get("native", {}).get("query", "(no SQL)")
        tags = c.get("dataset_query", {}).get("native", {}).get("template-tags", {})
        has_from   = "from_date" in tags
        has_to     = "to_date"   in tags
        date_in_sql = "from_date" in sql and "to_date" in sql
        print(f"  [{c['id']}] {c['name']}")
        print(f"      template-tags: from_date={has_from}  to_date={has_to}  |  vars_in_sql={date_in_sql}")
        for line in sql.splitlines():
            l = line.strip()
            if any(k in l for k in ("from_date", "to_date", "report_date")):
                print(f"      SQL> {l}")
        print()

if __name__ == "__main__":
    main()
