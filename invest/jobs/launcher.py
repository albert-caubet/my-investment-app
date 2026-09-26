"""Start a job from the app without the app hanging on it.

The pages never fetch on their own; this is what runs when you ask them to. A job
is started as a separate process, exactly the command the scheduler and the CLI
use, so there is one code path however a refresh is triggered.

Output goes to a log file, not a pipe. A page that streams a pipe stops reading
the moment you navigate away, the pipe fills, and the child blocks forever halfway
through writing the database. A file never fills, so the job finishes whether or
not anyone is watching, and a page opened later can reattach by reading the file.

No Streamlit import here, in keeping with the rest of ``invest.jobs``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from invest.paths import REPO_ROOT, data_dir

#: What each job name runs. ``-u`` keeps the child's output unbuffered, so the log
#: shows progress as it happens rather than in 8 KB lumps.
JOBS: dict[str, list[str]] = {
    "refresh": [sys.executable, "-u", "-m", "invest.jobs.refresh"],
}

# refresh prints "  SERIES_ID: 312 obs, 12 new" or "  SERIES_ID: FAILED ..." per series.
_SERIES_LINE = re.compile(r"^  (\S+): (?:\d+ obs|FAILED)")


def logs_dir() -> Path:
    return data_dir() / "logs"


@dataclass
class JobRun:
    name: str
    command: list[str]
    log_path: Path
    started: datetime
    process: subprocess.Popen = field(repr=False)

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    @property
    def exit_code(self) -> int | None:
        return self.process.poll()

    @property
    def crashed(self) -> bool:
        """Died on an exception rather than finishing with a status of its own."""
        return self.exit_code not in (None, 0) and "Traceback (most recent call last)" in self.text()

    def text(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def tail(self, lines: int = 40) -> str:
        return "\n".join(self.text().splitlines()[-lines:])

    def series_done(self) -> int:
        return count_series_lines(self.text())

    def elapsed_seconds(self, now: datetime | None = None) -> float:
        return ((now or datetime.now()) - self.started).total_seconds()


def count_series_lines(text: str) -> int:
    """How many series the refresh has finished, fetched or failed."""
    return sum(1 for line in text.splitlines() if _SERIES_LINE.match(line))


def start(name: str, *, command: list[str] | None = None, directory: Path | None = None) -> JobRun:
    """Launch ``name`` in the background and return a handle to it.

    ``command`` overrides the registry, which is what the tests use. The child
    inherits the environment, so ``INVEST_DB`` and ``INVEST_DATA_DIR`` point it at
    the same database the page reads.
    """
    command = list(command or JOBS[name])
    directory = directory or logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    # Microseconds, and "x" mode: two jobs started in the same second must not share
    # a file, or the second truncates the first's log -- traceback and all.
    log_path = directory / f"{name}_{started:%Y%m%d_%H%M%S_%f}.log"

    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash on Windows
    with log_path.open("x", encoding="utf-8") as log:
        log.write(f"$ {' '.join(command[1:])}\nstarted {started:%Y-%m-%d %H:%M:%S}\n\n")
        log.flush()
        # The child inherits the handle; closing ours afterwards leaves it writing.
        process = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
    return JobRun(name, command, log_path, started, process)
