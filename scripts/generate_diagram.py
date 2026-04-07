#!/usr/bin/env python3
"""
Netrefer Reporting — System Architecture Diagram
Generates architecture.pdf in the project root.
Usage: python scripts/generate_diagram.py
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.patheffects as pe

# ── Canvas ───────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(16, 10))
ax.set_xlim(0, 16)
ax.set_ylim(0, 10)
ax.axis("off")
fig.patch.set_facecolor("#FFFFFF")

# ── Colour palette ────────────────────────────────────────────────────────────
C = {
    "bg":       "#F8F9FA",
    "docker":   "#E8F4FD",
    "docker_b": "#4A90D9",
    "csv":      "#FFF3CD",
    "csv_b":    "#F0AD4E",
    "etl":      "#D1ECF1",
    "etl_b":    "#17A2B8",
    "db":       "#D4EDDA",
    "db_b":     "#28A745",
    "mb":       "#E8DAEF",
    "mb_b":     "#8E44AD",
    "script":   "#FFE5D9",
    "script_b": "#E67E22",
    "user":     "#FADBD8",
    "user_b":   "#E74C3C",
    "arrow":    "#2C3E50",
    "title":    "#1A252F",
    "text":     "#2C3E50",
    "sub":      "#6C757D",
}


def box(ax, x, y, w, h, fc, ec, label, sublabel="", icon="", radius=0.3, lw=2):
    """Draw a rounded box with label."""
    patch = FancyBboxPatch((x, y), w, h,
                           boxstyle=f"round,pad=0,rounding_size={radius}",
                           linewidth=lw, edgecolor=ec, facecolor=fc, zorder=3)
    ax.add_patch(patch)
    cy = y + h / 2
    if icon:
        ax.text(x + 0.35, cy + (0.12 if sublabel else 0), icon,
                ha="center", va="center", fontsize=18, zorder=4)
        tx = x + 0.72
    else:
        tx = x + w / 2
    ax.text(tx, cy + (0.15 if sublabel else 0), label,
            ha="center", va="center", fontsize=10, fontweight="bold",
            color=C["text"], zorder=4)
    if sublabel:
        ax.text(tx, cy - 0.22, sublabel,
                ha="center", va="center", fontsize=7.5,
                color=C["sub"], zorder=4)


def arrow(ax, x1, y1, x2, y2, label="", color="#2C3E50"):
    """Draw a labelled arrow."""
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color,
                                lw=1.8, mutation_scale=16),
                zorder=2)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx, my + 0.18, label, ha="center", va="bottom",
                fontsize=7.5, color=C["sub"],
                bbox=dict(fc="white", ec="none", pad=1))


def dashed_box(ax, x, y, w, h, color, label):
    """Draw a dashed container rectangle with a title tab."""
    rect = plt.Rectangle((x, y), w, h, linewidth=1.5,
                          edgecolor=color, facecolor=C["docker"],
                          linestyle="--", zorder=1, alpha=0.7)
    ax.add_patch(rect)
    ax.text(x + 0.25, y + h - 0.08, label,
            fontsize=8, fontweight="bold", color=color,
            va="top", zorder=2)


# ═══════════════════════════════════════════════════════════════════════════════
# Title
# ═══════════════════════════════════════════════════════════════════════════════
ax.text(8, 9.65, "Netrefer Reporting  —  System Architecture",
        ha="center", va="center", fontsize=17, fontweight="bold",
        color=C["title"])
ax.text(8, 9.35, "Automated affiliate data pipeline: CSV export → ETL → MySQL → Metabase dashboards",
        ha="center", va="center", fontsize=9.5, color=C["sub"])
ax.axhline(9.2, color="#DEE2E6", lw=1)

# ═══════════════════════════════════════════════════════════════════════════════
# Docker boundary
# ═══════════════════════════════════════════════════════════════════════════════
dashed_box(ax, 2.8, 1.5, 10.6, 7.3, C["docker_b"], "🐳  Docker Compose")

# ═══════════════════════════════════════════════════════════════════════════════
# Left: CSV input
# ═══════════════════════════════════════════════════════════════════════════════
box(ax, 0.2, 7.2, 2.2, 1.1, C["csv"], C["csv_b"],
    "Netrefer CSV", "netrefer_YYYY-MM-DD.csv", "📄")

box(ax, 0.2, 5.2, 2.2, 1.1, C["csv"], C["csv_b"],
    "drop/ folder", "incoming CSVs", "📁")

box(ax, 0.2, 3.2, 2.2, 1.1, C["csv"], C["csv_b"],
    "processed/", "archived after load", "📦")

# ═══════════════════════════════════════════════════════════════════════════════
# ETL container
# ═══════════════════════════════════════════════════════════════════════════════
dashed_box(ax, 3.1, 5.7, 3.8, 2.7, C["etl_b"], "  etl container")

box(ax, 3.3, 7.1, 3.4, 1.0, C["etl"], C["etl_b"],
    "watcher.py", "polls every 60 s", "👁")

box(ax, 3.3, 5.9, 3.4, 1.0, C["etl"], C["etl_b"],
    "netrefer_etl.py", "parse + UPSERT 500/batch", "⚙️")

# ═══════════════════════════════════════════════════════════════════════════════
# MySQL container
# ═══════════════════════════════════════════════════════════════════════════════
dashed_box(ax, 7.3, 3.8, 3.6, 4.6, C["db_b"], "  db container · MySQL 8")

box(ax, 7.5, 6.9, 3.2, 0.95, C["db"], C["db_b"],
    "netrefer_stats", "report_date × affiliate × campaign", "🗄")

box(ax, 7.5, 5.7, 3.2, 0.95, C["db"], C["db_b"],
    "etl_runs", "audit log for every load", "📋")

box(ax, 7.5, 4.5, 3.2, 0.95, C["db"], C["db_b"],
    "Views", "v_netrefer_daily · v_netrefer_kpis", "🔭")

# ═══════════════════════════════════════════════════════════════════════════════
# Metabase container
# ═══════════════════════════════════════════════════════════════════════════════
dashed_box(ax, 11.3, 3.8, 3.8, 4.6, C["mb_b"], "  metabase container · :3001")

box(ax, 11.5, 7.0, 3.4, 0.85, C["mb"], C["mb_b"],
    "Daily Dashboard", "KPIs + trends + drill-down", "📊")

box(ax, 11.5, 5.9, 3.4, 0.85, C["mb"], C["mb_b"],
    "Board Dashboard", "C-level monthly summary", "📈")

box(ax, 11.5, 4.8, 3.4, 0.85, C["mb"], C["mb_b"],
    "ETL Health", "load monitoring", "🩺")

# ═══════════════════════════════════════════════════════════════════════════════
# Setup scripts (bottom-left)
# ═══════════════════════════════════════════════════════════════════════════════
box(ax, 3.1, 1.7, 3.8, 1.3, C["script"], C["script_b"],
    "Setup Scripts", "setup_daily_dashboard.py\nrun once via  make  commands", "🛠")

# User (bottom-right)
box(ax, 11.3, 1.7, 3.8, 1.3, C["user"], C["user_b"],
    "Browser / User", "localhost:3001\nfilter by date & affiliate", "👤")

# ═══════════════════════════════════════════════════════════════════════════════
# Arrows
# ═══════════════════════════════════════════════════════════════════════════════
# CSV → drop/
arrow(ax, 1.3, 7.2, 1.3, 6.3, "rename file")
# drop/ → watcher
arrow(ax, 2.4, 5.75, 3.3, 5.75, "new file\ndetected")
# watcher → ETL processor
arrow(ax, 5.0, 7.1, 5.0, 6.9, "trigger")
# ETL → MySQL
arrow(ax, 6.7, 6.4, 7.3, 6.4, "UPSERT\n500 rows/batch")
# ETL → processed/
arrow(ax, 3.3, 5.9, 1.3, 4.3, "archive\nafter load")
# MySQL → Metabase
arrow(ax, 10.9, 6.4, 11.3, 6.4, "SQL queries")
# Setup scripts → Metabase
ax.annotate("", xy=(12.7, 3.8), xytext=(5.3, 3.0),
            arrowprops=dict(arrowstyle="-|>", color=C["script_b"],
                            lw=1.5, linestyle="dashed", mutation_scale=14),
            zorder=2)
ax.text(9.5, 3.3, "Metabase API  (run once)", ha="center",
        fontsize=7.5, color=C["script_b"])
# User → Metabase
arrow(ax, 13.2, 3.1, 13.2, 3.8, "open browser")

# ═══════════════════════════════════════════════════════════════════════════════
# Legend
# ═══════════════════════════════════════════════════════════════════════════════
legend_y = 0.55
items = [
    (C["csv"],    C["csv_b"],    "Files / Folders"),
    (C["etl"],    C["etl_b"],    "ETL Pipeline"),
    (C["db"],     C["db_b"],     "MySQL Database"),
    (C["mb"],     C["mb_b"],     "Metabase BI"),
    (C["script"], C["script_b"], "Setup Scripts"),
    (C["user"],   C["user_b"],   "User / Browser"),
]
for i, (fc, ec, label) in enumerate(items):
    x = 1.2 + i * 2.4
    patch = FancyBboxPatch((x, legend_y), 0.5, 0.35,
                           boxstyle="round,pad=0,rounding_size=0.08",
                           linewidth=1.5, edgecolor=ec, facecolor=fc, zorder=3)
    ax.add_patch(patch)
    ax.text(x + 0.65, legend_y + 0.175, label,
            va="center", fontsize=8, color=C["text"])

ax.axhline(0.5, color="#DEE2E6", lw=1)
ax.text(8, 0.2, "ramadinoramic / reportingnetrefer  •  Netrefer Affiliate Reporting Pipeline",
        ha="center", va="center", fontsize=8, color=C["sub"])

# ═══════════════════════════════════════════════════════════════════════════════
# Save
# ═══════════════════════════════════════════════════════════════════════════════
plt.tight_layout(pad=0)
out = "architecture.pdf"
plt.savefig(out, format="pdf", dpi=150, bbox_inches="tight",
            facecolor="white", edgecolor="none")
print(f"✓  Saved: {out}")
plt.close()
