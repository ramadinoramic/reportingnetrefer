.PHONY: setup up down logs db-shell load load-dir

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
