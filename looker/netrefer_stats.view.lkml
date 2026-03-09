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

  # ── Campaign ────────────────────────────────────────────────────────────────
  dimension: campaign_id {
    type: string
    sql: ${TABLE}.campaign_id ;;
    label: "Campaign ID"
  }

  dimension: campaign_name {
    type: string
    sql: ${TABLE}.campaign_name ;;
    label: "Campaign Name"
  }

  # ── Groupings ────────────────────────────────────────────────────────────────
  dimension: brand {
    type: string
    sql: ${TABLE}.brand ;;
    label: "Brand"
  }

  dimension: country {
    type: string
    map_layer_name: countries
    sql: ${TABLE}.country ;;
    label: "Country"
  }

  dimension: media_type {
    type: string
    sql: ${TABLE}.media_type ;;
    label: "Media Type"
  }

  # ── Traffic Measures ─────────────────────────────────────────────────────────
  measure: total_impressions {
    type: sum
    sql: ${TABLE}.impressions ;;
    label: "Impressions"
    value_format_name: decimal_0
  }

  measure: total_clicks {
    type: sum
    sql: ${TABLE}.clicks ;;
    label: "Clicks"
    value_format_name: decimal_0
  }

  measure: click_through_rate {
    type: number
    sql: NULLIF(${total_clicks}, 0) / NULLIF(${total_impressions}, 0) ;;
    label: "CTR"
    value_format_name: percent_2
  }

  # ── Conversion Measures ──────────────────────────────────────────────────────
  measure: total_registrations {
    type: sum
    sql: ${TABLE}.registrations ;;
    label: "Registrations"
    value_format_name: decimal_0
  }

  measure: total_ftds {
    type: sum
    sql: ${TABLE}.first_depositors ;;
    label: "First Time Depositors (FTDs)"
    value_format_name: decimal_0
  }

  measure: total_depositors {
    type: sum
    sql: ${TABLE}.total_depositors ;;
    label: "Total Depositors"
    value_format_name: decimal_0
  }

  measure: click_to_registration_rate {
    type: number
    sql: NULLIF(${total_registrations}, 0) / NULLIF(${total_clicks}, 0) ;;
    label: "Click → Reg Rate"
    value_format_name: percent_2
  }

  measure: registration_to_ftd_rate {
    type: number
    sql: NULLIF(${total_ftds}, 0) / NULLIF(${total_registrations}, 0) ;;
    label: "Reg → FTD Rate"
    value_format_name: percent_2
  }

  # ── Financial Measures ───────────────────────────────────────────────────────
  measure: total_deposits {
    type: sum
    sql: ${TABLE}.deposits ;;
    label: "Total Deposits"
    value_format_name: usd
  }

  measure: total_net_revenue {
    type: sum
    sql: ${TABLE}.net_revenue ;;
    label: "Net Revenue"
    value_format_name: usd
  }

  measure: total_gross_revenue {
    type: sum
    sql: ${TABLE}.gross_revenue ;;
    label: "Gross Revenue"
    value_format_name: usd
  }

  measure: total_chargebacks {
    type: sum
    sql: ${TABLE}.chargebacks ;;
    label: "Chargebacks"
    value_format_name: usd
  }

  measure: total_commission {
    type: sum
    sql: ${TABLE}.commission ;;
    label: "Commission"
    value_format_name: usd
  }

  # ── Per-Affiliate Averages ───────────────────────────────────────────────────
  measure: avg_revenue_per_ftd {
    type: number
    sql: NULLIF(${total_net_revenue}, 0) / NULLIF(${total_ftds}, 0) ;;
    label: "Avg Revenue / FTD"
    value_format_name: usd
  }

  measure: avg_commission_per_ftd {
    type: number
    sql: NULLIF(${total_commission}, 0) / NULLIF(${total_ftds}, 0) ;;
    label: "Avg Commission / FTD"
    value_format_name: usd
  }

  measure: count_affiliates {
    type: count_distinct
    sql: ${affiliate_id} ;;
    label: "# Affiliates"
  }
}
