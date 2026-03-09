# Netrefer → MySQL → Looker Automation

Automates loading Netrefer affiliate CSV exports into MySQL, ready for Looker.

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
  Looker explore (LookML)
```

---

## Setup (one time)

**1. Install dependencies**
```bash
pip install -r requirements.txt
```

**2. Configure database credentials**
```bash
cp .env.example .env
# Fill in MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
```

**3. Create the MySQL table**
```bash
make db-init
```

**4. Connect Looker**
- Looker Admin → Connections → New → MySQL, name it **`netrefer_mysql`**
- Copy the `looker/` folder into your LookML project and deploy

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

That's it. Processed files are automatically moved to `./drop/processed/`.

---

## File naming convention

The date in the filename tells the script which period the data belongs to
(since Netrefer CSVs don't have a date column).

| Filename | Detected date |
|---|---|
| `netrefer_2024-01-31.csv` | 2024-01-31 |
| `report_2024-01-31.csv` | 2024-01-31 |
| `january_2024-01-01.csv` | 2024-01-01 |

Any filename containing `YYYY-MM-DD` anywhere works.

---

## Column mapping

If Netrefer ever changes their export headers, edit `etl/column_map.yaml`.
No code changes needed — just add or update the mapping there.

---

## Looker measures available

| Category | Measures |
|---|---|
| Traffic | Views, Unique Views, Clicks, Unique Clicks |
| Conversions | Signups, Depositing Customers, FTDs, Active Customers |
| Conversion rates | Click→Signup, Signup→FTD |
| Financials | Deposits, Turnover, Gross Revenue, Net Revenue, Bonuses, Chargebacks |
| Rewards | Revenue Share, CPA, Sub-Affiliate, Total Reward |
| KPIs | Net Revenue / FTD, Reward / FTD |
