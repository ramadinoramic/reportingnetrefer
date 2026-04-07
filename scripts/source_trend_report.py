#!/usr/bin/env python3
"""
source_trend_report.py  –  Per-source drop detection across 3 / 7 / 15 day windows.

For each traffic source (campaign_name) compares:
  - current window  vs  same-length prior window
  - metrics: clicks, registrations, FTDs

Outputs a terminal table + optional HTML report with colour-coded delta badges.

Usage:
    # Quick terminal scan (uses .env for DB creds)
    python scripts/source_trend_report.py

    # Save HTML report
    python scripts/source_trend_report.py --output drop/trend_report.html

    # Open in browser automatically
    python scripts/source_trend_report.py --output drop/trend_report.html --open

    # Override the "today" anchor (useful for testing with older data)
    python scripts/source_trend_report.py --today 2026-03-17
"""

import argparse
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

def latest_date_in_db(cursor) -> Optional[date]:
    cursor.execute("SELECT MAX(report_date) FROM netrefer_stats")
    row = cursor.fetchone()
    return row[0] if row else None


def window_ranges(anchor: date, days: int) -> Tuple[Tuple[date, date], Tuple[date, date]]:
    """Return (current_from, current_to), (prior_from, prior_to) for a rolling window."""
    cur_to   = anchor
    cur_from = anchor - timedelta(days=days - 1)
    prev_to  = cur_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=days - 1)
    return (cur_from, cur_to), (prev_from, prev_to)


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------

def source_comparison(cursor, days: int, anchor: date) -> List[Dict]:
    """
    Single query covering both current + prior window.
    Returns one row per source with cur/prev metrics.
    """
    (cur_from, cur_to), (prev_from, prev_to) = window_ranges(anchor, days)
    full_from = prev_from  # earliest date needed

    rows = q(cursor, """
        SELECT
            campaign_name AS source,
            SUM(CASE WHEN report_date BETWEEN %s AND %s THEN clicks           ELSE 0 END) AS clicks_cur,
            SUM(CASE WHEN report_date BETWEEN %s AND %s THEN clicks           ELSE 0 END) AS clicks_prev,
            SUM(CASE WHEN report_date BETWEEN %s AND %s THEN registrations    ELSE 0 END) AS regs_cur,
            SUM(CASE WHEN report_date BETWEEN %s AND %s THEN registrations    ELSE 0 END) AS regs_prev,
            SUM(CASE WHEN report_date BETWEEN %s AND %s THEN first_depositors ELSE 0 END) AS ftds_cur,
            SUM(CASE WHEN report_date BETWEEN %s AND %s THEN first_depositors ELSE 0 END) AS ftds_prev
        FROM netrefer_stats
        WHERE report_date BETWEEN %s AND %s
          AND campaign_name != ''
        GROUP BY campaign_name
        ORDER BY ftds_cur DESC, clicks_cur DESC
    """, (
        cur_from,  cur_to,    # clicks_cur
        prev_from, prev_to,   # clicks_prev
        cur_from,  cur_to,    # regs_cur
        prev_from, prev_to,   # regs_prev
        cur_from,  cur_to,    # ftds_cur
        prev_from, prev_to,   # ftds_prev
        full_from, cur_to,    # WHERE
    ))
    return rows, (cur_from, cur_to), (prev_from, prev_to)


# ---------------------------------------------------------------------------
# Delta calculation
# ---------------------------------------------------------------------------

def pct_change(current, prior) -> Optional[float]:
    try:
        c, p = float(current or 0), float(prior or 0)
    except (TypeError, ValueError):
        return None
    if p == 0:
        return None if c == 0 else 100.0
    return round((c - p) / abs(p) * 100, 1)


def fmt_delta_terminal(current, prior) -> str:
    pct = pct_change(current, prior)
    if pct is None:
        return "   —  "
    arrow = "▲" if pct >= 0 else "▼"
    sign  = "+" if pct >= 0 else ""
    return f"{arrow}{sign}{pct:.0f}%"


ANSI_RED    = "\033[31m"
ANSI_GREEN  = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RESET  = "\033[0m"
ANSI_BOLD   = "\033[1m"


