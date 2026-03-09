-- ============================================================
-- Affiliate Daily Report View
-- Open this view in TablePlus and use the Filter bar to:
--   • Filter by affiliate_name  → search one affiliate
--   • Filter by report_date     → pick a date or date range
--   • Filter by country         → geo breakdown
--   • Filter by campaign_name   → campaign breakdown
-- ============================================================

CREATE OR REPLACE VIEW v_affiliate_daily_report AS
SELECT
    -- ── Identity ──────────────────────────────────────────────
    report_date,
    affiliate_id,
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
ORDER BY report_date DESC, net_revenue DESC;
