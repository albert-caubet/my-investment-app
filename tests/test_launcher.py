"""Starting jobs from the app: the launcher, and the Macro page's refresh button."""

import sys
import time
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from invest.jobs import launcher

ROOT = Path(__file__).resolve().parents[1]

# Two lines in the exact shape refresh prints, then a clean exit.
FAKE_REFRESH = [
    sys.executable,
    "-c",
    "print('  US_UNRATE: 912 obs, 912 new'); print('  EA_HICP: FAILED no observations returned')",
]


def _wait(run, timeout=30.0):
    deadline = time.monotonic() + timeout
    while run.running and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not run.running, "job did not finish"
    return run


def test_count_series_lines_matches_what_refresh_prints():
    text = "\n".join(
        [
            "$ -m invest.jobs.refresh",
            "  US_UNRATE: 912 obs, 0 new",
            "  EA_HICP: FAILED HTTP 503",
            "  derived US_REAL_RATE: 400 values, 3 new",  # derived lines are not fetched series
            "  releases: 4 entries, 0 new",
            "  regime: Late cycle (curve)",
            "series  source  status",  # the final table
        ]
    )
    assert launcher.count_series_lines(text) == 2


def test_fundamentals_progress_counts_companies_fetched_or_failed(tmp_path):
    lines = [
        "  MMM (66740): 512 facts, SIC 3841",
        "  NOPE: FAILED HTTP 404",
        "  prices: 2 of 3 symbols",  # the price step, not a company
        "  MMM: metrics FAILED division by zero",  # the table step, after fetching
        "  BREADTH_ABOVE_200D_PCT: 2500 days, 3 new",
    ]
    command = [sys.executable, "-c", f"print({chr(10).join(lines)!r})"]
    run = _wait(launcher.start("fundamentals", command=command, directory=tmp_path))
    assert run.done() == 2


def test_registry_runs_the_same_command_as_the_cli():
    command = launcher.JOBS["refresh"]
    assert command[0] == sys.executable
    assert command[-2:] == ["-m", "invest.jobs.refresh"]
    assert "-u" in command  # unbuffered, or the log arrives in lumps
    assert launcher.JOBS["fundamentals"][-2:] == ["-m", "invest.jobs.fundamentals"]
    assert launcher.JOBS["weekly"][-2:] == ["-m", "invest.jobs.weekly"]


def test_args_go_after_the_command(tmp_path):
    command = [sys.executable, "-c", "import sys; print(sys.argv[1:])"]
    run = _wait(launcher.start("weekly", args=("--skip-refresh", "--skip-filings"), command=command,
                               directory=tmp_path))
    assert "['--skip-refresh', '--skip-filings']" in run.text()
    assert run.command[-2:] == ["--skip-refresh", "--skip-filings"]


def test_weekly_progress_counts_steps_not_series(tmp_path):
    lines = [
        "  US_UNRATE: 912 obs, 12 new",  # the refresh inside the weekly job
        "[refresh] done, 183s",
        "[13F holdings] FAILED SEC_USER_AGENT is not set",
        "[fundamentals] skipped",
        "  archived C:/data/reports/report_2026-10-03_0800.html",
    ]
    command = [sys.executable, "-c", f"print({chr(10).join(lines)!r})"]
    run = _wait(launcher.start("weekly", command=command, directory=tmp_path))
    assert run.done() == 3


def test_start_writes_output_to_a_log_file(tmp_path):
    run = _wait(launcher.start("refresh", command=FAKE_REFRESH, directory=tmp_path))
    assert run.exit_code == 0
    assert not run.crashed
    assert run.log_path.parent == tmp_path
    assert run.log_path.name.startswith("refresh_")
    assert run.series_done() == 2
    assert "US_UNRATE: 912 obs" in run.tail()


def test_running_is_true_while_the_child_works(tmp_path):
    run = launcher.start(
        "refresh", command=[sys.executable, "-c", "import time; time.sleep(1.5)"], directory=tmp_path
    )
    assert run.running
    assert run.exit_code is None
    _wait(run)
    assert run.exit_code == 0


def test_a_crash_is_distinguished_from_a_status_code(tmp_path):
    crash = _wait(
        launcher.start("refresh", command=[sys.executable, "-c", "raise RuntimeError('db locked')"],
                       directory=tmp_path)
    )
    status = _wait(
        launcher.start("refresh", command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                       directory=tmp_path)
    )
    assert crash.log_path != status.log_path  # same second, still separate logs
    assert crash.exit_code != 0 and crash.crashed
    assert "db locked" in crash.tail()
    assert status.exit_code == 1 and not status.crashed  # critical series stale: a verdict, not a crash


def test_the_child_inherits_the_database_location(tmp_path, monkeypatch):
    monkeypatch.setenv("INVEST_DB", str(tmp_path / "market.duckdb"))
    run = _wait(
        launcher.start("refresh", command=[sys.executable, "-c", "import os; print(os.environ['INVEST_DB'])"],
                       directory=tmp_path)
    )
    assert str(tmp_path / "market.duckdb") in run.text()


# --- the Macro page -------------------------------------------------------------------


@pytest.fixture
def empty_macro(tmp_path, monkeypatch):
    monkeypatch.setenv("INVEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INVEST_DB", str(tmp_path / "absent.duckdb"))
    monkeypatch.setitem(launcher.JOBS, "refresh", FAKE_REFRESH)
    st.cache_resource.clear()  # the job registry is shared server-wide; start clean
    yield tmp_path
    st.cache_resource.clear()


def _macro():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    return at.switch_page("pages/macro.py").run()


def test_empty_database_offers_the_button(empty_macro):
    at = _macro()
    assert not at.exception
    assert any("invest.jobs.refresh" in i.value for i in at.info)
    assert [b.label for b in at.button] == ["Fetch the data now"]


def test_button_runs_the_job_and_reports_the_outcome(empty_macro):
    at = _macro()
    at.button[0].click().run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert any(label.startswith("✅ Last refresh from the app") for label in labels), labels
    logs = list((empty_macro / "logs").glob("refresh_*.log"))
    assert len(logs) == 1  # one click, one job
