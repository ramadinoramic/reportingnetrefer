#!/usr/bin/env python3
"""
Board / C-Level Executive Report Generator
===========================================
Queries MySQL and produces a self-contained HTML report that can be
printed to PDF from any browser (File → Print → Save as PDF).

Usage:
    # Report for the most recent period in the database
    python scripts/generate_board_report.py

    # Report for a specific month
    python scripts/generate_board_report.py --month 2026-03

    # Report for a custom date range
    python scripts/generate_board_report.py --from 2026-02-01 --to 2026-02-28

    # Save to a specific path
    python scripts/generate_board_report.py --output /tmp/board_report.html

Add to Makefile:
    make board-report
    make board-report MONTH=2026-03
"""

import argparse
import os
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def db_conn():
    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
    )


def q(cursor, sql: str, params=None) -> List[Dict]:
    cursor.execute(sql, params or ())
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

def latest_date(cursor) -> Optional[date]:
    cursor.execute("SELECT MAX(report_date) FROM netrefer_stats")
    row = cursor.fetchone()
    return row[0] if row else None


def month_range(month_str: str) -> Tuple[date, date]:
    """'2026-03' → (2026-03-01, 2026-03-31)"""
    d = datetime.strptime(month_str, "%Y-%m").date()
    # last day of month
    next_m = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    last = next_m - timedelta(days=1)
    return d, last


def prior_month_range(date_from: date, date_to: date) -> Tuple[date, date]:
    delta = (date_to - date_from).days + 1
    return date_from - timedelta(days=delta), date_to - timedelta(days=delta)


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------

def kpi_summary(cursor, d_from: date, d_to: date) -> Dict:
    rows = q(cursor, """
        SELECT
            SUM(clicks)           AS clicks,
            SUM(unique_clicks)    AS unique_clicks,
            SUM(registrations)    AS signups,
            SUM(first_depositors) AS ftds,
            SUM(depositing_customers) AS depositing_customers,
            SUM(active_customers) AS active_customers,
            SUM(deposits)         AS deposits,
            SUM(gross_revenue)    AS gross_revenue,
            SUM(bonuses)          AS bonuses,
            SUM(chargebacks)      AS chargebacks,
            SUM(net_revenue)      AS net_revenue,
            SUM(total_reward)     AS total_commission,
            SUM(rev_share_reward) AS rev_share,
            SUM(cpa_reward)       AS cpa_reward,
            COUNT(DISTINCT affiliate_id) AS active_affiliates
        FROM netrefer_stats
        WHERE report_date BETWEEN %s AND %s
    """, (d_from, d_to))
    return rows[0] if rows else {}


def top_affiliates(cursor, d_from: date, d_to: date, limit: int = 10) -> List[Dict]:
    return q(cursor, """
        SELECT
            affiliate_name,
            SUM(clicks)           AS clicks,
            SUM(registrations)    AS signups,
            SUM(first_depositors) AS ftds,
            SUM(deposits)         AS deposits,
            SUM(net_revenue)      AS net_revenue,
            SUM(total_reward)     AS commission,
            ROUND(100.0 * SUM(registrations) / NULLIF(SUM(clicks),0), 1) AS click_to_reg_pct,
            ROUND(100.0 * SUM(first_depositors) / NULLIF(SUM(registrations),0), 1) AS reg_to_ftd_pct
        FROM netrefer_stats
        WHERE report_date BETWEEN %s AND %s
        GROUP BY affiliate_id, affiliate_name
        ORDER BY net_revenue DESC
        LIMIT %s
    """, (d_from, d_to, limit))


def country_breakdown(cursor, d_from: date, d_to: date, limit: int = 8) -> List[Dict]:
    return q(cursor, """
        SELECT
            COALESCE(NULLIF(country,''), 'Unknown') AS country,
            SUM(first_depositors) AS ftds,
            SUM(net_revenue)      AS net_revenue,
            SUM(deposits)         AS deposits
        FROM netrefer_stats
        WHERE report_date BETWEEN %s AND %s
        GROUP BY country
        ORDER BY net_revenue DESC
        LIMIT %s
    """, (d_from, d_to, limit))


