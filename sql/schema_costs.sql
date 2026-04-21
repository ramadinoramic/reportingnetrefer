-- ============================================================
-- Cost tracking tables for CPA / CPL / CPM calculation
--
-- Two tables:
--   affiliate_deals   — negotiated deal terms per affiliate (synced from Google Sheets)
--   traffic_costs     — daily cost records (auto-calculated from deals + manual overrides)
-- ============================================================

USE netrefer_reporting;

-- Deal terms per affiliate (rarely changes)
CREATE TABLE IF NOT EXISTS affiliate_deals (
    id                  BIGINT UNSIGNED     NOT NULL AUTO_INCREMENT,

    affiliate_id        VARCHAR(64)         NOT NULL,
    brand               VARCHAR(64)         NOT NULL DEFAULT '',   -- '' = applies to all brands
    deal_type           ENUM('CPA','RevShare','Flat','Hybrid') NOT NULL,

    -- Cost rates (only the relevant field(s) for the deal_type need to be set)
    cpa_rate_eur        DECIMAL(10,2)       NOT NULL DEFAULT 0.00, -- EUR per FTD
    revshare_pct        DECIMAL(5,2)        NOT NULL DEFAULT 0.00, -- percentage, e.g. 35.00
    flat_monthly_eur    DECIMAL(12,2)       NOT NULL DEFAULT 0.00, -- EUR per calendar month

    valid_from          DATE                NOT NULL,
    valid_to            DATE                NULL,                  -- NULL = ongoing

    notes               TEXT,
    synced_at           TIMESTAMP           NOT NULL DEFAULT CURRENT_TIMESTAMP
                                            ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_deal          (affiliate_id, brand, valid_from),
    INDEX idx_affiliate_id      (affiliate_id),
    INDEX idx_valid_from        (valid_from),
    INDEX idx_valid_to          (valid_to)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Daily cost records (one row per date + affiliate + brand + geo + source)
CREATE TABLE IF NOT EXISTS traffic_costs (
    id                  BIGINT UNSIGNED     NOT NULL AUTO_INCREMENT,

    report_date         DATE                NOT NULL,
    affiliate_id        VARCHAR(64)         NOT NULL,
    brand               VARCHAR(64)         NOT NULL DEFAULT '',
    geo                 VARCHAR(128)        NOT NULL DEFAULT '',

    cost_eur            DECIMAL(12,4)       NOT NULL DEFAULT 0.0000,

    -- 'deal'   = auto-calculated from affiliate_deals x actual performance
    -- 'manual' = entered manually in the Google Sheet "Manual Costs" tab
    cost_source         VARCHAR(16)         NOT NULL DEFAULT 'deal',

    -- Human-readable label: 'CPA', 'RevShare', 'Flat', 'ad_spend', 'bonus', 'other'
    cost_type           VARCHAR(32)         NOT NULL DEFAULT '',

    notes               TEXT,
    synced_at           TIMESTAMP           NOT NULL DEFAULT CURRENT_TIMESTAMP
                                            ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_cost          (report_date, affiliate_id, brand, geo, cost_source),
    INDEX idx_report_date       (report_date),
    INDEX idx_affiliate_id      (affiliate_id),
    INDEX idx_brand             (brand),
    INDEX idx_cost_source       (cost_source)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
