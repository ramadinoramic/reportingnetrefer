.PHONY: setup up down logs db-shell load load-dir board-report board-dashboard affiliate-dashboard etl-dashboard migrate-key migrate-etl audit-csv watch etl-docker

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
# Usage: make board-dashboard USER=admin@example.com PASSWORD=secret
board-dashboard:
	$(PYTHON) scripts/setup_board_dashboard.py \
		--host $(or $(HOST),http://localhost:3000) \
		--user $(USER) \
		--password $(PASSWORD) \
		$(if $(DB_NAME),--db-name $(DB_NAME),)

# Create / update the Affiliate Deep Dive dashboard in Metabase
# Usage: make affiliate-dashboard USER=admin@example.com PASSWORD=secret
affiliate-dashboard:
	$(PYTHON) scripts/setup_affiliate_dashboard.py \
		--host $(or $(HOST),http://localhost:3000) \
		--user $(USER) \
		--password $(PASSWORD) \
		$(if $(DB_NAME),--db-name $(DB_NAME),)

# Run the unique-key migration on an existing database
# Usage: make migrate-key
migrate-key:
	docker compose exec db mysql \
		-u $$MYSQL_USER -p$$MYSQL_PASSWORD $$MYSQL_DATABASE \
		< sql/migrate_unique_key.sql

# Create / update the ETL Health dashboard in Metabase
# Usage: make etl-dashboard USER=admin@example.com PASSWORD=secret
etl-dashboard:
	$(PYTHON) scripts/setup_etl_dashboard.py \
		--host $(or $(HOST),http://localhost:3000) \
		--user $(USER) \
		--password $(PASSWORD) \
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

# Install monthly cron job to email board report on 1st of each month
# Usage: make install-cron EMAIL=you@example.com
install-cron:
	@bash scripts/install_cron.sh $(EMAIL)
