#!/usr/bin/env bash
# Installs a monthly cron job that emails the board report on the 1st of each month.
# Usage: bash scripts/install_cron.sh you@example.com

EMAIL="${1:-}"
if [ -z "$EMAIL" ]; then
  echo "Usage: bash scripts/install_cron.sh your@email.com"
  exit 1
fi

DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$DIR/logs/cron.log"
mkdir -p "$DIR/logs"

CRON_LINE="0 9 1 * * cd \"$DIR\" && make board-report EMAIL=$EMAIL >> \"$LOG\" 2>&1"

# Add only if not already present
if crontab -l 2>/dev/null | grep -qF "$DIR/logs/cron.log"; then
  echo "Cron job already installed. Edit with: crontab -e"
else
  (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
  echo "Done. Board report will email $EMAIL on the 1st of every month at 9am."
  echo "Logs: $LOG"
  echo "To edit: crontab -e"
fi
