.PHONY: setup db-init load load-dir

# Install Python dependencies
setup:
	pip install -r requirements.txt

# Create MySQL schema
db-init:
	mysql -h $$MYSQL_HOST -u $$MYSQL_USER -p$$MYSQL_PASSWORD < sql/schema.sql

# Load a single file for a specific date:
#   make load FILE=report.csv DATE=2024-01-31
load:
	python etl/netrefer_etl.py --file $(FILE) --date $(DATE)

# Load all CSVs from drop folder.
# Files named *_YYYY-MM-DD.csv are auto-dated.
# Others use DATE=:
#   make load-dir DATE=2024-01-31
load-dir:
	python etl/netrefer_etl.py --dir $(if $(DATE),--date $(DATE),)
