"""Start a background job from a page and follow it; shared by the Macro and Screener pages.

A job runs as its own process through ``invest.jobs.launcher``: exactly the command
the scheduler and the command line use. Both jobs write the one market database, and
DuckDB lets only one process write it at a time, so only one job runs at a time,
whichever page started it. While it runs, every page that uses this shows its progress
instead of reading the locked database. Leaving the page does not stop the job, and
coming back reattaches to it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import streamlit as st

from invest.jobs import launcher


def _catalog_size() -> int:
    from invest.macro.catalog import load_catalog

    return len(load_catalog().fetched())


def _universe_size() -> int:
    from invest.fundamentals.universe import load_universe
    from invest.jobs.fundamentals import DEFAULT_UNIVERSE

    return len(load_universe()[DEFAULT_UNIVERSE].tickers)


@dataclass(frozen=True)
class Job:
    noun: str  # "Last <noun> from the app"
    doing: str  # shown while it runs
    unit: str  # what the progress bar counts
    total: Callable[[], int]
    ok: str  # outcome on exit status 0
    problems: str  # outcome on any other status, when it did not crash
    problems_note: str


JOBS = {
    "refresh": Job(
        noun="refresh",
        doing="Refreshing the market database",
        unit="series fetched",
        total=_catalog_size,
        ok="finished, every critical series fresh",
        problems="finished with critical series failed or stale",
        problems_note="The data that did arrive is stored. The freshness table at the bottom names the rest.",
    ),
    "fundamentals": Job(
        noun="screener build",
        doing="Building the screener table: company filings from SEC EDGAR, then prices",
        unit="companies fetched",
        total=_universe_size,
        ok="finished",
        problems="finished with problems",
        problems_note="Companies that did arrive are in the table. The log below names the rest, and why.",
    ),
}


@st.cache_resource
def _registry() -> dict:
    """Shared by every browser session and page on this server."""
    return {"lock": threading.Lock(), "current": None, "last": {}}


def start(name: str) -> None:
    registry = _registry()
    with registry["lock"]:
        current = registry["current"]
        if current is not None and current.running:
            return  # one at a time; the rerun shows the job already going
        registry["current"] = launcher.start(name)


def start_button(name: str, label: str, **kwargs) -> None:
    st.button(label, on_click=start, args=(name,), **kwargs)


def _finish(run: launcher.JobRun) -> None:
    registry = _registry()
    with registry["lock"]:
        if registry["current"] is run:
            registry["last"][run.name], registry["current"] = run, None


def follow_if_running() -> None:
    """While a job runs, show its progress and live log, then rerun the page on the new data.

    Returns straight away when no job is running. While one is, it does not return:
    the page below it would try to read the database the job has locked.
    """
    run = _registry()["current"]
    if run is None:
        return
    if not run.running:  # it finished while nobody was watching
        _finish(run)
        return
    job = JOBS[run.name]
    total = job.total()
    st.info(
        f"{job.doing}, started {run.started:%H:%M:%S}. The page reloads when it finishes. "
        f"You can leave and come back; the job keeps running."
    )
    bar = st.progress(0.0)
    box = st.empty()
    while run.running:
        done = run.done()
        bar.progress(
            min(done / total, 1.0) if total else 0.0,
            text=f"{done} of {total} {job.unit}, {run.elapsed_seconds():.0f}s",
        )
        box.code(run.tail(20) or "starting…", language=None)
        time.sleep(1.0)
    _finish(run)
    st.rerun()


def last_result(name: str) -> None:
    """The outcome of the last run of ``name`` started from the app, with its log."""
    run = _registry()["last"].get(name)
    if run is None:
        return
    job = JOBS[name]
    when = f"{run.started:%Y-%m-%d %H:%M}"
    if run.exit_code == 0:
        label = f"✅ Last {job.noun} from the app, started {when}: {job.ok}"
    elif run.crashed:
        label = f"❌ Last {job.noun} from the app, started {when}: stopped with an error"
    else:
        label = f"⚠️ Last {job.noun} from the app, started {when}: {job.problems}"
    with st.expander(label, expanded=run.crashed):
        if run.crashed:
            st.write(
                "If another job was writing the market database at the same time, it was locked. "
                "Wait for it to finish and try again."
            )
        elif run.exit_code:
            st.write(job.problems_note)
        st.code(run.tail(60), language=None)
        st.caption(f"Full log: {run.log_path}")
