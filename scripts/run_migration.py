#!/usr/bin/env python3
"""
Run the unique-key migration using the same DB connection as the ETL.
Usage:  python scripts/run_migration.py
"""
import os, sys
from pathlib import Path

# Load .env the same way the ETL does
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import mysql.connector

conn = mysql.connector.connect(
    host=os.environ["MYSQL_HOST"],
    port=int(os.getenv("MYSQL_PORT", 3306)),
    user=os.environ["MYSQL_USER"],
    password=os.environ["MYSQL_PASSWORD"],
    database=os.environ["MYSQL_DATABASE"],
    charset="utf8mb4",
    autocommit=True,
)
cursor = conn.cursor()

print("Connected to MySQL. Checking current state...")

# Check current unique key
cursor.execute("SHOW INDEX FROM netrefer_stats WHERE Key_name = 'uq_grain'")
rows = cursor.fetchall()
columns_in_key = [r[4] for r in rows]  # Column_name is index 4
print(f"  Current uq_grain columns: {columns_in_key}")

if "campaign_name" in columns_in_key:
    print("\nMigration already done! Nothing to do.")
    sys.exit(0)

# Step 1: Remove duplicates
print("\nStep 1: Removing duplicates...")
cursor.execute("""
    DELETE n1 FROM netrefer_stats n1
    INNER JOIN netrefer_stats n2
      ON  n1.report_date   = n2.report_date
     AND  n1.affiliate_id  = n2.affiliate_id
     AND  n1.campaign_name = n2.campaign_name
     AND  n1.id < n2.id
""")
print(f"  {cursor.rowcount} duplicate rows removed")

# Step 2: Drop old unique key
print("Step 2: Dropping old unique key...")
cursor.execute("ALTER TABLE netrefer_stats DROP INDEX uq_grain")
print("  Done")

# Step 3: Add new unique key
print("Step 3: Adding new unique key (report_date, affiliate_id, campaign_name)...")
cursor.execute("""
    ALTER TABLE netrefer_stats
      ADD UNIQUE KEY uq_grain (report_date, affiliate_id, campaign_name(100))
""")
print("  Done")

cursor.close()
conn.close()
print("\nMigration complete!")
