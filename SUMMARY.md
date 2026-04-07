# Project Summary — Netrefer Reporting Automation

## Overview

Full reporting pipeline for **Netrefer affiliate data**:
CSV export → MySQL ETL → Metabase dashboards (operational + executive).

---

## Architecture

```
[Netrefer CSV export]
        │
        ▼
[drop/ folder]  ←── rename file to netrefer_YYYY-MM-DD.csv
        │
        ▼
[ETL watcher / make load]
  etl/netrefer_etl.py
  etl/column_map.yaml
        │
        ▼
[MySQL 8.0  :3308]
  netrefer_stats  (fact table)
  etl_runs        (audit log)
  + views + stored procedures
        │
        ▼
[Metabase  :3001]
  5 dashboards (see below)
```

All services run via **Docker Compose**. Data volumes survive `make down`.

---

## Dashboards

| Dashboard | Script | Audience | Purpose |
|-----------|--------|----------|---------|
| **Affiliate Deep Dive** | `setup_affiliate_dashboard.py` | Operations | Filter by affiliate + date range; KPIs, trends, campaign breakdown |
| **Board Report** | `setup_board_dashboard.py` | C-Level | Monthly KPIs, top affiliates, country/campaign breakdown |
| **ETL Health** | `setup_etl_dashboard.py` | Engineering | Load run history, error rates, row counts |
| **Channel & Affiliate Overview** | `setup_channel_dashboard.py` | Operations | Filter by date, channel (campaign), affiliate; KPI scorecards, daily line charts, per-channel bar charts, affiliate detail table |
| **Performance Leaderboard** | `setup_leaderboard_dashboard.py` | Management | Who is actually performing? Ranked by FTDs + conversion rates; bar charts + full scorecard table |
| **Source Drop Detection** | `setup_trend_dashboard.py` | Operations | Which sources are declining? 3d/7d/15d comparison vs prior window; worst drops at the top |

---

## ETL Pipeline (`etl/`)

- **`netrefer_etl.py`** — reads Netrefer CSV exports, strips `sep=` header, skips blank/totals rows, maps columns via `column_map.yaml`, UPSERTs into MySQL (idempotent — safe to re-run).
- **`watcher.py`** — watches `./drop/` for new CSVs and auto-loads them; runs inside Docker.
- **`column_map.yaml`** — decouples raw Netrefer headers from DB column names. Edit here when Netrefer changes their export format — no code changes needed.
- Date is extracted from the **filename** (e.g. `netrefer_2026-03-09.csv`) — Netrefer CSVs have no date column.

---

## MySQL Schema (`sql/`)

**Fact table:** `netrefer_stats`
- Grain: one row per `(report_date, affiliate_id, campaign_name)`
- Key metrics: `clicks`, `registrations`, `first_depositors`, `deposits`, `gross_revenue`, `net_revenue`, `total_reward`
- UPSERT key: `(report_date, affiliate_id, campaign_name)`

**Views:**

| View | Purpose |
|------|---------|
| `v_netrefer_daily` | All stats + calculated conversion rates (click→reg%, reg→FTD%) |
| `v_netrefer_kpis` | Aggregated KPIs per affiliate/date/campaign |
| `v_affiliate_daily_report` | Filterable affiliate view |
| `v_top_affiliates_7d` | Rolling 7-day rankings by FTD/signup |

**Audit:** `etl_runs` table tracks every load run (timestamp, rows upserted, status, errors).

---

## Reporting Scripts (`scripts/`)

| Script | Purpose |
|--------|---------|
| `generate_board_report.py` | Standalone HTML executive report (no Metabase needed); period-over-period KPI comparison with delta badges; email via SMTP |
| `source_trend_report.py` | **CLI drop detection** — terminal table + HTML report; last 3/7/15 days vs prior period; red = dropped >20% FTDs |
| `audit_csv.py` | Pre-load sanity check: validates column mapping, row counts, UPSERT collisions |
| `generate_diagram.py` | Generates `architecture.pdf` (requires `pip install matplotlib`) |
| `inspect_metabase.py` | Dumps current Metabase card SQL for debugging |
| `run_migration.py` | Runs DB schema migrations |

---

## Daily Workflow

