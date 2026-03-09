# Netrefer → MySQL → Metabase Automation

Automates loading Netrefer affiliate CSV exports into MySQL, ready for Metabase dashboards.

## How it works

```
Netrefer export (CSV)
        │  save as netrefer_YYYY-MM-DD.csv
        │  drop into ./drop/
        ▼
  etl/netrefer_etl.py   ←── etl/column_map.yaml
        │  reads date from filename
        │  skips sep= line, blank rows, totals row
        ▼
  MySQL: netrefer_stats  ──► v_netrefer_daily (view)
        │
        ▼
  Metabase (http://localhost:3000)
```

---

## Setup (one time)

**1. Copy and fill in your credentials**
```bash
cp .env.example .env
# Edit MYSQL_PASSWORD and MYSQL_ROOT_PASSWORD at minimum
```

**2. Start MySQL + Metabase**
```bash
make up
```

The MySQL schema is created automatically on first boot via `sql/schema.sql`.

**3. Install Python dependencies** (for the ETL)
```bash
make setup
```

**4. Connect Metabase to MySQL**

Open http://localhost:3000, complete the Metabase setup wizard, then add the database:

| Field    | Value                     |
|----------|---------------------------|
| Type     | MySQL                     |
| Host     | `db`                      |
| Port     | `3306`                    |
| Database | `netrefer_reporting`      |
| Username | value of `MYSQL_USER`     |
| Password | value of `MYSQL_PASSWORD` |

> Note: Use `db` as the host (the Docker service name), not `localhost`.

---

## Daily workflow

1. Download your report from Netrefer
2. **Rename the file** to include the period date, e.g.:
   ```
   netrefer_2024-01-31.csv
   ```
3. Drop it into the `./drop/` folder
4. Run:
   ```bash
   make load-dir
   ```

Processed files are automatically moved to `./drop/processed/`.

---

## Makefile commands

| Command                       | What it does                        |
|-------------------------------|-------------------------------------|
| `make up`                     | Start MySQL + Metabase              |
| `make down`                   | Stop containers                     |
| `make logs`                   | Tail all logs                       |
| `make logs service=metabase`  | Tail Metabase logs only             |
| `make db-shell`               | Open a MySQL shell in the container |
| `make load-dir`               | Load all CSVs from `./drop/`        |
| `make load FILE=path/to.csv`  | Load a single CSV file              |
| `make setup`                  | Install Python ETL dependencies     |

---

## File naming convention

The date in the filename tells the ETL which period the data belongs to
(Netrefer CSVs don't have a date column).

| Filename                  | Detected date |
|---------------------------|---------------|
| `netrefer_2024-01-31.csv` | 2024-01-31    |
| `report_2024-01-31.csv`   | 2024-01-31    |
| `january_2024-01-01.csv`  | 2024-01-01    |

Any filename containing `YYYY-MM-DD` anywhere works.

---

## Column mapping

If Netrefer ever changes their export headers, edit `etl/column_map.yaml`.
No code changes needed — just add or update the mapping there.

---

## Data available in Metabase

| Category         | Fields                                                               |
|------------------|----------------------------------------------------------------------|
| Traffic          | Views, Unique Views, Clicks, Unique Clicks                           |
| Conversions      | Signups, Depositing Customers, FTDs, Active Customers                |
| Conversion rates | Click→Signup %, Signup→FTD %                                         |
| Financials       | Deposits, Turnover, Gross Revenue, Net Revenue, Bonuses, Chargebacks |
| Rewards          | Revenue Share, CPA, Sub-Affiliate, Total Reward                      |
| KPIs             | Net Revenue / FTD, Reward / FTD                                      |

Use Metabase's **Question** builder or **SQL editor** against `v_netrefer_daily`
or `v_netrefer_kpis` to build your dashboards.
