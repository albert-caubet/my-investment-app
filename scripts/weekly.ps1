# Weekly job for Windows Task Scheduler (PLAN.md section 8, Stage A).
#
# Register once, from the repository root, to run every Saturday at 08:00:
#
#   $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#              -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\weekly.ps1`"" -WorkingDirectory "$PWD"
#   $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 8:00
#   Register-ScheduledTask -TaskName "InvestWeeklyReport" -Action $action -Trigger $trigger -Description "Weekly rebalancing report"
#
# Secrets (SEC_USER_AGENT, FRED_API_KEY, SMTP_*, TELEGRAM_*) come from the environment
# or .streamlit/secrets.toml; the task runs with the user's environment.

$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$env:PYTHONUTF8 = "1"
$python = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$log = Join-Path $PWD "data\weekly.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
"==== $(Get-Date -Format s) weekly job ====" | Out-File -Append -Encoding utf8 $log

# Data first (each job records its own failures), then the report, which reuses the data.
& $python -m invest.jobs.refresh --quiet 2>&1 | Out-File -Append -Encoding utf8 $log
& $python -m invest.jobs.filings 2>&1 | Out-File -Append -Encoding utf8 $log
& $python -m invest.jobs.fundamentals 2>&1 | Out-File -Append -Encoding utf8 $log
& $python -m invest.jobs.weekly --skip-refresh --skip-filings 2>&1 | Out-File -Append -Encoding utf8 $log
$code = $LASTEXITCODE
"==== exit $code ====" | Out-File -Append -Encoding utf8 $log
exit $code
