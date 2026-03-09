-- ============================================================
-- Affiliate Report Procedure
-- Usage:
--   CALL affiliate_report('Affiliate Name', '2026-03-01', '2026-03-09');
--
-- Parameters:
--   p_affiliate  – affiliate_name (partial match supported, e.g. 'Top%')
--   p_from       – start date (inclusive), format YYYY-MM-DD
--   p_to         – end date   (inclusive), format YYYY-MM-DD
-- ============================================================

DROP PROCEDURE IF EXISTS affiliate_report;

DELIMITER $$

CREATE PROCEDURE affiliate_report(
    IN p_affiliate  VARCHAR(255),
    IN p_from       DATE,
    IN p_to         DATE
)
BEGIN
    SELECT
        -- ── Identity ──────────────────────────────────────────────
        report_date,
        affiliate_name,
        affiliate_status,
        campaign_name,
        country,
        reward_plan,

        -- ── Traffic ───────────────────────────────────────────────
        clicks,
        unique_clicks,
        views,

        -- ── Conversions ───────────────────────────────────────────
        registrations                               AS signups,
        first_depositors                            AS ftds,
        depositing_customers,
        active_customers,
        transactions,

        -- ── Financials ────────────────────────────────────────────
        ROUND(deposits,         2)                  AS deposits,
        ROUND(gross_revenue,    2)                  AS gross_revenue,
        ROUND(bonuses,          2)                  AS bonuses,
        ROUND(chargebacks,      2)                  AS chargebacks,
        ROUND(net_revenue,      2)                  AS net_revenue,

        -- ── Commission ────────────────────────────────────────────
        ROUND(rev_share_reward, 2)                  AS rev_share,
        ROUND(cpa_reward,       2)                  AS cpa_reward,
        ROUND(total_reward,     2)                  AS total_commission,

        -- ── Conversion Rates ──────────────────────────────────────
        CASE WHEN clicks > 0
             THEN ROUND(registrations / clicks * 100, 2)
             ELSE 0
        END                                         AS click_to_reg_pct,

        CASE WHEN registrations > 0
             THEN ROUND(first_depositors / registrations * 100, 2)
             ELSE 0
        END                                         AS reg_to_ftd_pct,

        CASE WHEN first_depositors > 0
             THEN ROUND(net_revenue / first_depositors, 2)
             ELSE 0
        END                                         AS net_rev_per_ftd

    FROM netrefer_stats
    WHERE affiliate_name LIKE p_affiliate
      AND report_date BETWEEN p_from AND p_to
    ORDER BY report_date ASC;
END$$

DELIMITER ;
