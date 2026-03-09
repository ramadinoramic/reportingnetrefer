connection: "netrefer_mysql"  # Must match the connection name in your Looker Admin

label: "Netrefer Affiliate Reporting"

include: "/looker/*.view.lkml"

# ── Main explore ─────────────────────────────────────────────────────────────
explore: netrefer_stats {
  label: "Affiliate Stats"
  description: "Daily affiliate performance data from Netrefer"

  always_filter: {
    filters: [netrefer_stats.report_date: "30 days"]
  }

  aggregate_table: rollup__affiliate_month {
    query: {
      dimensions: [
        netrefer_stats.report_month,
        netrefer_stats.affiliate_name,
        netrefer_stats.brand,
        netrefer_stats.country
      ]
      measures: [
        netrefer_stats.total_clicks,
        netrefer_stats.total_registrations,
        netrefer_stats.total_ftds,
        netrefer_stats.total_net_revenue,
        netrefer_stats.total_commission
      ]
    }
    materialization: {
      sql_trigger_value: SELECT MAX(updated_at) FROM netrefer_reporting.netrefer_stats ;;
    }
  }
}
