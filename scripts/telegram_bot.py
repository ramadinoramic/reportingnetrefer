#!/usr/bin/env python3
"""
telegram_bot.py  –  Natural language reporting bot for Netrefer affiliate data.

Send a message like:
  "who is top performer yesterday?"
  "how did we do last week?"
  "which channel is dropping?"
  "show me FTDs trend for last 7 days"
  "compare this week vs last week"

The bot uses Claude API to parse your intent, then runs safe pre-defined
queries against the MySQL database and replies with formatted results.

Setup (add to .env):
  TELEGRAM_BOT_TOKEN=...         from @BotFather
  TELEGRAM_ALLOWED_USERS=123,456  your Telegram user ID(s), from @userinfobot
  ANTHROPIC_API_KEY=...           from console.anthropic.com
"""

import asyncio
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import anthropic
import mysql.connector
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

TELEGRAM_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]
_raw_users       = os.getenv("TELEGRAM_ALLOWED_USERS", "")
ALLOWED_USERS    = {int(u.strip()) for u in _raw_users.split(",") if u.strip()}

ai = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────

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


def latest_date(cursor) -> Optional[date]:
    cursor.execute("SELECT MAX(report_date) FROM netrefer_stats")
    row = cursor.fetchone()
    return row[0] if row else None


# ──────────────────────────────────────────────
# Period resolution
# ──────────────────────────────────────────────