```bash
# 1. Download from Netrefer portal — rename file:
#    netrefer_YYYY-MM-DD.csv

# 2. Drop into ./drop/   (watcher auto-loads)
#    OR load manually:
make load FILE=drop/netrefer_2026-03-17.csv

# 3. Verify load:
make check-date DATE=2026-03-17

# 4. Open Metabase:  http://localhost:3001
```

---

## Makefile Reference

### Data

| Command | Description |
|---------|-------------|
| `make up` | Start all Docker services |
| `make down` | Stop containers (data preserved) |
| `make logs` | Tail all logs |
| `make db-shell` | MySQL shell inside container |
| `make load FILE=path/to.csv` | Load a single CSV |
| `make load-dir` | Load all CSVs from `./drop/` |
| `make watch` | Watch `./drop/` in foreground |
| `make audit-csv FILE=...` | Validate CSV before loading |
| `make check-date DATE=YYYY-MM-DD` | Quick row/metric count for a date |
| `make audit-etl` | Show recent ETL run history |
| `make diagnose` | Last 30 days row counts |

### Dashboards

All dashboard targets use `MB_USER` and `MB_PASS` (not `USER`/`PASSWORD`):

```bash
make <target> MB_USER=dinoramitch@gmail.com MB_PASS=yourpassword
```

| Command | Dashboard created |
|---------|-------------------|
| `make affiliate-dashboard` | Affiliate Deep Dive |
| `make board-dashboard` | Board Report |
| `make etl-dashboard` | ETL Health |
| `make channel-dashboard` | Channel & Affiliate Overview |
| `make leaderboard-dashboard` | Performance Leaderboard |
| `make trend-dashboard` | Source Drop Detection |

### Reports

| Command | Description |
|---------|-------------|
| `make board-report` | HTML executive report (latest month) |
| `make board-report MONTH=2026-03` | Report for specific month |
| `make board-report FROM=2026-03-01 TO=2026-03-08` | Custom date range |
| `make source-trend` | Terminal drop detection report |
| `make source-trend OUTPUT=drop/trend.html OPEN=1` | Save + open in browser |

### Admin

| Command | Description |
|---------|-------------|
| `make setup` | Install Python dependencies into venv |
| `make reset-mb-h2` | Fix H2 PK sequence errors (run before dashboard setup if errors occur) |
| `make fix-tz` | Set Metabase timezone to Europe/Istanbul |
| `make migrate-key` | Run unique-key migration on existing DB |
| `make migrate-etl` | Run etl_runs schema migration |

---

## Infrastructure

| Service | Port | Notes |
|---------|------|-------|
| Metabase | `:3001` | `http://localhost:3001` |
| MySQL | `:3308` | Mapped from container :3306 |
| ETL watcher | — | Runs inside Docker as `etl` service |

### Common issues

**H2 PK violation on dashboard setup:**
```bash
make reset-mb-h2   # bumps sequences above existing IDs
# wait 30s, then re-run the dashboard target
```

**MySQL auth error (`caching_sha2_password`):**
```bash
docker compose exec db mysql -u root -prootpassword -e \
  "ALTER USER 'root'@'%' IDENTIFIED WITH mysql_native_password BY 'rootpassword'; FLUSH PRIVILEGES;"
docker compose restart metabase
```

**Metabase not starting:**
```bash
docker compose up -d metabase
docker compose logs -f metabase
```

---

## Key Design Decisions

- **Metabase over Looker** — self-hosted, zero licensing cost, full REST API for automation.
- **Filename-based date** — Netrefer CSVs have no date column; date comes from filename.
- **Idempotent scripts** — all dashboard setup scripts can be re-run safely; existing cards are updated (PUT), old dashboard is archived and recreated fresh.
- **Field filter dropdowns** — `has_field_values=list` + rescan forces Metabase to populate dropdowns from the DB rather than requiring manual text entry.
- **Simple date variables** (`type: date`) over field filters for date range — more reliable; Metabase substitutes `'YYYY-MM-DD'` strings directly into SQL.
- **No timezone math in SQL** — dates come from filenames (already the correct date), so no UTC conversion needed in queries.
- **Drop detection anchors to `MAX(report_date)`** — always uses the latest actual data, not `CURDATE()`, so it works regardless of when you upload files.

---

## Branch

All work on: `claude/automate-netrefer-looker-rhWIB`
