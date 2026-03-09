.PHONY: setup db-init load load-dir

# Install Python dependencies
PYTHON := $(shell command -v python3 || command -v python)

setup:
	$(PYTHON) -m pip install -r requirements.txt

# Create MySQL schema
db-init:
	mysql -h $$MYSQL_HOST -u $$MYSQL_USER -p$$MYSQL_PASSWORD < sql/schema.sql

# Load all CSVs from the drop folder (filenames must contain the date)
# Workflow: save Netrefer export as netrefer_YYYY-MM-DD.csv, drop into ./drop/, run this
load-dir:
	$(PYTHON) etl/netrefer_etl.py --dir