def resolve_period(period: str, anchor: date) -> Tuple[date, date]:
    """Map a period string to (date_from, date_to) using the DB anchor as 'today'."""
    p = period.lower().replace(" ", "_")
    if p in ("today", "latest"):
        return anchor, anchor
    if p == "yesterday":
        d = anchor - timedelta(days=1)
        return d, d
    if p == "last_7_days":
        return anchor - timedelta(days=6), anchor
    if p == "last_30_days":
        return anchor - timedelta(days=29), anchor
    if p == "this_week":
        monday = anchor - timedelta(days=anchor.weekday())
        return monday, anchor
    if p == "last_week":
        monday = anchor - timedelta(days=anchor.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if p == "this_month":
        return anchor.replace(day=1), anchor
    if p == "last_month":
        first_this = anchor.replace(day=1)
        last_prev  = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    # fallback
    return anchor - timedelta(days=6), anchor


def prior_period(d_from: date, d_to: date) -> Tuple[date, date]:
    delta = (d_to - d_from).days + 1
    return d_from - timedelta(days=delta), d_to - timedelta(days=delta)


# ──────────────────────────────────────────────
# Channel grouping (affiliate_id → group name)
# ──────────────────────────────────────────────

CHANNEL_CASE = """CASE
    WHEN affiliate_id IN ('660062','660060','660052','659813','659787','659836','659921','659933','660005','659819','659989','659838','659943','660039','659848','660007','659772','659844','660010','659786','659818','659730','659233','659861','659804','659803','660003','659725','660027','659952','659864','660029','659929','659783','659713','660094','659815','659807') THEN 'CPA/CPL'
    WHEN affiliate_id IN ('657238','657239') THEN 'Direct'
    WHEN affiliate_id IN ('660116','660117','659660','659699','659637','659876','659891','660018','659593','659594','659595','659909','659923','659561','660172','660174') THEN 'MB in-house'
    WHEN affiliate_id IN ('659873','659481','659839','660032','656618','660138','660137','659831','656062','660084','659462','659757') THEN 'MB outsourced'
    WHEN affiliate_id IN ('660108','660109','660110','657236') THEN 'SEO'
    WHEN affiliate_id IN ('660064','660074','660077','660079','660085','660087','660091','660114','660050','660043','659906','658393','657523','659926','659888','659898','660015','660014','660118','659567','660148','660162','660141','660124','660147','660179','660178','660151','660183','660184','660185','660186','660187','660133') THEN 'Influencers'
    WHEN affiliate_id IN ('657237','659271','659752') THEN 'Social'
    WHEN affiliate_id IN ('0','659086') THEN 'unattributed'
    ELSE 'Affiliates'
END"""


# ──────────────────────────────────────────────
# Query functions
# ──────────────────────────────────────────────

def kpi_summary(cursor, d_from: date, d_to: date) -> Dict:
    rows = q(cursor, """
        SELECT
            SUM(clicks)           AS clicks,
            SUM(registrations)    AS regs,
            SUM(first_depositors) AS ftds,
            ROUND(SUM(deposits),     2) AS deposits,
            ROUND(SUM(gross_revenue),2) AS ggr,
            ROUND(SUM(net_revenue),  2) AS ngr,
            ROUND(SUM(total_reward), 2) AS commission,
            COUNT(DISTINCT affiliate_id) AS affiliates
        FROM netrefer_stats
        WHERE report_date BETWEEN %s AND %s
    """, (d_from, d_to))
    return rows[0] if rows else {}


def top_affiliates(cursor, d_from: date, d_to: date, metric: str = "ftds", limit: int = 5) -> List[Dict]:
    metric_col = {
        "ftds": "SUM(first_depositors)",
        "clicks": "SUM(clicks)",
        "regs": "SUM(registrations)",
        "ngr": "ROUND(SUM(net_revenue),2)",
        "ggr": "ROUND(SUM(gross_revenue),2)",
        "commission": "ROUND(SUM(total_reward),2)",
    }.get(metric, "SUM(first_depositors)")

    return q(cursor, f"""
        SELECT
            affiliate_name,
            SUM(clicks)                AS clicks,
            SUM(registrations)         AS regs,
            SUM(first_depositors)      AS ftds,
            ROUND(SUM(net_revenue), 2) AS ngr,
            ROUND(SUM(total_reward),2) AS commission,
            ROUND(
                SUM(first_depositors)*100.0/NULLIF(SUM(registrations),0)
            ,1) AS reg_to_ftd_pct
        FROM netrefer_stats
        WHERE report_date BETWEEN %s AND %s
        GROUP BY affiliate_name
        ORDER BY {metric_col} DESC
        LIMIT %s
    """, (d_from, d_to, limit))


def channel_breakdown(cursor, d_from: date, d_to: date) -> List[Dict]:
    return q(cursor, f"""
        SELECT
            {CHANNEL_CASE}             AS channel,
            SUM(clicks)                AS clicks,
            SUM(registrations)         AS regs,
            SUM(first_depositors)      AS ftds,
            ROUND(SUM(net_revenue), 2) AS ngr,
            ROUND(
                SUM(first_depositors)*100.0/NULLIF(SUM(registrations),0)
            ,1) AS reg_to_ftd_pct
        FROM netrefer_stats
        WHERE report_date BETWEEN %s AND %s
        GROUP BY channel
        ORDER BY ftds DESC
    """, (d_from, d_to))


def daily_trend(cursor, d_from: date, d_to: date) -> List[Dict]:
    return q(cursor, """
        SELECT
            report_date,
            SUM(clicks)                AS clicks,
            SUM(registrations)         AS regs,
            SUM(first_depositors)      AS ftds,
            ROUND(SUM(net_revenue), 2) AS ngr
        FROM netrefer_stats
        WHERE report_date BETWEEN %s AND %s
        GROUP BY report_date
        ORDER BY report_date
    """, (d_from, d_to))


def source_drops(cursor, anchor: date, days: int = 7) -> List[Dict]:
    n   = days - 1
    p1  = days
    p2  = 2 * days - 1
    anc = anchor.strftime("'%Y-%m-%d'")
    return q(cursor, f"""
        SELECT
            affiliate_name                                                          AS source,
            SUM(CASE WHEN report_date BETWEEN DATE_SUB({anc}, INTERVAL {n} DAY) AND {anc}
                     THEN first_depositors ELSE 0 END)                              AS ftds_now,
            SUM(CASE WHEN report_date BETWEEN DATE_SUB({anc}, INTERVAL {p2} DAY)
                                          AND DATE_SUB({anc}, INTERVAL {p1} DAY)
                     THEN first_depositors ELSE 0 END)                              AS ftds_prev,
            ROUND(
                CASE WHEN SUM(CASE WHEN report_date BETWEEN DATE_SUB({anc}, INTERVAL {p2} DAY)
                                   AND DATE_SUB({anc}, INTERVAL {p1} DAY)
                                   THEN first_depositors ELSE 0 END) > 0
                     THEN (
                        SUM(CASE WHEN report_date BETWEEN DATE_SUB({anc}, INTERVAL {n} DAY)
                                 AND {anc} THEN first_depositors ELSE 0 END)
                      - SUM(CASE WHEN report_date BETWEEN DATE_SUB({anc}, INTERVAL {p2} DAY)
                                 AND DATE_SUB({anc}, INTERVAL {p1} DAY)
                                 THEN first_depositors ELSE 0 END)
                     ) * 100.0
                      / SUM(CASE WHEN report_date BETWEEN DATE_SUB({anc}, INTERVAL {p2} DAY)
                                 AND DATE_SUB({anc}, INTERVAL {p1} DAY)
                                 THEN first_depositors ELSE 0 END)
                     ELSE NULL END
            , 1)                                                                    AS delta_pct
        FROM netrefer_stats
        WHERE report_date >= DATE_SUB({anc}, INTERVAL {p2} DAY)
        GROUP BY affiliate_name
        HAVING ftds_prev > 0 AND delta_pct IS NOT NULL
        ORDER BY delta_pct ASC
        LIMIT 10
    """)


def specific_affiliate(cursor, d_from: date, d_to: date, name: str) -> List[Dict]:
    return q(cursor, """
        SELECT
            report_date,
            SUM(clicks)                AS clicks,
            SUM(registrations)         AS regs,
            SUM(first_depositors)      AS ftds,
            ROUND(SUM(net_revenue), 2) AS ngr,
            ROUND(SUM(total_reward),2) AS commission
        FROM netrefer_stats
        WHERE report_date BETWEEN %s AND %s
          AND LOWER(affiliate_name) LIKE LOWER(%s)
        GROUP BY report_date
        ORDER BY report_date
    """, (d_from, d_to, f"%{name}%"))


# ──────────────────────────────────────────────
# Intent parsing via Claude
# ──────────────────────────────────────────────

SYSTEM_PROMPT = """You are a query parser for an affiliate marketing reporting database.

The database tracks daily affiliate performance. Key dimensions:
- affiliate_name: name of the affiliate partner
- campaign_name: raw traffic source/campaign
- channel groups (computed from affiliate_id): CPA/CPL, Direct, MB in-house, MB outsourced, SEO, Influencers, Social, Affiliates

Key metrics:
- clicks: raw traffic clicks
- regs / registrations: new sign-ups
- ftds / first_depositors: first-time depositors (most important KPI)
- ngr / net_revenue: net gaming revenue (EUR)
- ggr / gross_revenue: gross gaming revenue (EUR)
- commission / total_reward: affiliate commission paid

Parse the user's message and return ONLY valid JSON (no explanation, no markdown):
{
  "intent": <one of: kpi_summary | top_performers | channel_breakdown | trend | specific_affiliate | drops | comparison | help>,
  "period": <one of: today | yesterday | last_7_days | last_30_days | this_week | last_week | this_month | last_month>,
  "metric": <one of: ftds | clicks | regs | ngr | ggr | commission>,
  "limit": <integer 1-20, default 5>,
  "filter_affiliate": <affiliate name string or null>,
  "filter_channel": <channel group name or null>
}

Rules:
- Default period is "yesterday" if not specified
- Default metric is "ftds" if not specified
- "top performer" means top_performers intent by ftds
- "dropping" or "declining" or "getting worse" means drops intent
- "trend" or "daily" or "day by day" means trend intent
- "compare" or "vs" or "versus" means comparison intent
- If unclear, use kpi_summary intent
- For greetings or help questions, use help intent"""


def parse_intent(message: str) -> Dict:
    try:
        resp = ai.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": message}],
        )
        text = resp.content[0].text.strip()
        # strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        log.warning(f"Intent parse failed: {e}")
        return {"intent": "kpi_summary", "period": "yesterday", "metric": "ftds",
                "limit": 5, "filter_affiliate": None, "filter_channel": None}


