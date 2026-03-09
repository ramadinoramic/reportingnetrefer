-- ============================================================
-- Netrefer Reporting Schema
-- ============================================================

CREATE DATABASE IF NOT EXISTS netrefer_reporting
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE netrefer_reporting;

-- ------------------------------------------------------------
-- Core stats table (one row per affiliate/campaign/date combo)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS netrefer_stats (
    id                  BIGINT       UNSIGNED NOT NULL AUTO_INCREMENT,

    -- Dimensions
    report_date         DATE         NOT NULL,
    affiliate_id        VARCHAR(64)  NOT NULL,
    affiliate_name      VARCHAR(255) NOT NULL DEFAULT '',
    campaign_id         VARCHAR(64)  NOT NULL DEFAULT '',
    campaign_name       VARCHAR(255) NOT NULL DEFAULT '',
    brand               VARCHAR(128) NOT NULL DEFAULT '',
    country             VARCHAR(64)  NOT NULL DEFAULT '',
    media_type          VARCHAR(64)  NOT NULL DEFAULT '',

    -- Traffic metrics
    impressions         INT UNSIGNED NOT NULL DEFAULT 0,
    clicks              INT UNSIGNED NOT NULL DEFAULT 0,

    -- Conversion metrics
    registrations       INT UNSIGNED NOT NULL DEFAULT 0,
    first_depositors    INT UNSIGNED NOT NULL DEFAULT 0,   -- FTDs
    total_depositors    INT UNSIGNED NOT NULL DEFAULT 0,

    -- Financial metrics (stored in cents to avoid float rounding)
    deposits_cents      BIGINT       NOT NULL DEFAULT 0,
    net_revenue_cents   BIGINT       NOT NULL DEFAULT 0,
    gross_revenue_cents BIGINT       NOT NULL DEFAULT 0,
    chargebacks_cents   BIGINT       NOT NULL DEFAULT 0,
    commission_cents    BIGINT       NOT NULL DEFAULT 0,

    -- Housekeeping
    loaded_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_netrefer_grain (report_date, affiliate_id, campaign_id, brand, country),
    INDEX idx_report_date      (report_date),
    INDEX idx_affiliate_id     (affiliate_id),
    INDEX idx_campaign_id      (campaign_id),
    INDEX idx_brand            (brand),
    INDEX idx_country          (country)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------
-- Aggregated view (makes Looker queries faster & simpler)
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_netrefer_daily AS
SELECT
    report_date,
    affiliate_id,
    affiliate_name,
    campaign_id,
    campaign_name,
    brand,
    country,
    media_type,
    SUM(impressions)                          AS impressions,
    SUM(clicks)                               AS clicks,
    SUM(registrations)                        AS registrations,
    SUM(first_depositors)                     AS first_depositors,
    SUM(total_depositors)                     AS total_depositors,
    SUM(deposits_cents)       / 100.0         AS deposits,
    SUM(net_revenue_cents)    / 100.0         AS net_revenue,
    SUM(gross_revenue_cents)  / 100.0         AS gross_revenue,
    SUM(chargebacks_cents)    / 100.0         AS chargebacks,
    SUM(commission_cents)     / 100.0         AS commission,
    -- Calculated ratios
    CASE WHEN SUM(clicks) > 0
         THEN ROUND(SUM(registrations) / SUM(clicks) * 100, 2)
         ELSE 0 END                           AS click_to_reg_pct,
    CASE WHEN SUM(registrations) > 0
         THEN ROUND(SUM(first_depositors) / SUM(registrations) * 100, 2)
         ELSE 0 END                           AS reg_to_ftd_pct
FROM netrefer_stats
GROUP BY
    report_date, affiliate_id, affiliate_name,
    campaign_id, campaign_name, brand, country, media_type;
