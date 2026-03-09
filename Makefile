.PHONY: setup up down logs db-shell load load-dir board-report board-dashboard

PYTHON := $(shell command -v python3 || command -v python)

# Install Python dependencies
setup:
	$(PYTHON) -m pip install -r requirements.txt

# Start MySQL + Metabase
up:
	docker compose up -d
	@echo "Metabase → http://localhost:3000"
	@echo "MySQL    → localhost:3306"

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
		$(if $(OUTPUT),--output $(OUTPUT),)

# Create / update the Board Report dashboard in Metabase
# Usage: make board-dashboard USER=admin@example.com PASSWORD=secret
board-dashboard:
	$(PYTHON) scripts/setup_board_dashboard.py \
		--host $(or $(HOST),http://localhost:3000) \
		--user $(USER) \
		--password $(PASSWORD) \
		$(if $(DB_NAME),--db-name $(DB_NAME),)
