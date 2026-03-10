-- ============================================================
-- Migration: add rows_parsed + warnings to etl_runs
-- Idempotent (IF NOT EXISTS) — safe to run on existing installs
-- Run: make migrate-etl
-- ============================================================

ALTER TABLE etl_runs
    ADD COLUMN IF NOT EXISTS rows_parsed  INT UNSIGNED  NOT NULL DEFAULT 0
        COMMENT 'Rows parsed from CSV (before UPSERT de-dup)'
        AFTER rows_upserted,
    ADD COLUMN IF NOT EXISTS warnings     TEXT          NULL
        COMMENT 'Semicolon-separated DQ warnings for this run'
        AFTER error_message;
