"""A dependency-free scheduler for the jobs container.

Runs the weekly chain (refresh, filings, fundamentals, weekly report) at the
time named by ``SCHEDULER_WEEKLY`` (``"sat 08:00"``) and the backup at
``SCHEDULER_BACKUP`` (``"daily 03:00"``), then sleeps. Every run is appended to
``$INVEST_DATA_DIR/scheduler.log`` with its exit status; a missed run is
noticed because no report arrives, and the log says why.

    python scripts/scheduler.py            # loop forever
    python scripts/scheduler.py --once weekly   # run one chain now and exit
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
CHAINS = {
    "weekly": [
        [sys.executable, "-m", "invest.jobs.refresh", "--quiet"],
        [sys.executable, "-m", "invest.jobs.filings"],
        [sys.executable, "-m", "invest.jobs.fundamentals"],
        [sys.executable, "-m", "invest.jobs.weekly", "--skip-refresh", "--skip-filings"],
    ],
    "backup": [[sys.executable, "-m", "invest.jobs.backup"]],
}


def parse_schedule(text: str) -> tuple[int | None, int, int]:
    """``"sat 08:00"`` -> (5, 8, 0); ``"daily 03:00"`` -> (None, 3, 0)."""
    day_text, clock = text.strip().lower().split()
    hour, minute = (int(x) for x in clock.split(":"))
    day = None if day_text == "daily" else DAYS[day_text[:3]]
    return day, hour, minute


def next_run(schedule: tuple[int | None, int, int], now: datetime) -> datetime:
    day, hour, minute = schedule
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day is None:
        return candidate if candidate > now else candidate + timedelta(days=1)
    ahead = (day - now.weekday()) % 7
    candidate = candidate + timedelta(days=ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def log_path() -> Path:
    directory = Path(os.environ.get("INVEST_DATA_DIR", "data"))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "scheduler.log"


def run_chain(name: str) -> int:
    worst = 0
    with log_path().open("a", encoding="utf-8") as log:
        log.write(f"==== {datetime.now():%Y-%m-%d %H:%M:%S} {name} ====\n")
        for command in CHAINS[name]:
            log.write(f"$ {' '.join(command[1:])}\n")
            log.flush()
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
            log.write(f"exit {result.returncode}\n")
            worst = max(worst, result.returncode)
        log.write(f"==== {name} finished with status {worst} ====\n")
    return worst


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", choices=sorted(CHAINS), help="run one chain now and exit")
    args = parser.parse_args(argv)
    if args.once:
        return run_chain(args.once)

    schedules = {
        "weekly": parse_schedule(os.environ.get("SCHEDULER_WEEKLY", "sat 08:00")),
        "backup": parse_schedule(os.environ.get("SCHEDULER_BACKUP", "daily 03:00")),
    }
    while True:
        now = datetime.now()
        upcoming = {name: next_run(s, now) for name, s in schedules.items()}
        name, when = min(upcoming.items(), key=lambda kv: kv[1])
        wait = (when - now).total_seconds()
        print(f"next: {name} at {when:%Y-%m-%d %H:%M}", flush=True)
        time.sleep(max(1.0, wait))
        run_chain(name)


if __name__ == "__main__":
    sys.exit(main())
