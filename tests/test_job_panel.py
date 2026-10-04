"""Jobs started from pages: one at a time, the Screener page's build button and the Report page's."""

import sys
import time
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import job_panel
from invest.jobs import launcher

ROOT = Path(__file__).resolve().parents[1]

# One company line in the shape the fundamentals job prints, then a clean exit.
FAKE_FUNDAMENTALS = [sys.executable, "-c", "print('  MMM (66740): 512 facts, SIC 3841')"]
# One step line in the shape the weekly job prints, then the options it was given.
FAKE_WEEKLY = [sys.executable, "-c", "import sys; print('[portfolio] done, 0s'); print(sys.argv[1:])"]
SLEEPER = [sys.executable, "-c", "import time; time.sleep(1.5)"]


@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.setenv("INVEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INVEST_DB", str(tmp_path / "absent.duckdb"))
    monkeypatch.setitem(launcher.JOBS, "fundamentals", FAKE_FUNDAMENTALS)
    monkeypatch.setitem(launcher.JOBS, "weekly", FAKE_WEEKLY)
    monkeypatch.setenv("INVEST_REPORTS_DIR", str(tmp_path / "reports"))
    st.cache_resource.clear()  # the job registry is shared server-wide; start clean
    yield tmp_path
    run = job_panel._registry()["current"]
    while run is not None and run.running:
        time.sleep(0.05)
    st.cache_resource.clear()


def test_a_second_job_waits_for_the_first(clean, monkeypatch):
    """Both jobs write the one market database, so the second click is ignored."""
    monkeypatch.setitem(launcher.JOBS, "refresh", SLEEPER)
    job_panel.start("refresh")
    job_panel.start("fundamentals")
    assert job_panel._registry()["current"].name == "refresh"
    assert [p.name.split("_")[0] for p in (clean / "logs").glob("*.log")] == ["refresh"]


def _screener():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    return at.switch_page("pages/screener.py").run()


def _build_button(at):
    return next(b for b in at.button if b.label == "Build the screener table")


def test_without_the_sec_setting_the_button_is_off_and_the_page_says_why(clean, monkeypatch):
    monkeypatch.setattr("invest.secrets.sec_user_agent", lambda: None)
    at = _screener()
    assert not at.exception
    assert _build_button(at).disabled
    assert any("SEC_USER_AGENT" in w.value and "secrets.toml" in w.value for w in at.warning)


def test_the_build_button_runs_the_job_and_reports_the_outcome(clean, monkeypatch):
    monkeypatch.setattr("invest.secrets.sec_user_agent", lambda: "test-app test@example.com")
    at = _screener()
    assert not at.warning
    _build_button(at).click().run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert any(label.startswith("✅ Last screener build from the app") for label in labels), labels
    assert len(list((clean / "logs").glob("fundamentals_*.log"))) == 1


def _report():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    return at.switch_page("pages/report.py").run()


def _report_button(at):
    return next(b for b in at.button if b.label == "Build the report now")


def _weekly_log(directory):
    logs = list((directory / "logs").glob("weekly_*.log"))
    assert len(logs) == 1  # one click, one job
    return logs[0].read_text(encoding="utf-8")


def test_the_report_button_runs_the_weekly_job_and_reports_the_outcome(clean):
    at = _report()
    assert not at.exception
    assert any("button below" in i.value for i in at.info)
    _report_button(at).click().run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert any(label.startswith("✅ Last weekly report from the app") for label in labels), labels
    assert "[]" in _weekly_log(clean)  # fetches first, as the command line does by default


def test_unticking_the_box_builds_from_the_stored_data(clean):
    at = _report()
    at.checkbox[0].uncheck().run()
    _report_button(at).click().run()
    assert not at.exception
    assert "['--skip-refresh', '--skip-filings']" in _weekly_log(clean)