def color_delta(delta_str: str) -> str:
    if "▼" in delta_str:
        pct_val = float(delta_str.replace("▼", "").replace("%", "").strip())
        color = ANSI_RED if abs(pct_val) >= 20 else ANSI_YELLOW
        return f"{color}{delta_str}{ANSI_RESET}"
    if "▲" in delta_str:
        return f"{ANSI_GREEN}{delta_str}{ANSI_RESET}"
    return delta_str


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def print_window(rows, days, cur_range, prev_range):
    cur_from, cur_to   = cur_range
    prev_from, prev_to = prev_range

    print(f"\n{ANSI_BOLD}{'─'*78}{ANSI_RESET}")
    print(f"{ANSI_BOLD}  Last {days} days  │  "
          f"Current: {cur_from} → {cur_to}  │  "
          f"Prior: {prev_from} → {prev_to}{ANSI_RESET}")
    print(f"{'─'*78}")

    if not rows:
        print("  No data for this window.")
        return

    # Header
    print(f"  {'SOURCE':<28}  {'CLICKS':>8} {'Δ':>7}  {'REGS':>6} {'Δ':>7}  {'FTDs':>5} {'Δ':>7}")
    print(f"  {'─'*28}  {'─'*8} {'─'*7}  {'─'*6} {'─'*7}  {'─'*5} {'─'*7}")

    for r in rows:
        src = r["source"][:27]
        d_clicks = fmt_delta_terminal(r["clicks_cur"], r["clicks_prev"])
        d_regs   = fmt_delta_terminal(r["regs_cur"],   r["regs_prev"])
        d_ftds   = fmt_delta_terminal(r["ftds_cur"],   r["ftds_prev"])

        # Flag severely dropping rows
        ftd_pct = pct_change(r["ftds_cur"], r["ftds_prev"])
        row_color = ""
        row_reset = ""
        if ftd_pct is not None and ftd_pct <= -20:
            row_color = ANSI_RED
            row_reset = ANSI_RESET

        print(
            f"  {row_color}{src:<28}{row_reset}  "
            f"{int(r['clicks_cur']):>8,} {color_delta(d_clicks):>7}  "
            f"{int(r['regs_cur']):>6,} {color_delta(d_regs):>7}  "
            f"{int(r['ftds_cur']):>5,} {color_delta(d_ftds):>7}"
        )


# ---------------------------------------------------------------------------
# HTML output
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
.header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 60%, #0f3460 100%);
    color: #fff;
    padding: 32px 40px;
    border-radius: 12px;
    margin-bottom: 28px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
}
.header h1 { font-size: 24px; font-weight: 700; }
.header .sub { font-size: 12px; opacity: 0.65; margin-top: 4px; }
.header .meta { text-align: right; font-size: 12px; opacity: 0.7; }
.section {
    background: #fff;
    border-radius: 10px;
    padding: 20px 24px;
    margin-bottom: 24px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}
