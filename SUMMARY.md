# Project Summary — Netrefer Reporting Automation

## Overview

Full reporting pipeline for **Netrefer affiliate data**:
CSV export → MySQL ETL → Metabase dashboards (operational + executive).

---

## What Was Built

### 1. ETL Pipeline (`etl/`)

- **`netrefer_etl.py`** — reads Netrefer CSV exports, strips the `sep=` header, skips blank/totals rows, maps columns via `column_map.yaml`, and UPSERTs into MySQL (idempotent — safe to re-run).
- **`watcher.py`** — watches `./drop/` for new CSVs and auto-loads them; runs inside Docker.
- **`column_map.yaml`** — decouples raw Netrefer headers from DB column names. Edit here when Netrefer changes their export format — no code changes needed.
- Date is extracted from the **filename** (e.g. `netrefer_2026-03-09.csv`) since Netrefer CSVs have no date column.

### 2. MySQL Schema (`sql/`)

| Object | Purpose |
|--------|---------|
| `netrefer_stats` | Raw fact table — one row per (date, affiliate, campaign) |
| `v_netrefer_daily` | Daily view with calculated conversion rates |
| `v_netrefer_kpis` | Aggregated KPIs per affiliate/date |
| `v_affiliate_daily_report` | Affiliate-level daily breakdown |
| `v_top_affiliates_7d` | Top affiliates by signups/FTDs over last 7 days |
| `affiliate_report` (proc) | Stored procedure — filter by affiliate and date range |
| `etl_runs` | ETL audit log — tracks each load run, row counts, errors |

### 3. Metabase Dashboards (`scripts/`)

Three automated setup scripts create or update dashboards via the Metabase API:

| Script | Dashboard | Audience |
|--------|-----------|----------|
| `setup_metabase.py` | Initial DB connection + base setup | Admin |
| `setup_affiliate_dashboard.py` | **Affiliate Deep Dive** | Operations |
| `setup_board_dashboard.py` | **Board Report** | C-Level / Board |
| `setup_etl_dashboard.py` | **ETL Health** | Engineering |

All scripts are **idempotent** — re-running updates existing cards and recreates the dashboard with clean parameter mappings.

#### Affiliate Deep Dive dashboard

Cards: 5 KPI scalars, Daily Revenue Trend (line), Daily Conversions (line),
Conversion Funnel (bar), Revenue by Campaign (bar), Revenue by Country (pie),
Daily Detail Table, Top 10 Affiliates (last 7 days, fixed).

Filters:
- **Affiliate Name** — `string/=` field filter, populates as a dropdown from the DB
- **From Date** — `date/single` variable → `report_date >= <date>`
- **To Date** — `date/single` variable → `report_date <= <date>`

Both date filters are optional and independent. All filterable cards accumulate
data across the full selected range.

### 4. Board HTML Report (`scripts/generate_board_report.py`)

Standalone **HTML executive report** — no Metabase required:
- Defaults to the latest month in the DB
- Supports `--month YYYY-MM` or custom `--from / --to` range
- Output: self-contained HTML file ready to email or share
- Can be scheduled monthly via `make install-cron EMAIL=...`

### 5. CSV Audit Tool (`scripts/audit_csv.py`)

Pre-load sanity check: validates row counts, column mapping coverage, and detects UPSERT collisions before committing data.

---

## Key Decisions

- **Switched from Looker to Metabase** — self-hosted, no licensing cost, full REST API.
- **Filename-based date detection** — avoids any dependency on Netrefer CSV structure for the period date.
- **Affiliate filter uses `has_field_values=list`** — forces Metabase to populate the dropdown from the DB rather than requiring manual entry.
- **Date filters use simple `type: date` template variables** (not field filters) — Metabase substitutes `'YYYY-MM-DD'` strings directly into the SQL. This is more reliable than `date/range` field filters, which require Metabase to introspect the MySQL column type and can silently return no results when type detection fails.
- **Scalar KPIs use `COALESCE(SUM(...), 0)`** — shows `0` instead of `null` when no data exists for the selected range.
- **Detail table groups by affiliate only** — when a date range is selected, one row per affiliate is returned with totals accumulated across the full range (not one row per day per affiliate).
- **Dashboard is always recreated on re-run** — the old dashboard is archived and a fresh one created to avoid stale parameter mappings accumulating.
- **Field IDs resolved dynamically** — never hardcoded; the setup scripts query the Metabase API to find field IDs, so they work across fresh installs and schema changes.

---

## Daily Workflow

```bash
# 1. Download report from Netrefer, rename to include the date:
#    netrefer_2026-03-09.csv

# 2. Drop into ./drop/ (ETL watcher loads it automatically)
#    OR load manually:
make load-dir

# 3. (First time or after script changes) recreate dashboards:
make affiliate-dashboard USER=admin@example.com PASSWORD=secret
make board-dashboard     USER=admin@example.com PASSWORD=secret

# 4. (Optional) generate standalone HTML board report
make board-report MONTH=2026-03
```

---

## Infrastructure

| Service | URL / Port |
|---------|-----------|
| Metabase | http://localhost:3000 |
| MySQL | localhost:3308 |
| ETL watcher | Runs inside Docker (`etl` service) |

Both DB and ETL run via **Docker Compose**. Data volumes are preserved on `make down`.

---

## Makefile Reference

| Command | Description |
|---------|-------------|
| `make up` | Start MySQL + ETL watcher |
| `make down` | Stop containers (data preserved) |
| `make logs` | Tail all logs |
| `make logs service=db` | Tail a specific service |
| `make db-shell` | Open MySQL shell in container |
| `make load-dir` | Load all CSVs from `./drop/` |
| `make load FILE=path/to.csv` | Load a single CSV |
| `make setup` | Install Python dependencies |
| `make affiliate-dashboard USER=x PASSWORD=y` | Create/update Affiliate Deep Dive dashboard |
| `make board-dashboard USER=x PASSWORD=y` | Create/update Board Report dashboard |
| `make etl-dashboard USER=x PASSWORD=y` | Create/update ETL Health dashboard |
| `make board-report` | HTML board report (latest month) |
| `make board-report MONTH=2026-03` | HTML report for specific month |
| `make board-report FROM=2026-03-01 TO=2026-03-08` | HTML report for custom range |
| `make watch` | Watch `./drop/` and auto-load (foreground) |
| `make audit-csv FILE=drop/netrefer_2026-03-09.csv` | Audit a CSV before loading |
| `make install-cron EMAIL=you@example.com` | Monthly cron to email board report |
| `make migrate-key` | Run unique-key migration on existing DB |
| `make migrate-etl` | Run etl_runs schema migration |

---

## Branch

All work developed on: `claude/automate-netrefer-looker-rhWIB`
