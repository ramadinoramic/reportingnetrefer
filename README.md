# Netrefer → MySQL → Metabase Automation

Automates loading Netrefer affiliate CSV exports into MySQL and building Metabase dashboards.

## How it works

```
Netrefer export (CSV)
        │  rename to netrefer_YYYY-MM-DD.csv
        │  drop into ./drop/
        ▼
  etl/netrefer_etl.py   ←── etl/column_map.yaml
        │  reads date from filename
        │  skips sep= line, blank rows, totals row
        │  UPSERTs into MySQL (idempotent)
        ▼
  MySQL: netrefer_stats  ──► v_netrefer_daily (view)
        │
        ▼
  Metabase (http://localhost:3000)
    ├── Affiliate Deep Dive dashboard
    └── Board Report dashboard
```

---

## Setup (one time)

**1. Copy and fill in your credentials**
```bash
cp .env.example .env
# Edit MYSQL_PASSWORD and MYSQL_ROOT_PASSWORD at minimum
```

**2. Start MySQL + ETL watcher**
```bash
make up
```

The MySQL schema (`sql/schema.sql`) is created automatically on first boot.

**3. Install Python dependencies**
```bash
make setup
```

**4. Connect Metabase to MySQL**

Open http://localhost:3000, complete the setup wizard, then add the database:

| Field    | Value                     |
|----------|---------------------------|
| Type     | MySQL                     |
| Host     | `db`                      |
| Port     | `3306`                    |
| Database | `netrefer_reporting`      |
| Username | value of `MYSQL_USER`     |
| Password | value of `MYSQL_PASSWORD` |

> Use `db` as the host (the Docker service name), not `localhost`.

**5. Create dashboards**
```bash
make affiliate-dashboard USER=admin@example.com PASSWORD=secret
make board-dashboard     USER=admin@example.com PASSWORD=secret
```

---

## Daily workflow

1. Download your report from Netrefer
2. Rename the file to include the period date:
   ```
   netrefer_2026-03-09.csv
   ```
3. Drop it into the `./drop/` folder — the ETL watcher picks it up automatically.
   Or run manually:
   ```bash
   make load-dir
   ```

Processed files are moved to `./drop/processed/` automatically.

---

## Dashboards

### Affiliate Deep Dive (`setup_affiliate_dashboard.py`)

Operational dashboard for day-to-day affiliate analysis.

**Filters:**
- **Affiliate Name** — dropdown populated from the database
- **From Date** — lower bound on `report_date` (inclusive)
- **To Date** — upper bound on `report_date` (inclusive)

Both date filters are optional and independent. When a range is selected, all
cards aggregate across the full range.

**Cards:**
| Card | Type | Description |
|------|------|-------------|
| AF – Clicks | Scalar | Total clicks in range |
| AF – Registrations | Scalar | Total registrations in range |
| AF – FTDs | Scalar | Total first depositors in range |
| AF – Net Revenue | Scalar | Total net revenue in range (EUR) |
| AF – Deposits | Scalar | Total deposits in range (EUR) |
| AF – Daily Revenue Trend | Line chart | Net revenue + deposits by day |
| AF – Daily Conversions | Line chart | Clicks / registrations / FTDs by day |
| AF – Conversion Funnel | Bar chart | Clicks → Registrations → FTDs totals |
| AF – Revenue by Campaign | Bar chart | Net revenue per campaign name |
| AF – Revenue by Country | Pie chart | Net revenue share by country |
| AF – Daily Detail Table | Table | One row per affiliate — totals for selected range |
| AF – Top 10 Affiliates (Last 7 Days) | Table | Fixed: top 10 by FTDs over last 7 days |

### Board Report (`setup_board_dashboard.py`)

Executive dashboard for leadership / C-level review. No date filters — always
shows the full dataset aggregated by month.

---

## Makefile commands

| Command | What it does |
|---------|-------------|
| `make up` | Start MySQL + ETL watcher (Docker) |
| `make down` | Stop containers (data preserved) |
| `make logs` | Tail all logs |
| `make logs service=db` | Tail a specific service |
| `make db-shell` | Open a MySQL shell in the container |
| `make load-dir` | Load all CSVs from `./drop/` |
| `make load FILE=path/to.csv` | Load a single CSV file |
| `make setup` | Install Python ETL dependencies |
| `make affiliate-dashboard USER=x PASSWORD=y` | Create/update Affiliate Deep Dive dashboard |
| `make board-dashboard USER=x PASSWORD=y` | Create/update Board Report dashboard |
| `make etl-dashboard USER=x PASSWORD=y` | Create/update ETL Health dashboard |
| `make board-report` | Generate HTML board report (latest month) |
| `make board-report MONTH=2026-03` | HTML report for a specific month |
| `make board-report FROM=2026-03-01 TO=2026-03-08` | HTML report for custom range |
| `make watch` | Watch `./drop/` and auto-load new CSVs (foreground) |
| `make audit-csv FILE=drop/netrefer_2026-03-09.csv` | Audit a CSV before loading |
| `make install-cron EMAIL=you@example.com` | Monthly cron to email board report |

---

## File naming convention

The date in the filename tells the ETL which period the data belongs to
(Netrefer CSVs have no date column).

| Filename | Detected date |
|----------|---------------|
| `netrefer_2024-01-31.csv` | 2024-01-31 |
| `report_2024-01-31.csv` | 2024-01-31 |
| `january_2024-01-01.csv` | 2024-01-01 |

Any filename containing `YYYY-MM-DD` anywhere works.

---

## Column mapping

If Netrefer changes their export headers, edit `etl/column_map.yaml`.
No code changes needed — just add or update the mapping there.

---

## Data available in Metabase

| Category | Fields |
|----------|--------|
| Traffic | Views, Unique Views, Clicks, Unique Clicks |
| Conversions | Signups, Depositing Customers, FTDs, Active Customers |
| Conversion rates | Click→Signup %, Signup→FTD % |
| Financials | Deposits, Turnover, Gross Revenue, Net Revenue, Bonuses, Chargebacks |
| Rewards | Revenue Share, CPA, Sub-Affiliate, Total Reward |

Use Metabase's SQL editor against `v_netrefer_daily` or `v_netrefer_kpis` to build custom questions.
