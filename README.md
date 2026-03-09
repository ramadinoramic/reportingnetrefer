# Netrefer → MySQL → Looker Automation

Automated pipeline that ingests Netrefer affiliate reports into MySQL and exposes them as a Looker explore.

## Architecture

```
Netrefer (API or CSV export)
        │
        ▼
  etl/netrefer_etl.py   ←── etl/column_map.yaml
        │
        ▼
  MySQL: netrefer_stats  ──► v_netrefer_daily (view)
        │
        ▼
  Looker (LookML model + view)
        │
        ▼
  Dashboards / Explores
```

## Quick Start

### 1. Environment setup

```bash
cp .env.example .env
# Edit .env with your MySQL credentials and (optionally) Netrefer API key
pip install -r requirements.txt
```

### 2. Create the MySQL schema

```bash
make db-init
# or: mysql -h HOST -u USER -pPASS < sql/schema.sql
```

### 3. Load data

**Option A – Drop CSV files** (no API key required):

```bash
# Single file
make etl-csv FILE=/path/to/netrefer_report.csv

# All CSVs in a folder
make etl-dir DIR=/path/to/csv_exports/
```

**Option B – Netrefer API** (requires `NETREFER_API_KEY` in `.env`):

```bash
make etl-api START=2024-01-01 END=2024-01-31
```

**Option C – Daily scheduled run** (fetches yesterday automatically at 06:00):

```bash
make run-scheduler
```

### 4. Connect Looker

1. In Looker Admin → **Connections**, create a new MySQL connection named **`netrefer_mysql`** pointing to your database.
2. Copy the `looker/` folder into your LookML project.
3. Deploy and navigate to **Explore → Affiliate Stats**.

## Column Mapping

Edit `etl/column_map.yaml` to match your Netrefer CSV headers.
The YAML maps raw CSV column names to MySQL column names.
No code changes needed when Netrefer changes their export format.

## MySQL Schema

| Column | Type | Notes |
|---|---|---|
| `report_date` | DATE | Grain key |
| `affiliate_id` | VARCHAR | Grain key |
| `campaign_id` | VARCHAR | Grain key |
| `brand` | VARCHAR | Grain key |
| `country` | VARCHAR | Grain key |
| `clicks` | INT | |
| `registrations` | INT | |
| `first_depositors` | INT | FTDs |
| `net_revenue_cents` | BIGINT | Stored × 100 to avoid float issues |
| `commission_cents` | BIGINT | Stored × 100 |

The view `v_netrefer_daily` exposes currency columns divided back to decimals for Looker.

## Looker Measures Available

| Measure | Description |
|---|---|
| Impressions / Clicks | Traffic volume |
| CTR | Clicks / Impressions |
| Registrations | Sign-ups |
| First Time Depositors | FTDs |
| Click → Reg Rate | Conversion funnel step 1 |
| Reg → FTD Rate | Conversion funnel step 2 |
| Net Revenue / Gross Revenue | Revenue metrics |
| Commission | Affiliate payout |
| Avg Revenue / FTD | Revenue efficiency |
