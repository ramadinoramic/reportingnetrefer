-- Top affiliates by Signups and FTDs over the last 7 days
-- Grain: one row per affiliate, aggregated across the rolling 7-day window

CREATE OR REPLACE VIEW v_top_affiliates_7d AS
SELECT
    affiliate_id,
    affiliate_name,
    affiliate_email,

    -- Signup performance
    SUM(registrations)                                      AS signups,
    RANK() OVER (ORDER BY SUM(registrations) DESC)          AS signup_rank,

    -- FTD performance
    SUM(first_depositors)                                   AS ftds,
    RANK() OVER (ORDER BY SUM(first_depositors) DESC)       AS ftd_rank,

    -- Supporting metrics
    SUM(clicks)                                             AS clicks,
    SUM(deposits)                                           AS deposits,
    SUM(net_revenue)                                        AS net_revenue,

    -- Conversion rates
    ROUND(
        100.0 * SUM(registrations)
              / NULLIF(SUM(clicks), 0),
        2
    )                                                       AS click_to_reg_pct,
    ROUND(
        100.0 * SUM(first_depositors)
              / NULLIF(SUM(registrations), 0),
        2
    )                                                       AS reg_to_ftd_pct,

    -- Window info
    MIN(report_date)                                        AS period_from,
    MAX(report_date)                                        AS period_to,
    COUNT(DISTINCT report_date)                             AS days_with_data

FROM netrefer_stats
WHERE report_date >= CURDATE() - INTERVAL 7 DAY
  AND report_date <  CURDATE()          -- exclude today (partial day)
GROUP BY
    affiliate_id,
    affiliate_name,
    affiliate_email
ORDER BY
    ftd_rank ASC,
    signup_rank ASC;
