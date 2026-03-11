.PHONY: setup up down logs db-shell load load-dir board-report board-dashboard affiliate-dashboard etl-dashboard migrate-key migrate-etl audit-csv watch etl-docker diagnose

# Load .env so make targets can use MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE etc.
-include .env
export

PYTHON := $(shell test -f venv/bin/python && echo venv/bin/python || command -v python3 || command -v python)

# Install Python dependencies
setup:
	$(PYTHON) -m pip install -r requirements.txt

# Start MySQL + ETL watcher (docker compose up -d)
up:
	docker compose up -d --build
	@echo "MySQL      → localhost:3308"
	@echo "ETL watcher running inside Docker (drop CSVs into ./drop/)"

# Stop containers (data volumes are preserved)
down:
	docker compose down

# Tail logs (optionally pass service=db or service=metabase)
logs:
	docker compose logs -f $(service)

# Open a MySQL shell inside the db container
db-shell:
	docker compose exec db mysql -u $$MYSQL_USER -p$$MYSQL_PASSWORD $$MYSQL_DATABASE

# Load all CSVs from the drop folder (filenames must contain the date)
load-dir:
	$(PYTHON) etl/netrefer_etl.py --dir

# Load a single file: make load FILE=drop/netrefer_2024-01-31.csv
load:
	$(PYTHON) etl/netrefer_etl.py --file $(FILE)

# Generate C-Level / Board HTML report
# Examples:
#   make board-report                   ← latest month in DB
#   make board-report MONTH=2026-03     ← specific month
#   make board-report FROM=2026-03-01 TO=2026-03-08  ← custom range
board-report:
	$(PYTHON) scripts/generate_board_report.py \
		$(if $(MONTH),--month $(MONTH),) \
		$(if $(FROM),--from $(FROM),) \
		$(if $(TO),--to $(TO),) \
		$(if $(OUTPUT),--output $(OUTPUT),) \
		$(if $(EMAIL),--email $(EMAIL),)