def campaign_breakdown(cursor, d_from: date, d_to: date) -> List[Dict]:
    return q(cursor, """
        SELECT
            COALESCE(NULLIF(campaign_name,''), 'Default') AS campaign_name,
            SUM(clicks)           AS clicks,
            SUM(registrations)    AS signups,
            SUM(first_depositors) AS ftds,
            SUM(net_revenue)      AS net_revenue,
            SUM(total_reward)     AS commission
        FROM netrefer_stats
        WHERE report_date BETWEEN %s AND %s
        GROUP BY campaign_name
        ORDER BY net_revenue DESC
        LIMIT 8
    """, (d_from, d_to))


def daily_trend(cursor, d_from: date, d_to: date) -> List[Dict]:
    return q(cursor, """
        SELECT
            report_date,
            SUM(net_revenue)      AS net_revenue,
            SUM(deposits)         AS deposits,
            SUM(first_depositors) AS ftds,
            SUM(registrations)    AS signups
        FROM netrefer_stats
        WHERE report_date BETWEEN %s AND %s
        GROUP BY report_date
        ORDER BY report_date
    """, (d_from, d_to))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_money(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        if abs(f) >= 1_000_000:
            return f"€{f/1_000_000:.2f}M"
        if abs(f) >= 1_000:
            return f"€{f/1_000:.1f}K"
        return f"€{f:,.2f}"
    except (TypeError, ValueError):
        return "—"


def fmt_int(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "—"


def fmt_pct(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return "—"


def delta_badge(current, prior, money: bool = False) -> str:
    """Return an HTML badge showing change vs prior period."""
    if current is None or prior is None:
        return ""
    try:
        c, p = float(current), float(prior)
    except (TypeError, ValueError):
        return ""
    if p == 0:
        return '<span class="badge neutral">—</span>'
    pct = (c - p) / abs(p) * 100
    direction = "up" if pct >= 0 else "down"
    arrow = "▲" if pct >= 0 else "▼"
    css = "positive" if pct >= 0 else "negative"
    return f'<span class="badge {css}">{arrow} {abs(pct):.1f}%</span>'


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    background: #f4f6f9;
    color: #1a1a2e;
    font-size: 13px;
    line-height: 1.5;
}
.page { max-width: 1100px; margin: 0 auto; padding: 32px 24px; }

/* ── Header ─────────────────────────────────── */
.header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 60%, #0f3460 100%);
    color: #fff;
    padding: 36px 40px;
    border-radius: 12px;
    margin-bottom: 28px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
}
.header h1 { font-size: 26px; font-weight: 700; letter-spacing: -0.5px; }
.header .subtitle { font-size: 13px; opacity: 0.7; margin-top: 4px; }
.header .period-box { text-align: right; }
.header .period-box .period { font-size: 18px; font-weight: 600; }
.header .period-box .generated { font-size: 11px; opacity: 0.6; margin-top: 4px; }

/* ── Section titles ──────────────────────────── */
.section-title {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #6b7280;
    margin: 32px 0 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid #e5e7eb;
}

/* ── KPI cards ───────────────────────────────── */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
    margin-bottom: 8px;
}
.kpi-grid-3 { grid-template-columns: repeat(3, 1fr); }
.kpi-card {
    background: #fff;
    border-radius: 10px;
    padding: 20px 22px;
    box-shadow: 0 1px 4px rgba(0,0,0,.06);
    border-top: 3px solid transparent;
}
.kpi-card.revenue  { border-color: #10b981; }
.kpi-card.traffic  { border-color: #3b82f6; }
.kpi-card.convert  { border-color: #f59e0b; }
.kpi-card.cost     { border-color: #ef4444; }
.kpi-card.neutral  { border-color: #8b5cf6; }

.kpi-label { font-size: 11px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: .5px; }
.kpi-value { font-size: 28px; font-weight: 700; color: #111827; margin: 6px 0 4px; line-height: 1; }
.kpi-meta  { font-size: 11px; color: #9ca3af; display: flex; align-items: center; gap: 6px; }

/* ── Badges ──────────────────────────────────── */
.badge { font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 9999px; }
.badge.positive { background: #d1fae5; color: #065f46; }
.badge.negative { background: #fee2e2; color: #991b1b; }
.badge.neutral  { background: #f3f4f6; color: #6b7280; }

/* ── Tables ──────────────────────────────────── */
.tbl-wrap { background: #fff; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.06); overflow: hidden; }
table { width: 100%; border-collapse: collapse; }
thead th {
    background: #f9fafb;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .5px;
    color: #6b7280;
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid #e5e7eb;
}
thead th.num { text-align: right; }
tbody td { padding: 10px 14px; border-bottom: 1px solid #f3f4f6; font-size: 12px; vertical-align: middle; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: #f9fafb; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.name { font-weight: 600; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rank-pill {
    display: inline-flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 50%;
    font-size: 11px; font-weight: 700;
    background: #f3f4f6; color: #6b7280;
}
.rank-pill.gold   { background: #fef3c7; color: #92400e; }
.rank-pill.silver { background: #f1f5f9; color: #475569; }
.rank-pill.bronze { background: #fef6ee; color: #9a3412; }

/* ── Two-col layout ──────────────────────────── */
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

/* ── Chart container ─────────────────────────── */
.chart-wrap {
    background: #fff;
    border-radius: 10px;
    box-shadow: 0 1px 4px rgba(0,0,0,.06);
    padding: 20px 22px;
}
.chart-wrap h3 { font-size: 12px; font-weight: 700; color: #374151; margin-bottom: 14px; }

/* ── Funnel bar ──────────────────────────────── */
.funnel { display: flex; flex-direction: column; gap: 8px; padding: 20px 22px; }
.funnel-step { display: flex; align-items: center; gap: 12px; }
.funnel-bar-wrap { flex: 1; }
.funnel-bar-bg { background: #f3f4f6; border-radius: 4px; height: 28px; position: relative; overflow: hidden; }
.funnel-bar-fill { height: 100%; border-radius: 4px; transition: width .4s; }
.funnel-label { font-size: 11px; font-weight: 600; color: #6b7280; width: 90px; text-align: right; white-space: nowrap; }
.funnel-val { font-size: 12px; font-weight: 700; color: #111827; width: 80px; }
.funnel-pct { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); font-size: 11px; font-weight: 600; color: #fff; }

/* ── Footer ──────────────────────────────────── */
.footer { margin-top: 40px; text-align: center; font-size: 10px; color: #9ca3af; padding-top: 16px; border-top: 1px solid #e5e7eb; }

/* ── Print ───────────────────────────────────── */
@media print {
    body { background: #fff; }
    .page { padding: 0; }
    .kpi-card, .tbl-wrap, .chart-wrap { box-shadow: none; border: 1px solid #e5e7eb; }
    .header { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
"""


def build_html(
    period_label: str,
    d_from: date,
    d_to: date,
    kpi: Dict,
    prior_kpi: Dict,
    affiliates: List[Dict],
    countries: List[Dict],
    campaigns: List[Dict],
    trend: List[Dict],
) -> str:

    generated = datetime.now().strftime("%d %b %Y, %H:%M")

    # ── KPI helpers ─────────────────────────────────────────────────────────
    def card(label: str, value: str, css: str, prior_val=None, curr_val=None, meta: str = "") -> str:
        badge = delta_badge(curr_val, prior_val) if prior_val is not None else ""
        meta_html = f'<span>{meta}</span>' if meta else ''
        return f"""
        <div class="kpi-card {css}">
            <div class="kpi-label">{label}</div>
            <div class="kpi-value">{value}</div>
            <div class="kpi-meta">{badge}{meta_html}</div>
        </div>"""

    nr       = kpi.get("net_revenue") or 0
    gr       = kpi.get("gross_revenue") or 0
    dep      = kpi.get("deposits") or 0
    comm     = kpi.get("total_commission") or 0
    clicks   = kpi.get("clicks") or 0
    signups  = kpi.get("signups") or 0
    ftds     = kpi.get("ftds") or 0
    dep_cust = kpi.get("depositing_customers") or 0
    aff_cnt  = kpi.get("active_affiliates") or 0

    pnr   = float(prior_kpi.get("net_revenue") or 0)
    pgr   = float(prior_kpi.get("gross_revenue") or 0)
    pdep  = float(prior_kpi.get("deposits") or 0)
    pcomm = float(prior_kpi.get("total_commission") or 0)
    pftds = float(prior_kpi.get("ftds") or 0)

    margin_pct = (float(nr) / float(gr) * 100) if gr and float(gr) != 0 else 0
    comm_pct   = (float(comm) / float(nr) * 100) if nr and float(nr) != 0 else 0
    ctr        = (float(signups) / float(clicks) * 100) if clicks and float(clicks) != 0 else 0
    reg_ftd    = (float(ftds) / float(signups) * 100) if signups and float(signups) != 0 else 0

    # ── Funnel data ─────────────────────────────────────────────────────────
    max_funnel = max(float(clicks or 1), 1)
    def funnel_bar(label: str, val, color: str, denominator=None) -> str:
        n = float(val or 0)
        w = round(n / max_funnel * 100, 1)
        pct_txt = f"{n/denominator*100:.1f}%" if denominator else ""
        return f"""
        <div class="funnel-step">
            <div class="funnel-label">{label}</div>
            <div class="funnel-bar-wrap">
                <div class="funnel-bar-bg">
                    <div class="funnel-bar-fill" style="width:{w}%;background:{color};">
                        <span class="funnel-pct">{pct_txt}</span>
                    </div>
                </div>
            </div>
            <div class="funnel-val">{fmt_int(val)}</div>
        </div>"""

    funnel_html = (
        funnel_bar("Clicks", clicks, "#3b82f6")
        + funnel_bar("Signups", signups, "#f59e0b", float(clicks or 1))
        + funnel_bar("FTDs", ftds, "#10b981", float(signups or 1))
        + funnel_bar("Depositors", dep_cust, "#8b5cf6", float(ftds or 1))
    )

    # ── Top affiliates table ─────────────────────────────────────────────────
    aff_rows = ""
    for i, a in enumerate(affiliates, 1):
        pill_cls = {1: "gold", 2: "silver", 3: "bronze"}.get(i, "")
        aff_rows += f"""
        <tr>
            <td><span class="rank-pill {pill_cls}">{i}</span></td>
            <td class="name">{a.get('affiliate_name') or '—'}</td>
            <td class="num">{fmt_int(a.get('clicks'))}</td>
            <td class="num">{fmt_int(a.get('signups'))}</td>
            <td class="num">{fmt_int(a.get('ftds'))}</td>
            <td class="num">{fmt_pct(a.get('click_to_reg_pct'))}</td>
            <td class="num">{fmt_pct(a.get('reg_to_ftd_pct'))}</td>
            <td class="num">{fmt_money(a.get('net_revenue'))}</td>
            <td class="num">{fmt_money(a.get('commission'))}</td>
        </tr>"""

    # ── Country table ────────────────────────────────────────────────────────
    total_country_nr = sum(float(c.get("net_revenue") or 0) for c in countries) or 1
    country_rows = ""
    for c in countries:
        share = float(c.get("net_revenue") or 0) / total_country_nr * 100
        bar_w = round(share, 1)
        country_rows += f"""
        <tr>
            <td class="name">{c.get('country') or '—'}</td>
            <td class="num">{fmt_int(c.get('ftds'))}</td>
            <td class="num">{fmt_money(c.get('deposits'))}</td>
            <td class="num">{fmt_money(c.get('net_revenue'))}</td>
            <td>
                <div style="background:#f3f4f6;border-radius:4px;height:10px;min-width:80px;">
                    <div style="width:{bar_w}%;background:#10b981;height:10px;border-radius:4px;"></div>
                </div>
            </td>
        </tr>"""

    # ── Campaign table ───────────────────────────────────────────────────────
    camp_rows = ""
    for c in campaigns:
        camp_rows += f"""
        <tr>
            <td class="name">{c.get('campaign_name') or '—'}</td>
            <td class="num">{fmt_int(c.get('clicks'))}</td>
            <td class="num">{fmt_int(c.get('signups'))}</td>
            <td class="num">{fmt_int(c.get('ftds'))}</td>
            <td class="num">{fmt_money(c.get('net_revenue'))}</td>
            <td class="num">{fmt_money(c.get('commission'))}</td>
        </tr>"""

    # ── Trend chart data ─────────────────────────────────────────────────────
    trend_labels = [str(r["report_date"]) for r in trend]
    trend_nr     = [float(r.get("net_revenue") or 0) for r in trend]
    trend_dep    = [float(r.get("deposits") or 0) for r in trend]
    trend_ftds   = [int(r.get("ftds") or 0) for r in trend]

    import json
    labels_js = json.dumps(trend_labels)
    nr_js     = json.dumps(trend_nr)
    dep_js    = json.dumps(trend_dep)
    ftds_js   = json.dumps(trend_ftds)

    chart_section = ""
    if trend:
        chart_section = f"""
        <div class="section-title">Performance Trend</div>
        <div class="chart-wrap">
            <h3>Daily Net Revenue &amp; Deposits</h3>
            <canvas id="trendChart" height="80"></canvas>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
        <script>
        (function() {{
            const ctx = document.getElementById('trendChart');
            new Chart(ctx, {{
                type: 'bar',
                data: {{
                    labels: {labels_js},
                    datasets: [
                        {{
                            label: 'Net Revenue',
                            data: {nr_js},
                            backgroundColor: 'rgba(16,185,129,0.75)',
                            borderRadius: 4,
                            yAxisID: 'y'
                        }},
                        {{
                            label: 'Deposits',
                            data: {dep_js},
                            backgroundColor: 'rgba(59,130,246,0.45)',
                            borderRadius: 4,
                            yAxisID: 'y'
                        }}
                    ]
                }},
                options: {{
                    responsive: true,
                    interaction: {{ mode: 'index', intersect: false }},
                    plugins: {{
                        legend: {{ position: 'top', labels: {{ font: {{ size: 11 }} }} }}
                    }},
                    scales: {{
                        y: {{ ticks: {{ callback: v => '€' + (v>=1000 ? (v/1000).toFixed(1)+'K' : v) }}, grid: {{ color: '#f3f4f6' }} }},
                        x: {{ ticks: {{ font: {{ size: 10 }} }}, grid: {{ display: false }} }}
                    }}
                }}
            }});
        }})();
        </script>
        """

    # ── Commission breakdown ─────────────────────────────────────────────────
    rev_share = float(kpi.get("rev_share") or 0)
    cpa       = float(kpi.get("cpa_reward") or 0)
    other_c   = float(comm) - rev_share - cpa
    comm_rows = ""
    for label, val in [("Revenue Share", rev_share), ("CPA", cpa), ("Other / Sub-aff.", other_c)]:
        pct = val / float(comm) * 100 if float(comm) else 0
        comm_rows += f"""
        <tr>
            <td>{label}</td>
            <td class="num">{fmt_money(val)}</td>
            <td class="num">{fmt_pct(pct)}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Affiliate Board Report — {period_label}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">

<!-- ═══════════════════ HEADER ═══════════════════ -->
<div class="header">
    <div>
        <div class="subtitle">AFFILIATE PERFORMANCE REPORT</div>
        <h1>Executive &amp; Board Summary</h1>
    </div>
    <div class="period-box">
        <div class="period">{period_label}</div>
        <div class="generated">Generated {generated} · Confidential</div>
    </div>
</div>

<!-- ═══════════════════ FINANCIAL KPIs ═══════════════════ -->
<div class="section-title">Financial Performance</div>
<div class="kpi-grid">
    {card("Net Gaming Revenue", fmt_money(nr),  "revenue", pnr, nr,  f"Gross margin {margin_pct:.1f}%")}
    {card("Gross Revenue",      fmt_money(gr),  "revenue", pgr, gr)}
    {card("Total Deposits",     fmt_money(dep), "revenue", pdep, dep)}
    {card("Total Commission",   fmt_money(comm),"cost",    pcomm, comm, f"{comm_pct:.1f}% of NGR")}
</div>

<!-- ═══════════════════ OPERATIONAL KPIs ═══════════════════ -->
<div class="section-title">Affiliate Metrics</div>
<div class="kpi-grid">
    {card("Total Clicks",         fmt_int(clicks),  "traffic")}
    {card("Registrations",        fmt_int(signups), "traffic")}
    {card("First-Time Depositors",fmt_int(ftds),    "convert", pftds, ftds)}
    {card("Active Affiliates",    fmt_int(aff_cnt), "neutral")}
</div>

<!-- ═══════════════════ CONVERSION FUNNEL ═══════════════════ -->
<div class="section-title">Conversion Funnel</div>
<div class="tbl-wrap">
    <div class="funnel">
        {funnel_html}
    </div>
</div>

<!-- ═══════════════════ TREND CHART ═══════════════════ -->
{chart_section}

<!-- ═══════════════════ TOP AFFILIATES ═══════════════════ -->
<div class="section-title">Top Affiliates by Net Revenue</div>
<div class="tbl-wrap">
    <table>
        <thead>
            <tr>
                <th>#</th>
                <th>Affiliate</th>
                <th class="num">Clicks</th>
                <th class="num">Signups</th>
                <th class="num">FTDs</th>
                <th class="num">Click→Reg</th>
                <th class="num">Reg→FTD</th>
                <th class="num">Net Revenue</th>
                <th class="num">Commission</th>
            </tr>
        </thead>
        <tbody>{aff_rows}</tbody>
    </table>
</div>

<!-- ═══════════════════ GEO + CAMPAIGNS ═══════════════════ -->
<div class="two-col">
    <div>
        <div class="section-title">Geographic Breakdown</div>
        <div class="tbl-wrap">
            <table>
                <thead>
                    <tr>
                        <th>Country</th>
                        <th class="num">FTDs</th>
                        <th class="num">Deposits</th>
                        <th class="num">Net Rev.</th>
                        <th>Share</th>
                    </tr>
                </thead>
                <tbody>{country_rows}</tbody>
            </table>
        </div>
    </div>
    <div>
        <div class="section-title">Campaign Breakdown</div>
        <div class="tbl-wrap">
            <table>
                <thead>
                    <tr>
                        <th>Campaign</th>
                        <th class="num">Clicks</th>
                        <th class="num">Signups</th>
                        <th class="num">FTDs</th>
                        <th class="num">Net Rev.</th>
                        <th class="num">Comm.</th>
                    </tr>
                </thead>
                <tbody>{camp_rows}</tbody>
            </table>
        </div>
    </div>
</div>

<!-- ═══════════════════ COMMISSION DETAIL ═══════════════════ -->
<div class="section-title">Commission Breakdown</div>
<div class="tbl-wrap">
    <table>
        <thead>
            <tr><th>Type</th><th class="num">Amount</th><th class="num">% of Total</th></tr>
        </thead>
        <tbody>{comm_rows}
        <tr style="font-weight:700;background:#f9fafb;">
            <td>Total</td>
            <td class="num">{fmt_money(comm)}</td>
            <td class="num">100%</td>
        </tr>
        </tbody>
    </table>
</div>

<div class="footer">
    Netrefer Affiliate Reporting · Period {d_from} → {d_to} · Confidential &amp; Privileged
</div>

</div><!-- /page -->
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate C-Level / Board affiliate report")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--month", metavar="YYYY-MM",
                       help="Report for a full calendar month (default: latest month in DB)")
    group.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD",
                       help="Start date of custom range")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD",
                        help="End date of custom range (required with --from)")
    parser.add_argument("--output", "-o", metavar="FILE",
                        help="Output HTML file path (default: reports/board_report_<period>.html)")
    parser.add_argument("--email", metavar="ADDRESS[,ADDRESS]",
                        help="Comma-separated recipient addresses — sends report via SMTP "
                             "(configure SMTP_HOST/USER/PASSWORD/PORT in .env)")
    args = parser.parse_args()

    # ── Resolve period ───────────────────────────────────────────────────────
    conn = db_conn()
    cursor = conn.cursor()

    if args.date_from:
        d_from = date.fromisoformat(args.date_from)
        d_to   = date.fromisoformat(args.date_to) if args.date_to else d_from
        period_label = f"{d_from.strftime('%d %b %Y')} – {d_to.strftime('%d %b %Y')}"
    else:
        if args.month:
            d_from, d_to = month_range(args.month)
        else:
            latest = latest_date(cursor)
            if not latest:
                print("ERROR: No data found in the database.", file=sys.stderr)
                sys.exit(1)
            # default to the month of the latest record
            d_from, d_to = month_range(latest.strftime("%Y-%m"))
        period_label = d_from.strftime("%B %Y")

    # ── Prior period ─────────────────────────────────────────────────────────
    prior_from, prior_to = prior_month_range(d_from, d_to)

    # ── Query ────────────────────────────────────────────────────────────────
    kpi       = kpi_summary(cursor, d_from, d_to)
    prior_kpi = kpi_summary(cursor, prior_from, prior_to)
    affiliates = top_affiliates(cursor, d_from, d_to)
    countries  = country_breakdown(cursor, d_from, d_to)
    campaigns  = campaign_breakdown(cursor, d_from, d_to)
    trend      = daily_trend(cursor, d_from, d_to)

    cursor.close()
    conn.close()

    # ── Render ───────────────────────────────────────────────────────────────
    html = build_html(
        period_label=period_label,
        d_from=d_from,
        d_to=d_to,
        kpi=kpi,
        prior_kpi=prior_kpi,
        affiliates=affiliates,
        countries=countries,
        campaigns=campaigns,
        trend=trend,
    )

    # ── Output path ──────────────────────────────────────────────────────────
    if args.output:
        out_path = Path(args.output)
    else:
        reports_dir = Path(__file__).parent.parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        slug = d_from.strftime("%Y-%m")
        out_path = reports_dir / f"board_report_{slug}.html"

    out_path.write_text(html, encoding="utf-8")
    print(f"Report saved → {out_path}")
    print(f"Open in your browser and use File → Print → Save as PDF to export.")

    # ── Optional email delivery ───────────────────────────────────────────
    if args.email:
        send_report_email(html, period_label, args.email.split(","))


def send_report_email(html: str, period_label: str, recipients: List[str]):
    """
    Send the board report HTML via email using SMTP settings from .env.

    Required env vars:
        SMTP_HOST      — e.g. smtp.gmail.com
        SMTP_PORT      — e.g. 587
        SMTP_USER      — sender address
        SMTP_PASSWORD  — sender password / app password
    Optional:
        SMTP_FROM      — display name + address, defaults to SMTP_USER
    """
    host     = os.environ.get("SMTP_HOST")
    port     = int(os.environ.get("SMTP_PORT", 587))
    user     = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    sender   = os.environ.get("SMTP_FROM", user)

    if not all([host, user, password]):
        print(
            "ERROR: email requested but SMTP_HOST / SMTP_USER / SMTP_PASSWORD "
            "are not set in .env — skipping email delivery.",
            file=sys.stderr,
        )
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Affiliate Board Report — {period_label}"
    msg["From"]    = sender
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(sender, recipients, msg.as_string())
        print(f"Email sent to: {', '.join(recipients)}")
    except Exception as exc:
        print(f"ERROR: failed to send email — {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