# ──────────────────────────────────────────────
# Formatters
# ──────────────────────────────────────────────

def fmt_num(v, money: bool = False) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        if money:
            return f"€{f:,.0f}"
        return f"{int(f):,}"
    except (TypeError, ValueError):
        return str(v)


def period_label(d_from: date, d_to: date) -> str:
    if d_from == d_to:
        return d_from.strftime("%b %d, %Y")
    return f"{d_from.strftime('%b %d')} – {d_to.strftime('%b %d, %Y')}"


def format_kpi_summary(data: Dict, d_from: date, d_to: date) -> str:
    if not data or not any(data.values()):
        return "No data found for that period."
    return (
        f"📊 *KPI Summary — {period_label(d_from, d_to)}*\n\n"
        f"👆 Clicks:     `{fmt_num(data.get('clicks'))}`\n"
        f"📝 Regs:       `{fmt_num(data.get('regs'))}`\n"
        f"💰 FTDs:       `{fmt_num(data.get('ftds'))}`\n"
        f"💵 GGR:        `{fmt_num(data.get('ggr'), money=True)}`\n"
        f"📈 NGR:        `{fmt_num(data.get('ngr'), money=True)}`\n"
        f"🎯 Commission: `{fmt_num(data.get('commission'), money=True)}`\n"
        f"👥 Affiliates: `{fmt_num(data.get('affiliates'))}`"
    )


