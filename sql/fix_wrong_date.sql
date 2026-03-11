-- ============================================================
-- Fix rows loaded under the wrong report_date
--
-- Use this when a CSV was loaded with the wrong date
-- (e.g. March 10 data ended up as March 9).
--
-- Step 1: verify what you're about to change
-- Step 2: run the UPDATE
-- Step 3: confirm
--
-- Replace WRONG_DATE and CORRECT_DATE before running.
-- ============================================================

USE netrefer_reporting;

-- ── Step 1: preview ─────────────────────────────────────────
-- Shows the rows that will be moved and what already exists on
-- the correct date (to check for conflicts before updating).

SELECT 'rows to move' AS check_type, report_date, COUNT(*) AS rows
FROM   netrefer_stats
WHERE  report_date = 'WRONG_DATE'
GROUP  BY report_date

UNION ALL

SELECT 'rows already on correct date', report_date, COUNT(*)
FROM   netrefer_stats
WHERE  report_date = 'CORRECT_DATE'
GROUP  BY report_date;


-- ── Step 2: move the rows ───────────────────────────────────
-- Only safe to run if "rows already on correct date" above = 0.
-- If there IS existing data on the correct date, delete the
-- wrong-date rows and re-load the file instead (see Step 2b).

-- Step 2a: simple UPDATE (no existing data on correct date)
UPDATE netrefer_stats
SET    report_date = 'CORRECT_DATE'
WHERE  report_date = 'WRONG_DATE';

-- Step 2b: if there IS a conflict — delete wrong rows + reload
-- DELETE FROM netrefer_stats WHERE report_date = 'WRONG_DATE';
-- Then: python etl/netrefer_etl.py --file <your_file> --date CORRECT_DATE


-- ── Step 3: also fix the etl_runs audit log ─────────────────
-- Optional but keeps the audit trail clean.
-- Update the source_detail to reflect the corrected date.
-- UPDATE etl_runs
-- SET    source_detail = REPLACE(source_detail, 'WRONG_DATE', 'CORRECT_DATE')
-- WHERE  source_detail LIKE '%WRONG_DATE%'
--   AND  status = 'success';


-- ── Step 4: confirm ─────────────────────────────────────────
SELECT report_date, COUNT(*) AS rows
FROM   netrefer_stats
WHERE  report_date IN ('WRONG_DATE', 'CORRECT_DATE')
GROUP  BY report_date;
