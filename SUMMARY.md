# Project Summary — Netrefer Reporting Automation

## Overview

Full reporting pipeline for **Netrefer affiliate data**:
CSV export → MySQL ETL → Metabase dashboards (operational + executive) + Telegram bot (natural language queries).

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
        ├──────────────────────────────────┐
        ▼                                  ▼
[Metabase  :3001]              [Telegram Bot]
  6 dashboards                   scripts/telegram_bot.py
        │                        /menu keyboard + natural
        ▼                        language queries
[Cloudflare Tunnel]
  HTTPS remote access
  Zero Trust OTP gate
```

All services run via **Docker Compose**. Data volumes survive `make down`.

---

## Dashboards

| Dashboard | Script | Audience | Purpose |
|-----------|--------|----------|---------|
| **Affiliate Deep Dive** | `setup_affiliate_dashboard.py` | Operations | Filter by affiliate + date range; KPIs, trends, campaign breakdown |
| **Board Report** | `setup_board_dashboard.py` | C-Level | Monthly KPIs, top affiliates, country/campaign breakdown |
| **ETL Health** | `setup_etl_dashboard.py` | Engineering | Load run history, error rates, row counts |
| **Channel & Affiliate Overview** | `setup_channel_dashboard.py` | Operations | Filter by date, channel group, affiliate; KPI scorecards, daily line charts, horizontal bar charts per channel |
| **Performance Leaderboard** | `setup_leaderboard_dashboard.py` | Management | Who is performing vs not: top 10 by FTDs + NGR, conversion rates, full ranked scorecard table |
| **Source Drop Detection** | `setup_trend_dashboard.py` | Operations | Which sources are declining? 3d/7d/15d vs prior window; worst drops first; self-contained (no date filters needed) |

### Channel Grouping

All channel-aware dashboards and the Telegram bot compute channel using a `CASE` expression on `affiliate_id` (not `campaign_name`):

| Group | Description |
|-------|-------------|
| CPA/CPL | Cost-per-action / cost-per-lead affiliates |
| Direct | Direct traffic partners |
| MB in-house | MoneyBoosters in-house team |
| MB outsourced | MoneyBoosters outsourced partners |
| SEO | Search engine optimisation affiliates |
| Influencers | Influencer & content affiliates |
| Social | Social media affiliates |
| unattributed | No affiliate ID (organic / direct) |
| Affiliates | All other affiliates |

---

## ETL Pipeline (`etl/`)

- **`netrefer_etl.py`** — reads Netrefer CSV exports, strips `sep=` header, skips blank/totals rows, maps columns via `column_map.yaml`, UPSERTs into MySQL (idempotent — safe to re-run).
- **`watcher.py`** — watches `./drop/` every 60 seconds for new CSVs and auto-loads them; runs inside Docker. Skips files already in `etl_runs` with `status='success'`.
- **`column_map.yaml`** — decouples raw Netrefer headers from DB column names. Edit here when Netrefer changes their export format — no code changes needed.
- Date is extracted from the **filename** (e.g. `netrefer_2026-03-09.csv`) — Netrefer CSVs have no date column; supports `YYYY-MM-DD`, `YYYYMMDD`, `DD-MM-YYYY`, `DD.MM.YYYY`.

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

**Audit:** `etl_runs` table tracks every load run (timestamp, rows upserted, status, errors, DQ warnings).

---

## Reporting Scripts (`scripts/`)

| Script | Purpose |
|--------|---------|
| `telegram_bot.py` | Telegram reporting bot — see section below |
| `generate_board_report.py` | Standalone HTML executive report (no Metabase needed); period-over-period KPI comparison with delta badges; email via SMTP |
| `source_trend_report.py` | **CLI drop detection** — terminal table + HTML report; last 3/7/15 days vs prior period; red = dropped >20% FTDs |
| `audit_csv.py` | Pre-load sanity check: validates column mapping, row counts, UPSERT collisions |
| `generate_diagram.py` | Generates `architecture.pdf` (requires `pip install matplotlib`) |
| `inspect_metabase.py` | Dumps current Metabase card SQL for debugging |
| `run_migration.py` | Runs DB schema migrations |

---

## Telegram Bot

Natural language reporting interface. Send questions like *"who is top performer yesterday?"* or tap a button from `/menu`.

**Commands:**
- `/start` or `/menu` — shows inline keyboard with 10 pre-filled quick queries

**Quick-access buttons** (tap — no typing needed):

| Row | Left | Right |
|-----|------|-------|
| 1 | 📊 Summary yesterday | 📊 Summary this week |
| 2 | 🏆 Top performers | 🏆 Top — last 7d |
| 3 | 📡 Channels yesterday | 📡 Channels this week |
| 4 | 📈 Trend last 7d | 📈 Trend this month |
| 5 | ⚠️ Who is dropping? | 🔄 Week vs last week |

**Natural language intents understood:**

| Intent | Example |
|--------|---------|
| `kpi_summary` | "how did we do yesterday?" |
| `top_performers` | "who is best by NGR last week?" |
| `channel_breakdown` | "show channel breakdown this week" |
| `trend` | "daily trend last 7 days" |
| `drops` | "who is dropping?" / "which sources are declining?" |
| `comparison` | "compare this week vs last week" |
| `specific_affiliate` | "show me SEO stats yesterday" |

**Security:** `TELEGRAM_ALLOWED_USERS` in `.env` — comma-separated Telegram user IDs (get from `@userinfobot`). Bot silently ignores all other users.

**Required `.env` vars:**
```
TELEGRAM_BOT_TOKEN=        # from @BotFather
TELEGRAM_ALLOWED_USERS=    # e.g. 491833895,123456789
```

**Share with a colleague:** Get their Telegram user ID (they message `@userinfobot`), add it to `TELEGRAM_ALLOWED_USERS`, run `docker compose up -d bot`.

---

## Remote Access (Cloudflare Tunnel)

Metabase is accessible to remote colleagues via a **Cloudflare Zero Trust tunnel**:
- Metabase binds to `127.0.0.1:3001` (loopback only — not reachable directly from outside)
- `cloudflared` Docker service opens an outbound tunnel to Cloudflare's edge
- Cloudflare Access gate requires email OTP before the Metabase login screen appears
- Required `.env` var: `CLOUDFLARE_TUNNEL_TOKEN=` (from Cloudflare Zero Trust dashboard)

---

## Daily Workflow

```bash
# 1. Download from Netrefer portal — rename file:
#    netrefer_YYYY-MM-DD.csv