def format_top_affiliates(rows: List[Dict], metric: str, d_from: date, d_to: date) -> str:
    if not rows:
        return "No data found for that period."
    metric_label = {"ftds": "FTDs", "clicks": "Clicks", "regs": "Regs",
                    "ngr": "NGR", "ggr": "GGR", "commission": "Commission"}.get(metric, "FTDs")
    lines = [f"🏆 *Top Affiliates by {metric_label} — {period_label(d_from, d_to)}*\n"]
    medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 20
    for i, r in enumerate(rows):
        name = r.get("affiliate_name", "Unknown")[:25]
        ftds = fmt_num(r.get("ftds"))
        ngr  = fmt_num(r.get("ngr"), money=True)
        cr   = r.get("reg_to_ftd_pct")
        cr_s = f" ({cr}% FTD rate)" if cr else ""
        lines.append(f"{medals[i]} *{name}*  —  {ftds} FTDs  |  {ngr} NGR{cr_s}")
    return "\n".join(lines)


def format_channel_breakdown(rows: List[Dict], d_from: date, d_to: date) -> str:
    if not rows:
        return "No data found for that period."
    lines = [f"📡 *Channel Breakdown — {period_label(d_from, d_to)}*\n"]
    for r in rows:
        ch   = r.get("channel", "?")
        ftds = fmt_num(r.get("ftds"))
        ngr  = fmt_num(r.get("ngr"), money=True)
        cr   = r.get("reg_to_ftd_pct")
        cr_s = f"  ({cr}% FTD rate)" if cr else ""
        lines.append(f"▪️ *{ch}*  —  {ftds} FTDs  |  {ngr} NGR{cr_s}")
    return "\n".join(lines)