.section-title {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #6b7280;
    margin-bottom: 14px;
    padding-bottom: 8px;
    border-bottom: 1px solid #f0f0f0;
}
.period-info {
    font-size: 11px;
    color: #9ca3af;
    margin-bottom: 14px;
}
table { width: 100%; border-collapse: collapse; }
th {
    text-align: left;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: #6b7280;
    padding: 8px 10px;
    border-bottom: 2px solid #f0f0f0;
}
th.num, td.num { text-align: right; }
td {
    padding: 8px 10px;
    border-bottom: 1px solid #f8f8f8;
    font-size: 13px;
}
tr:hover td { background: #f9fafb; }
tr.drop-severe td { background: #fff5f5; }
tr.drop-severe td.source-col { font-weight: 600; color: #dc2626; }
.badge {
    display: inline-block;
    padding: 2px 7px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
    white-space: nowrap;
}
.badge.up       { background: #dcfce7; color: #166534; }
.badge.down-mild { background: #fef9c3; color: #854d0e; }
.badge.down-hard { background: #fee2e2; color: #991b1b; }
.badge.neutral  { background: #f3f4f6; color: #6b7280; }
.footer { text-align: center; font-size: 11px; color: #9ca3af; margin-top: 32px; }
"""


def html_badge(current, prior) -> str:
    pct = pct_change(current, prior)
    if pct is None:
        return '<span class="badge neutral">—</span>'
    arrow = "▲" if pct >= 0 else "▼"
    sign  = "+" if pct >= 0 else ""
    label = f"{arrow} {sign}{pct:.0f}%"
    if pct >= 0:
        css = "up"
    elif pct > -20:
        css = "down-mild"
    else:
        css = "down-hard"
    return f'<span class="badge {css}">{label}</span>'


def html_window_table(rows, days, cur_range, prev_range) -> str:
    cur_from, cur_to   = cur_range
    prev_from, prev_to = prev_range

    if not rows:
        return f"<p style='color:#9ca3af'>No data found for this window.</p>"

    row_html = ""
    for r in rows:
        ftd_pct = pct_change(r["ftds_cur"], r["ftds_prev"])
        severe  = ftd_pct is not None and ftd_pct <= -20
        tr_cls  = ' class="drop-severe"' if severe else ""

        row_html += f"""
        <tr{tr_cls}>
          <td class="source-col">{r['source']}</td>
          <td class="num">{int(r['clicks_cur']):,}</td>
          <td class="num">{html_badge(r['clicks_cur'], r['clicks_prev'])}</td>
          <td class="num">{int(r['regs_cur']):,}</td>
          <td class="num">{html_badge(r['regs_cur'], r['regs_prev'])}</td>
          <td class="num">{int(r['ftds_cur']):,}</td>
          <td class="num">{html_badge(r['ftds_cur'], r['ftds_prev'])}</td>
        </tr>"""

    return f"""
    <div class="period-info">
        Current: {cur_from} → {cur_to} &nbsp;|&nbsp; Prior: {prev_from} → {prev_to}
    </div>
    <table>
      <thead>
        <tr>
          <th>Source (Channel)</th>
          <th class="num">Clicks</th><th class="num">Δ</th>
          <th class="num">Regs</th><th class="num">Δ</th>
          <th class="num">FTDs</th><th class="num">Δ</th>
        </tr>
      </thead>
      <tbody>{row_html}</tbody>
    </table>"""


def build_html(windows_data, anchor: date) -> str:
    sections = ""
    for days, rows, cur_range, prev_range in windows_data:
        table = html_window_table(rows, days, cur_range, prev_range)
        sections += f"""
    <div class="section">
      <div class="section-title">Last {days} Days vs Prior {days} Days</div>
      {table}
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Source Trend Report — {anchor}</title>
  <style>{CSS}</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div>
      <h1>Source Performance Trends</h1>
      <div class="sub">
        Red rows = source dropped &gt;20% FTDs vs prior period &nbsp;|&nbsp;
        Anchor date: {anchor}
      </div>
    </div>
    <div class="meta">Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
  </div>
  {sections}
  <div class="footer">Netrefer Reporting · source_trend_report.py</div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Per-source drop detection report")
    parser.add_argument("--today",  help="Anchor date YYYY-MM-DD (default: latest date in DB)")
    parser.add_argument("--output", help="Path to save HTML report")
    parser.add_argument("--open",   action="store_true", help="Open HTML in browser after saving")
    args = parser.parse_args()

    print("Connecting to database …")
    try:
        conn = db_conn()
    except Exception as e:
        print(f"  DB connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    cursor = conn.cursor()

    if args.today:
        anchor = datetime.strptime(args.today, "%Y-%m-%d").date()
    else:
        anchor = latest_date_in_db(cursor)
        if anchor is None:
            print("No data found in netrefer_stats.", file=sys.stderr)
            sys.exit(1)

    print(f"  Anchor date: {anchor}\n")

    windows_data = []
    for days in [3, 7, 15]:
        rows, cur_range, prev_range = source_comparison(cursor, days, anchor)
        windows_data.append((days, rows, cur_range, prev_range))
        print_window(rows, days, cur_range, prev_range)

    cursor.close()
    conn.close()

    # HTML output
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        html = build_html(windows_data, anchor)
        out_path.write_text(html, encoding="utf-8")
        print(f"\n  HTML saved → {out_path.resolve()}")
        if args.open:
            webbrowser.open(out_path.resolve().as_uri())

    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
