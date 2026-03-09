.PHONY: setup db-init etl-csv etl-api run-scheduler

# Install Python dependencies
setup:
	pip install -r requirements.txt

# Create MySQL schema (requires MYSQL_* env vars to be set)
db-init:
	mysql -h $$MYSQL_HOST -u $$MYSQL_USER -p$$MYSQL_PASSWORD < sql/schema.sql

# Load a single CSV file (usage: make etl-csv FILE=path/to/file.csv)
etl-csv:
	python etl/netrefer_etl.py csv --file $(FILE)

# Load all CSV files from a directory (usage: make etl-dir DIR=path/to/dir/)
etl-dir:
	python etl/netrefer_etl.py csv --dir $(DIR)

# Fetch from Netrefer API for a date range
# Usage: make etl-api START=2024-01-01 END=2024-01-31
etl-api:
	python etl/netrefer_etl.py api --start $(START) --end $(END)

# Run the daily scheduler (stays alive)
run-scheduler:
	python scripts/schedule_etl.py