def format_trend(rows: List[Dict], d_from: date, d_to: date) -> str:
    if not rows:
        return "No data found for that period."
    lines = [f"📈 *Daily Trend — {period_label(d_from, d_to)}*\n"]
    for r in rows:
        dt   = r["report_date"]
        day  = dt.strftime("%b %d") if hasattr(dt, "strftime") else str(dt)
        ftds = fmt_num(r.get("ftds"))
        ngr  = fmt_num(r.get("ngr"), money=True)
        lines.append(f"`{day}`  —  {ftds} FTDs  |  {ngr} NGR")
    return "\n".join(lines)


def format_drops(rows: List[Dict], days: int, anchor: date) -> str:
    if not rows:
        return f"No significant drops detected in the last {days} days. 🎉"
    lines = [f"⚠️ *Dropping Sources (last {days}d vs prior {days}d)*\n"]
    for r in rows:
        src   = r.get("source", "?")[:25]
        now   = fmt_num(r.get("ftds_now"))
        prev  = fmt_num(r.get("ftds_prev"))
        delta = r.get("delta_pct")
        arrow = "🔴" if delta is not None and delta <= -20 else "🟡"
        d_str = f"{delta:+.0f}%" if delta is not None else "—"
        lines.append(f"{arrow} *{src}*  —  {now} FTDs (was {prev})  `{d_str}`")
    return "\n".join(lines)


def format_specific_affiliate(rows: List[Dict], name: str, d_from: date, d_to: date) -> str:
    if not rows:
        return f"No data found for affiliate matching *{name}* in that period."
    lines = [f"👤 *{name} — {period_label(d_from, d_to)}*\n"]
    for r in rows:
        dt   = r["report_date"]
        day  = dt.strftime("%b %d") if hasattr(dt, "strftime") else str(dt)
        ftds = fmt_num(r.get("ftds"))
        ngr  = fmt_num(r.get("ngr"), money=True)
        lines.append(f"`{day}`  —  {ftds} FTDs  |  {ngr} NGR")
    return "\n".join(lines)


def format_comparison(cur: Dict, prev: Dict, d_from: date, d_to: date) -> str:
    pf, pt = prior_period(d_from, d_to)

    def delta(a, b):
        try:
            a, b = float(a or 0), float(b or 0)
            if b == 0:
                return ""
            pct = (a - b) / abs(b) * 100
            arrow = "▲" if pct >= 0 else "▼"
            return f"  {arrow}{abs(pct):.0f}%"
        except Exception:
            return ""

    return (
        f"🔄 *Period Comparison*\n"
        f"Current: {period_label(d_from, d_to)}\n"
        f"Prior:   {period_label(pf, pt)}\n\n"
        f"👆 Clicks:  `{fmt_num(cur.get('clicks'))}` vs `{fmt_num(prev.get('clicks'))}`{delta(cur.get('clicks'), prev.get('clicks'))}\n"
        f"📝 Regs:    `{fmt_num(cur.get('regs'))}` vs `{fmt_num(prev.get('regs'))}`{delta(cur.get('regs'), prev.get('regs'))}\n"
        f"💰 FTDs:    `{fmt_num(cur.get('ftds'))}` vs `{fmt_num(prev.get('ftds'))}`{delta(cur.get('ftds'), prev.get('ftds'))}\n"
        f"📈 NGR:     `{fmt_num(cur.get('ngr'), money=True)}` vs `{fmt_num(prev.get('ngr'), money=True)}`{delta(cur.get('ngr'), prev.get('ngr'))}\n"
    )


