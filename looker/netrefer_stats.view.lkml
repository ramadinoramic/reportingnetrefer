view: netrefer_stats {
  sql_table_name: netrefer_reporting.v_netrefer_daily ;;

  # ── Date ────────────────────────────────────────────────────────────────────
  dimension_group: report {
    type: time
    timeframes: [date, week, month, quarter, year]
    convert_tz: no
    sql: ${TABLE}.report_date ;;
    label: "Report"
  }

  # ── Affiliate ────────────────────────────────────────────────────────────────
  dimension: affiliate_id {
    type: string
    sql: ${TABLE}.affiliate_id ;;
    label: "Affiliate ID"
  }

  dimension: affiliate_name {
    type: string
    sql: ${TABLE}.affiliate_name ;;
    label: "Affiliate Name"
  }

  dimension: affiliate_email {
    type: string
    sql: ${TABLE}.affiliate_email ;;
    label: "Affiliate Email"
  }

  dimension: affiliate_status {
    type: string
    sql: ${TABLE}.affiliate_status ;;
    label: "Affiliate Status"
  }

  dimension_group: affiliate_signup {
    type: time
    timeframes: [date, month, year]
    convert_tz: no
    sql: ${TABLE}.affiliate_signup_date ;;
    label: "Affiliate Signup"
  }

  # ── Campaign / Plan ──────────────────────────────────────────────────────────
  dimension: campaign_name {
    type: string
    sql: ${TABLE}.campaign_name ;;
    label: "Campaign"
  }

  dimension: media_type {
    type: string
    sql: ${TABLE}.media_type ;;
    label: "Media Type"
  }

  dimension: reward_plan {
    type: string
    sql: ${TABLE}.reward_plan ;;
    label: "Reward Plan"
  }

  dimension: country {
    type: string
    map_layer_name: countries
    sql: ${TABLE}.country ;;
    label: "Country"
  }

  # ── Traffic Measures ─────────────────────────────────────────────────────────
  measure: total_views {
    type: sum
    sql: ${TABLE}.views ;;
    label: "Views"
    value_format_name: decimal_0
  }

  measure: total_unique_views {
    type: sum
    sql: ${TABLE}.unique_views ;;
    label: "Unique Views"
    value_format_name: decimal_0
  }

  measure: total_clicks {
    type: sum
    sql: ${TABLE}.clicks ;;
    label: "Clicks"
    value_format_name: decimal_0
  }

  measure: total_unique_clicks {
    type: sum
    sql: ${TABLE}.unique_clicks ;;
    label: "Unique Clicks"
    value_format_name: decimal_0
  }

  # ── Conversion Measures ──────────────────────────────────────────────────────
  measure: total_registrations {
    type: sum
    sql: ${TABLE}.registrations ;;
    label: "Customer Signups"
    value_format_name: decimal_0
  }

  measure: total_depositing_customers {
    type: sum
    sql: ${TABLE}.depositing_customers ;;
    label: "Depositing Customers"
    value_format_name: decimal_0
  }

  measure: total_active_customers {
    type: sum
    sql: ${TABLE}.active_customers ;;
    label: "Active Customers"
    value_format_name: decimal_0
  }

  measure: total_new_depositing {
    type: sum
    sql: ${TABLE}.new_depositing_customers ;;
    label: "New Depositing Customers"
    value_format_name: decimal_0
  }

  measure: total_new_active {
    type: sum
    sql: ${TABLE}.new_active_customers ;;
    label: "New Active Customers"
    value_format_name: decimal_0
  }

  measure: total_ftds {
    type: sum
    sql: ${TABLE}.first_depositors ;;
    label: "First Time Depositing Customers"
    value_format_name: decimal_0
  }

  measure: total_first_active {
    type: sum
    sql: ${TABLE}.first_active_customers ;;
    label: "First Time Active Customers"
    value_format_name: decimal_0
  }

  measure: total_transactions {
    type: sum
    sql: ${TABLE}.transactions ;;
    label: "Transactions"
    value_format_name: decimal_0
  }

  # ── Conversion Rates ─────────────────────────────────────────────────────────
  measure: click_to_signup_rate {
    type: number
    sql: NULLIF(${total_registrations}, 0) / NULLIF(${total_clicks}, 0) ;;
    label: "Click → Signup Rate"
    value_format_name: percent_2
  }

  measure: signup_to_ftd_rate {
    type: number
    sql: NULLIF(${total_ftds}, 0) / NULLIF(${total_registrations}, 0) ;;
    label: "Signup → FTD Rate"
    value_format_name: percent_2
  }

  # ── Financial Measures ───────────────────────────────────────────────────────
  measure: total_deposits {
    type: sum
    sql: ${TABLE}.deposits ;;
    label: "Deposits"
    value_format_name: usd
  }

  measure: total_turnover {
    type: sum
    sql: ${TABLE}.turnover ;;
    label: "Turnover"
    value_format_name: usd
  }

  measure: total_gross_revenue {
    type: sum
    sql: ${TABLE}.gross_revenue ;;
    label: "Gross Revenue"
    value_format_name: usd
  }

  measure: total_bonuses {
    type: sum
    sql: ${TABLE}.bonuses ;;
    label: "Bonuses"
    value_format_name: usd
  }

  measure: total_chargebacks {
    type: sum
    sql: ${TABLE}.chargebacks ;;
    label: "Adj (Chargebacks)"
    value_format_name: usd
  }

  measure: total_adj_general {
    type: sum
    sql: ${TABLE}.adj_general ;;
    label: "Adj (General)"
    value_format_name: usd
  }

  measure: total_net_revenue {
    type: sum
    sql: ${TABLE}.net_revenue ;;
    label: "Net Revenue"
    value_format_name: usd
  }

  measure: total_contributions {
    type: sum
    sql: ${TABLE}.contributions ;;
    label: "Contributions"
    value_format_name: usd
  }

  measure: total_payouts {
    type: sum
    sql: ${TABLE}.payouts ;;
    label: "Payouts"
    value_format_name: usd
  }

  # ── Reward Measures ──────────────────────────────────────────────────────────
  measure: total_rev_share_reward {
    type: sum
    sql: ${TABLE}.rev_share_reward ;;
    label: "Revenue Share Reward"
    value_format_name: usd
  }

  measure: total_cpa_reward {
    type: sum
    sql: ${TABLE}.cpa_reward ;;
    label: "CPA Reward"
    value_format_name: usd
  }

  measure: total_sub_affiliate_reward {
    type: sum
    sql: ${TABLE}.sub_affiliate_reward ;;
    label: "Sub-Affiliate Reward"
    value_format_name: usd
  }

  measure: total_other_rewards {
    type: sum
    sql: ${TABLE}.other_rewards ;;
    label: "Other Rewards"
    value_format_name: usd
  }

  measure: total_reward {
    type: sum
    sql: ${TABLE}.total_reward ;;
    label: "Total Reward"
    value_format_name: usd
  }

  # ── Efficiency KPIs ──────────────────────────────────────────────────────────
  measure: net_revenue_per_ftd {
    type: number
    sql: NULLIF(${total_net_revenue}, 0) / NULLIF(${total_ftds}, 0) ;;
    label: "Net Revenue / FTD"
    value_format_name: usd
  }

  measure: reward_per_ftd {
    type: number
    sql: NULLIF(${total_reward}, 0) / NULLIF(${total_ftds}, 0) ;;
    label: "Total Reward / FTD"
    value_format_name: usd
  }

  measure: count_affiliates {
    type: count_distinct
    sql: ${affiliate_id} ;;
    label: "# Affiliates"
  }
}