# Create / update the Board Report dashboard in Metabase
# Usage: make board-dashboard MB_USER=admin@example.com MB_PASS=secret
board-dashboard:
	$(PYTHON) scripts/setup_board_dashboard.py \
		--host $(or $(HOST),http://localhost:3000) \
		--user $(MB_USER) \
		--password $(MB_PASS) \
		$(if $(DB_NAME),--db-name $(DB_NAME),)

# Create / update the Affiliate Deep Dive dashboard in Metabase
# Usage: make affiliate-dashboard MB_USER=admin@example.com MB_PASS=secret
affiliate-dashboard:
	$(PYTHON) scripts/setup_affiliate_dashboard.py \
		--host $(or $(HOST),http://localhost:3000) \
		--user $(MB_USER) \
		--password $(MB_PASS) \
		$(if $(DB_NAME),--db-name $(DB_NAME),)

# Run the unique-key migration on an existing database
# Usage: make migrate-key
migrate-key:
	docker compose exec db mysql \
		-u $$MYSQL_USER -p$$MYSQL_PASSWORD $$MYSQL_DATABASE \
		< sql/migrate_unique_key.sql

# Create / update the ETL Health dashboard in Metabase
# Usage: make etl-dashboard MB_USER=admin@example.com MB_PASS=secret
etl-dashboard:
	$(PYTHON) scripts/setup_etl_dashboard.py \
		--host $(or $(HOST),http://localhost:3000) \
		--user $(MB_USER) \
		--password $(MB_PASS) \
		$(if $(DB_NAME),--db-name $(DB_NAME),)

# Run the etl_runs schema migration (adds rows_parsed + warnings columns)
# Usage: make migrate-etl
migrate-etl:
	docker compose exec db mysql \
		-u $$MYSQL_USER -p$$MYSQL_PASSWORD $$MYSQL_DATABASE \
		< sql/migrate_etl_runs.sql

# Watch drop/ folder and auto-load new CSVs (runs forever, Ctrl-C to stop)
# Usage: make watch
watch:
	$(PYTHON) etl/watcher.py

# Process whatever is in drop/ right now and exit
# Usage: make watch-once
watch-once:
	$(PYTHON) etl/watcher.py --once

# Build the Docker image for the ETL runner
# Usage: make etl-docker
etl-docker:
	docker build -f Dockerfile.etl -t netrefer-etl .

# Audit a CSV file: check row counts, column mapping, and UPSERT collisions
# Usage: make audit-csv FILE=drop/netrefer_2026-03-09.csv
audit-csv:
	$(PYTHON) scripts/audit_csv.py --file $(FILE)

# Fix Metabase date picker timezone: sets report-timezone to Europe/Istanbul (UTC+3).
# Run this once whenever Metabase is freshly installed or timezone resets.
# Usage: make fix-tz MB_USER=admin@example.com MB_PASS=secret
fix-tz:
	@TOKEN=$$(curl -s -X POST $(or $(HOST),http://localhost:3000)/api/session \
	  -H "Content-Type: application/json" \
	  -d "{\"username\":\"$(MB_USER)\",\"password\":\"$(MB_PASS)\"}" \
	  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])") && \
	curl -s -X PUT $(or $(HOST),http://localhost:3000)/api/setting/report-timezone \
	  -H "Content-Type: application/json" \
	  -H "X-Metabase-Session: $$TOKEN" \
	  -d '{"value":"Europe/Istanbul"}' && \
	echo "Done — Metabase timezone set to Europe/Istanbul"

# Inspect what SQL the AF cards actually have in Metabase RIGHT NOW,
# and show the current timezone setting.
# Usage: make inspect-mb MB_USER=admin@example.com MB_PASS=secret
inspect-mb:
	@python3 - <<'PYEOF'
import sys, json, urllib.request, urllib.error
host = "$(or $(HOST),http://localhost:3000)"
# login
req = urllib.request.Request(host + "/api/session",
    data=json.dumps({"username":"$(MB_USER)","password":"$(MB_PASS)"}).encode(),
    headers={"Content-Type":"application/json"})
try:
    tok = json.loads(urllib.request.urlopen(req).read())["id"]
except Exception as e:
    print("LOGIN FAILED:", e); sys.exit(1)
hdrs = {"X-Metabase-Session": tok, "Content-Type": "application/json"}
def get(path):
    r = urllib.request.Request(host+path, headers=hdrs)
    return json.loads(urllib.request.urlopen(r).read())
# timezone
try:
    tz = get("/api/setting/report-timezone")
    print("=== Metabase report-timezone:", tz.get("value","(not set)"))
except: print("=== Metabase report-timezone: (could not read)")
# cards
cards = get("/api/card")
af = [c for c in cards if c["name"].startswith("AF –")]
if not af:
    print("\nNO 'AF –' cards found — setup script has never been run!")
    sys.exit(0)
print(f"\nFound {len(af)} AF cards:\n")
for c in sorted(af, key=lambda x: x["name"]):
    sql = c.get("dataset_query",{}).get("native",{}).get("query","(no SQL)")
    tags = c.get("dataset_query",{}).get("native",{}).get("template-tags",{})
    has_from = "from_date" in tags
    has_to   = "to_date"   in tags
    date_in_sql = "from_date" in sql and "to_date" in sql
    print(f"  [{c['id']}] {c['name']}")
    print(f"      tags: from_date={has_from}, to_date={has_to}  |  vars_in_sql={date_in_sql}")
    if "from_date" in sql:
        for line in sql.splitlines():
            if "from_date" in line or "to_date" in line or "report_date" in line:
                print(f"      SQL: {line.strip()}")
    print()
PYEOF

# Check DB data for a specific date directly (bypasses Metabase).
# Usage: make check-date DATE=2026-03-10
check-date:
	docker compose exec db mysql -u $$MYSQL_USER -p$$MYSQL_PASSWORD $$MYSQL_DATABASE -e \
	  "SELECT report_date, COUNT(*) rows, SUM(clicks) clicks, SUM(first_depositors) ftds \
	   FROM netrefer_stats \
	   WHERE report_date = '$(DATE)' \
	   GROUP BY report_date;"

# Show per-day row counts and totals for the last 30 days — quick data-quality check
# Usage: make diagnose
diagnose:
	docker compose exec db mysql -u $$MYSQL_USER -p$$MYSQL_PASSWORD $$MYSQL_DATABASE -e \
	  "SELECT report_date, COUNT(*) row_count, SUM(clicks) clicks, SUM(first_depositors) ftds, \
	   ROUND(SUM(net_revenue),0) net_revenue \
	   FROM netrefer_stats \
	   WHERE report_date >= CURDATE() - INTERVAL 30 DAY \
	   GROUP BY report_date ORDER BY report_date DESC;"

# Install monthly cron job to email board report on 1st of each month
# Usage: make install-cron EMAIL=you@example.com
install-cron:
	@bash scripts/install_cron.sh $(EMAIL)