HELP_TEXT = (
    "👋 *Netrefer Reporting Bot*\n\n"
    "Ask me anything about affiliate performance. Examples:\n\n"
    "📊 *Summaries*\n"
    "• _how did we do yesterday?_\n"
    "• _show me last week's numbers_\n"
    "• _this month's KPIs_\n\n"
    "🏆 *Rankings*\n"
    "• _who is the top performer?_\n"
    "• _best affiliates last 7 days by NGR_\n"
    "• _top 10 by clicks this month_\n\n"
    "📡 *Channels*\n"
    "• _which channel performed best yesterday?_\n"
    "• _show channel breakdown last week_\n\n"
    "📈 *Trends*\n"
    "• _show me the daily trend last 7 days_\n"
    "• _FTDs day by day this week_\n\n"
    "⚠️ *Drop Detection*\n"
    "• _who is dropping?_\n"
    "• _which sources are declining?_\n\n"
    "🔄 *Comparisons*\n"
    "• _compare this week vs last week_\n"
    "• _how does this month compare to last month?_\n\n"
    "👤 *Specific Affiliate*\n"
    "• _how is Direct doing this week?_\n"
    "• _show me SEO stats yesterday_"
)


# ──────────────────────────────────────────────
# Message handler
# ──────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()

    # Security gate
    if ALLOWED_USERS and user.id not in ALLOWED_USERS:
        log.warning(f"Rejected user {user.id} (@{user.username})")
        return

    log.info(f"User {user.id}: {text!r}")

    # Show typing indicator
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    # Parse intent
    intent_data = parse_intent(text)
    intent   = intent_data.get("intent", "kpi_summary")
    period   = intent_data.get("period", "yesterday")
    metric   = intent_data.get("metric", "ftds")
    limit    = min(int(intent_data.get("limit") or 5), 20)
    aff_filt = intent_data.get("filter_affiliate")
    ch_filt  = intent_data.get("filter_channel")

    log.info(f"Intent: {intent_data}")

    if intent == "help":
        await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)
        return

    # Connect to DB
    try:
        conn   = db_conn()
        cursor = conn.cursor()
        anchor = latest_date(cursor)
        if anchor is None:
            await update.message.reply_text("⚠️ No data in the database yet.")
            return

        d_from, d_to = resolve_period(period, anchor)

        # Execute intent
        if intent == "top_performers":
            rows  = top_affiliates(cursor, d_from, d_to, metric=metric, limit=limit)
            reply = format_top_affiliates(rows, metric, d_from, d_to)

        elif intent == "channel_breakdown":
            rows  = channel_breakdown(cursor, d_from, d_to)
            reply = format_channel_breakdown(rows, d_from, d_to)

        elif intent == "trend":
            rows  = daily_trend(cursor, d_from, d_to)
            reply = format_trend(rows, d_from, d_to)

        elif intent == "drops":
            days  = {"last_7_days": 7, "last_30_days": 15, "last_3_days": 3}.get(period, 7)
            rows  = source_drops(cursor, anchor, days=days)
            reply = format_drops(rows, days, anchor)

        elif intent == "comparison":
            pf, pt = prior_period(d_from, d_to)
            cur    = kpi_summary(cursor, d_from, d_to)
            prev   = kpi_summary(cursor, pf, pt)
            reply  = format_comparison(cur, prev, d_from, d_to)

        elif intent == "specific_affiliate":
            name  = aff_filt or ch_filt or "unknown"
            rows  = specific_affiliate(cursor, d_from, d_to, name)
            reply = format_specific_affiliate(rows, name, d_from, d_to)

        else:  # kpi_summary (default)
            data  = kpi_summary(cursor, d_from, d_to)
            reply = format_kpi_summary(data, d_from, d_to)

        cursor.close()
        conn.close()

    except Exception as e:
        log.error(f"DB error: {e}", exc_info=True)
        reply = f"⚠️ Error querying the database: {e}"

    await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main():
    if not ALLOWED_USERS:
        log.warning(
            "TELEGRAM_ALLOWED_USERS is not set — the bot will respond to EVERYONE. "
            "Set it to your Telegram user ID to restrict access."
        )

    log.info(f"Starting bot (allowed users: {ALLOWED_USERS or 'ALL'})")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Bot started, polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.error(f"Fatal: {e}", exc_info=True)
        sys.exit(1)
