-- ============================================================
-- Netrefer Reporting Schema
-- Run: mysql -h HOST -u USER -pPASS < sql/schema.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS netrefer_reporting
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE netrefer_reporting;

-- ------------------------------------------------------------
-- Core stats table
-- Grain: one row per (report_date, affiliate_id, campaign_name)
-- report_date is supplied via --date when running the ETL
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS netrefer_stats (
    id                          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    -- Period (passed via --date flag, not in the CSV)
    report_date                 DATE            NOT NULL,

    -- Affiliate identity
    affiliate_id                VARCHAR(64)     NOT NULL,
    affiliate_name              VARCHAR(255)    NOT NULL DEFAULT '',
    affiliate_email             VARCHAR(255)    NOT NULL DEFAULT '',
    affiliate_status            VARCHAR(64)     NOT NULL DEFAULT '',
    affiliate_signup_date       DATE            NULL,

    -- Campaign / plan
    campaign_name               VARCHAR(255)    NOT NULL DEFAULT '',
    media_type                  VARCHAR(255)    NOT NULL DEFAULT '',
    reward_plan_id              VARCHAR(64)     NOT NULL DEFAULT '',
    reward_plan                 VARCHAR(255)    NOT NULL DEFAULT '',
    country                     VARCHAR(64)     NOT NULL DEFAULT '',

    -- Traffic
    views                       INT UNSIGNED    NOT NULL DEFAULT 0,
    unique_views                INT UNSIGNED    NOT NULL DEFAULT 0,
    clicks                      INT UNSIGNED    NOT NULL DEFAULT 0,
    unique_clicks               INT UNSIGNED    NOT NULL DEFAULT 0,

    -- Conversions
    registrations               INT UNSIGNED    NOT NULL DEFAULT 0,
    depositing_customers        INT UNSIGNED    NOT NULL DEFAULT 0,
    active_customers            INT UNSIGNED    NOT NULL DEFAULT 0,
    new_depositing_customers    INT UNSIGNED    NOT NULL DEFAULT 0,
    new_active_customers        INT UNSIGNED    NOT NULL DEFAULT 0,
    first_depositors            INT UNSIGNED    NOT NULL DEFAULT 0,
    first_active_customers      INT UNSIGNED    NOT NULL DEFAULT 0,
    transactions                INT UNSIGNED    NOT NULL DEFAULT 0,
    sub_affiliates              INT UNSIGNED    NOT NULL DEFAULT 0,

    -- Financials (DECIMAL for exact money math)
    deposits                    DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    turnover                    DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    contributions               DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    payouts                     DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    gross_revenue               DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    bonuses                     DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    adj_general                 DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    chargebacks                 DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    net_revenue                 DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,

    -- Rewards / commission breakdown
    rev_share_reward            DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    cpa_reward                  DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    sub_affiliate_reward        DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    other_rewards               DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,
    total_reward                DECIMAL(15,4)   NOT NULL DEFAULT 0.0000,

    -- Housekeeping
    loaded_at                   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                                ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_grain         (report_date, affiliate_id, campaign_name(100)),
    INDEX idx_report_date       (report_date),
    INDEX idx_affiliate_id      (affiliate_id),
    INDEX idx_country           (country),
    INDEX idx_campaign_name     (campaign_name(100))

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- View used by Looker
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_netrefer_daily AS
SELECT
    report_date,
    affiliate_id,
    affiliate_name,
    affiliate_email,
    affiliate_status,
    affiliate_signup_date,
    campaign_name,
    media_type,
    reward_plan,
    country,

    views, unique_views, clicks, unique_clicks,

    registrations,
    depositing_customers,
    active_customers,
    new_depositing_customers,
    new_active_customers,
    first_depositors,
    first_active_customers,
    transactions,

    deposits, turnover, gross_revenue, bonuses,
    adj_general, chargebacks, net_revenue,
    contributions, payouts,

    rev_share_reward, cpa_reward, sub_affiliate_reward,
    other_rewards, total_reward,

    -- Calculated conversion rates
    CASE WHEN clicks > 0
         THEN ROUND(registrations / clicks * 100, 2) ELSE 0
    END AS click_to_reg_pct,

    CASE WHEN registrations > 0
         THEN ROUND(first_depositors / registrations * 100, 2) ELSE 0
    END AS reg_to_ftd_pct,

    CASE WHEN first_depositors > 0
         THEN ROUND(net_revenue / first_depositors, 4) ELSE 0
    END AS net_revenue_per_ftd

FROM netrefer_stats;


-- ------------------------------------------------------------
-- Summary view: key KPIs only
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_netrefer_kpis AS
SELECT
    report_date,
    affiliate_id,
    affiliate_name,
    campaign_name,
    country,

    SUM(clicks)           AS clicks,
    SUM(registrations)    AS signups,
    SUM(first_depositors) AS ftds,
    SUM(deposits)         AS deposits,
    SUM(net_revenue)      AS net_gaming_revenue

FROM netrefer_stats
GROUP BY
    report_date,
    affiliate_id,
    affiliate_name,
    campaign_name,
    country;


-- ------------------------------------------------------------
-- ETL audit log
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etl_runs (
    run_id          VARCHAR(64)                          NOT NULL,
    started_at      TIMESTAMP                            NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP                            NULL,
    mode            ENUM('api','csv')                    NOT NULL,
    source_detail   TEXT                                 NULL,
    rows_upserted   INT UNSIGNED                         NOT NULL DEFAULT 0,
    rows_parsed     INT UNSIGNED                         NOT NULL DEFAULT 0
                        COMMENT 'Rows parsed from CSV (before UPSERT de-dup)',
    status          ENUM('running','success','failed')   NOT NULL DEFAULT 'running',
    error_message   TEXT                                 NULL,
    warnings        TEXT                                 NULL
                        COMMENT 'Semicolon-separated DQ warnings for this run',
    PRIMARY KEY (run_id),
    INDEX idx_started_at (started_at),
    INDEX idx_status     (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
