.PHONY: setup db-init load load-dir

# Install Python dependencies
setup:
	pip install -r requirements.txt

# Create MySQL schema
db-init:
	mysql -h $$MYSQL_HOST -u $$MYSQL_USER -p$$MYSQL_PASSWORD < sql/schema.sql

# Load a single CSV file:  make load FILE=path/to/report.csv
load:
	python etl/netrefer_etl.py --file $(FILE)

# Load all CSVs from the drop folder (CSV_DROP_DIR in .env)
load-dir:
	python etl/netrefer_etl.py --dir
