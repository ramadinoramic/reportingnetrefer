-- ============================================================
-- Bahigo & Wettigo multi-brand reporting schema
-- Grain: one row per (report_date, brand_name, affiliate_id, geo)
--
-- Source: two daily CSV files merged by Affiliate ID:
--   netrefer_YYYY-MM-DD.csv         → affiliate stats (clicks, commission)
--   netrefer_custom_YYYY-MM-DD.csv  → customer report (brand, geo, revenue, FTDs)
-- ============================================================

USE netrefer_reporting;

CREATE TABLE IF NOT EXISTS bahigo_wettigo_stats (
    id                      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    -- Grain dimensions
    report_date             DATE            NOT NULL,
    brand_name              VARCHAR(64)     NOT NULL,   -- 'Bahigo' | 'Wettigo'
    geo                     VARCHAR(128)    NOT NULL,   -- customer country e.g. 'Turkey', 'Switzerland'

    -- Affiliate identity (from affiliate stats)
    affiliate_id            VARCHAR(64)     NOT NULL,
    affiliate_name          VARCHAR(255)    NOT NULL DEFAULT '',
    affiliate_email         VARCHAR(255)    NOT NULL DEFAULT '',
    affiliate_status        VARCHAR(64)     NOT NULL DEFAULT '',
    affiliate_signup_date   DATE            NULL,
    campaign_name           VARCHAR(255)    NOT NULL DEFAULT '',
    reward_plan             VARCHAR(255)    NOT NULL DEFAULT '',

    -- Traffic — from affiliate stats, split proportionally by brand/geo weight
    views                   INT UNSIGNED    NOT NULL DEFAULT 0,
    unique_views            INT UNSIGNED    NOT NULL DEFAULT 0,
    clicks                  INT UNSIGNED    NOT NULL DEFAULT 0,
    unique_clicks           INT UNSIGNED    NOT NULL DEFAULT 0,

    -- Conversions — from customer report (exact counts per brand/geo)
    registrations           INT UNSIGNED    NOT NULL DEFAULT 0,
    depositing_customers    INT UNSIGNED    NOT NULL DEFAULT 0,
    first_depositors        INT UNSIGNED    NOT NULL DEFAULT 0,

    -- Financials — from customer report (exact sums per brand/geo)
    deposits                DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    gross_revenue           DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    bonuses                 DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    adj_general             DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    net_revenue             DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,

    -- Commission — from affiliate stats, split proportionally
    rev_share_reward        DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    cpa_reward              DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    total_reward            DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,

    -- Housekeeping
    loaded_at               TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                            ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_grain     (report_date, brand_name, affiliate_id, geo(64)),
    INDEX idx_report_date   (report_date),
    INDEX idx_brand_name    (brand_name),
    INDEX idx_affiliate_id  (affiliate_id),
    INDEX idx_geo           (geo)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