# 2. Drop into ./drop/   (watcher auto-loads within 60s)
#    OR load manually:
make load FILE=drop/netrefer_2026-03-17.csv

# 3. Verify load:
make check-date DATE=2026-03-17

# 4. Open Metabase:  http://localhost:3001
#    OR ask Telegram bot:  /menu

# If re-uploading the same filename (updated Netrefer export):
make reprocess FILE=netrefer_2026-03-17.csv
# watcher will pick it up within 60s
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
| `make watch-once` | Process `./drop/` once and exit |
| `make reprocess FILE=name.csv` | Force re-process already-loaded file |
| `make audit-csv FILE=...` | Validate CSV before loading |
| `make check-date DATE=YYYY-MM-DD` | Quick row/metric count for a date |
| `make audit-etl` | Show recent ETL run history |
| `make diagnose` | Last 30 days row counts |

### Dashboards

All dashboard targets use `MB_USER` and `MB_PASS`:

```bash
make <target> MB_USER=admin@example.com MB_PASS=yourpassword
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
| MySQL | `:3308` | Mapped from container :3306 |
| Metabase | `:3001` (loopback) | `http://localhost:3001` — not externally reachable directly |
| ETL watcher | — | `etl` Docker service; polls `./drop/` every 60s |
| Telegram bot | — | `bot` Docker service; `/menu` for quick queries |
| Cloudflare Tunnel | — | `cloudflared` Docker service; HTTPS remote access + OTP gate |

### Common issues

**H2 PK violation on dashboard setup:**
```bash
make reset-mb-h2   # bumps sequences to 9,000,000 above existing IDs
# wait 30s, then re-run the dashboard target
```

**File already loaded — watcher keeps skipping it:**
```bash
make reprocess FILE=netrefer_2026-04-08.csv
# clears etl_runs record; watcher picks it up within 60s
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
- **Idempotent dashboard scripts** — all `setup_*.py` scripts update dashboards **in-place** (PUT) — preserves dashboard URL/ID and avoids H2 sequence collisions. Safe to re-run.
- **Channel group via `affiliate_id` CASE** — `campaign_name` is inconsistent across exports; `affiliate_id` is stable. All channel grouping uses a single `CHANNEL_CASE` constant shared across dashboard scripts and the Telegram bot.
- **Field filter dropdowns** — `has_field_values=list` + rescan forces Metabase to populate dropdowns from the DB rather than requiring manual text entry.
- **Simple date variables** (`type: date`) over field filters for date range — more reliable; Metabase substitutes `'YYYY-MM-DD'` strings directly into SQL.
- **No timezone math in SQL** — dates come from filenames (already the correct date), so no UTC conversion needed in queries.
- **Drop detection anchors to `MAX(report_date)`** — always uses the latest actual data, not `CURDATE()`, so it works regardless of when you upload files.
- **Telegram bot uses `date.today()` as period anchor** — "yesterday" always means the literal calendar day before today, not "one day before the latest DB row". The `latest` / `today` tokens still return the most recent available data.
- **Bot uses keyword-based intent parsing** — no external API dependency or credits needed; fast, free, deterministic.
- **Bot DB credentials use `MYSQL_BOT_USER=root`** — set in `docker-compose.yml` environment override so the bot always has DB access regardless of what `MYSQL_USER` is set to in `.env`.

---

## Branch

All work on: `claude/automate-netrefer-looker-rhWIB`
