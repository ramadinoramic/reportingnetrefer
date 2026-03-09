# Project Summary — Netrefer Reporting Automation

## Overview

This project automates the full reporting pipeline for **Netrefer affiliate data**:
CSV export → MySQL ETL → Metabase dashboards (operational + executive).

---

## What Was Built

### 1. ETL Pipeline (`etl/`)
- `netrefer_etl.py` — reads Netrefer CSV exports, strips the `sep=` header line, skips blank/totals rows, maps column names via `column_map.yaml`, and loads data into MySQL.
- `column_map.yaml` — decouples raw Netrefer headers from DB column names. Edit here if Netrefer changes their export format — no code changes needed.
- Date is extracted from the **filename** (e.g. `netrefer_2024-01-31.csv`) since Netrefer CSVs have no date column.

### 2. MySQL Schema (`sql/`)
| Object | Purpose |
|---|---|
| `netrefer_stats` | Raw fact table — one row per affiliate per date |
| `v_netrefer_daily` | Daily view for Metabase questions |
| `v_netrefer_kpis` | Derived KPIs (Net Revenue / FTD, Reward / FTD, etc.) |
| `v_affiliate_daily_report` | Affiliate-level daily breakdown |
| `v_top_affiliates_7d` | Top affiliates ranked by signups and FTDs over last 7 days |
| `affiliate_report` (proc) | Stored procedure — filter by affiliate and date range |

### 3. Metabase Dashboards (`scripts/`)
Three automated setup scripts create or update dashboards via the Metabase API:

| Script | Dashboard | Audience |
|---|---|---|
| `setup_metabase.py` | Initial DB connection + base setup | Admin |
| `setup_affiliate_dashboard.py` | **Affiliate Deep Dive** — traffic, conversions, revenue per affiliate with date-range + affiliate name filters | Operations |
| `setup_board_dashboard.py` | **Board Report** — executive KPI summary with Top 10 affiliates table | C-Level / Board |

### 4. Board HTML Report (`scripts/generate_board_report.py`)
Generates a standalone **HTML executive report** (no Metabase required):
- Defaults to the latest month in the DB
- Supports `--month YYYY-MM` or custom `--from / --to` date range
- Output: self-contained HTML file ready to email or share

---

## Key Decisions Made During Development

- **Switched from Looker to Metabase** — self-hosted, no licensing cost, full API access.
- **Filename-based date detection** — avoids any dependency on Netrefer CSV structure for the period date.
- **Affiliate filter uses `has_field_values=list`** — forces Metabase to populate the affiliate dropdown from the DB rather than requiring manual entry.
- **Board dashboard uses robust field lookup** — field IDs are resolved dynamically from the Metabase API rather than hardcoded, so the setup script works across fresh installs.
- **Date filters use Metabase "date/range" type** with reliable variable syntax to avoid dialect issues.
- **Top 10 affiliates table** in board dashboard uses raw SQL with `ORDER BY FTDs DESC LIMIT 10` to ensure correct ranking.

---

## Daily Workflow

```bash
# 1. Download report from Netrefer, rename to include date:
#    netrefer_2026-03-08.csv

# 2. Drop into ./drop/

# 3. Load into MySQL
make load-dir

# 4. (Optional) regenerate dashboards if setup changed
make board-dashboard USER=admin@example.com PASSWORD=secret

# 5. (Optional) generate standalone HTML board report
make board-report MONTH=2026-03
```

---

## Infrastructure

| Service | URL | Port |
|---|---|---|
| Metabase | http://localhost:3000 | 3000 |
| MySQL | localhost | 3306 |

Both run via **Docker Compose** (`docker-compose.yml`). Data volumes are preserved on `make down`.

---

## Makefile Reference

| Command | Description |
|---|---|
| `make up` | Start MySQL + Metabase |
| `make down` | Stop containers (data preserved) |
| `make logs` | Tail all logs |
| `make logs service=metabase` | Tail Metabase logs only |
| `make db-shell` | Open MySQL shell in container |
| `make load-dir` | Load all CSVs from `./drop/` |
| `make load FILE=path/to.csv` | Load a single CSV |
| `make setup` | Install Python dependencies |
| `make board-report` | Generate HTML board report (latest month) |
| `make board-report MONTH=2026-03` | Generate report for specific month |
| `make board-report FROM=2026-03-01 TO=2026-03-08` | Custom date range |
| `make board-dashboard USER=x PASSWORD=y` | Create/update Board dashboard in Metabase |

---

## Branch

All work developed on: `claude/automate-netrefer-looker-rhWIB`
