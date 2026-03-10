-- ============================================================
-- Migration: broaden unique key to include campaign_name
--
-- Why: the old key (report_date, affiliate_id) caused data loss
-- when an affiliate had multiple campaigns in the same CSV —
-- each UPSERT overwrote the previous campaign's row, so only
-- the last campaign survived per affiliate per day.
--
-- Run ONCE on existing installs:
--   mysql -h HOST -u USER -pPASS DATABASE < sql/migrate_unique_key.sql
-- ============================================================

USE netrefer_reporting;

-- Step 1: Remove true duplicates within the new grain
-- (rows with identical report_date + affiliate_id + campaign_name,
--  keeping the one with the highest id, i.e. the latest upsert)
DELETE n1
FROM netrefer_stats n1
INNER JOIN netrefer_stats n2
  ON  n1.report_date   = n2.report_date
 AND  n1.affiliate_id  = n2.affiliate_id
 AND  n1.campaign_name = n2.campaign_name
 AND  n1.id < n2.id;

-- Step 2: Drop the old narrow unique key
ALTER TABLE netrefer_stats
  DROP INDEX uq_grain;

-- Step 3: Add the new unique key that preserves per-campaign rows
ALTER TABLE netrefer_stats
  ADD UNIQUE KEY uq_grain (report_date, affiliate_id, campaign_name(100));

SELECT 'Migration complete. Unique key now covers (report_date, affiliate_id, campaign_name).' AS result;
