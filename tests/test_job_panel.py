"""Jobs started from pages: one at a time, and the Screener page's build button."""

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
SLEEPER = [sys.executable, "-c", "import time; time.sleep(1.5)"]


@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.setenv("INVEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INVEST_DB", str(tmp_path / "absent.duckdb"))
    monkeypatch.setitem(launcher.JOBS, "fundamentals", FAKE_FUNDAMENTALS)
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
