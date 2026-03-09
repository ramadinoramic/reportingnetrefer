-- Run this in TablePlus (Cmd+T → paste → Cmd+Enter)
-- Creates the v_netrefer_kpis view with the 5 core KPI columns

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
