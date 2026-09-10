#!/usr/bin/env sh
# Weekly job for cron (PLAN.md section 8, Stage A). Saturday 08:00:
#
#   0 8 * * 6  cd /path/to/my-investment-app && ./scripts/weekly.sh
#
# Secrets (SEC_USER_AGENT, FRED_API_KEY, SMTP_*, TELEGRAM_*) come from the environment
# or .streamlit/secrets.toml.
set -u
cd "$(dirname "$0")/.."
export PYTHONUTF8=1
PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"
mkdir -p data
LOG="data/weekly.log"
echo "==== $(date -Iseconds) weekly job ====" >> "$LOG"
"$PYTHON" -m invest.jobs.refresh --quiet >> "$LOG" 2>&1
"$PYTHON" -m invest.jobs.filings >> "$LOG" 2>&1
"$PYTHON" -m invest.jobs.fundamentals >> "$LOG" 2>&1
"$PYTHON" -m invest.jobs.weekly --skip-refresh --skip-filings >> "$LOG" 2>&1
CODE=$?
echo "==== exit $CODE ====" >> "$LOG"
exit $CODE
