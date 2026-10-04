"""Report page: the archived weekly reports, a button to build a new one, and the run history.

The button runs ``python -m invest.jobs.weekly`` in a separate process, the same
command the scheduler uses; with the box unticked it adds ``--skip-refresh
--skip-filings`` and builds from the data already stored.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

import job_panel
from invest.data.cache import Store
from invest.paths import db_path, reports_dir

st.title("Weekly report")

job_panel.follow_if_running()  # a job holds the database; nothing below may read it

BUILD_HELP = (
    "Runs `python -m invest.jobs.weekly` in the background: values the portfolio, then assembles the "
    "scorecard, positioning and screens from the market database, archives the report and sends it by "
    "email or Telegram when configured. The screener table is used as stored; rebuild it on the Screener page."
)
REUSE_DATA = ("--skip-refresh", "--skip-filings")

directory: Path = reports_dir()
files = sorted(directory.glob("report_*.md"), reverse=True) if directory.exists() else []

if not files:
    st.info(
        "No archived report yet. Build the first one with the button below. Outside the app, "
        "`python -m invest.jobs.weekly` does the same thing (add `--skip-refresh --skip-filings` to reuse the "
        f"data already stored). Reports are archived in `{directory}`."
    )

left, right = st.columns([3, 2], vertical_alignment="center")
fetch_first = left.checkbox(
    "Fetch fresh market data and 13F filings first",
    value=True,
    help="Takes several minutes. Untick it to build from the data already stored, for example right after "
         "a refresh on the Macro page.",
)
with right:
    job_panel.start_button("weekly", "Build the report now", job_args=() if fetch_first else REUSE_DATA,
                           type="primary" if not files else "secondary", help=BUILD_HELP)
job_panel.last_result("weekly")

if files:
    names = [f.stem.replace("report_", "") for f in files]
    choice = st.selectbox("Report", names, index=0)
    path = files[names.index(choice)]
    html_path = path.with_suffix(".html")
    st.caption(f"{path.name}" + (f"; HTML copy {html_path.name}" if html_path.exists() else ""))
    text = path.read_text(encoding="utf-8").replace("[TOC]", "")
    st.markdown(text, unsafe_allow_html=True)

st.markdown("---")
st.subheader("Runs")
DB = db_path()
if DB.exists():
    try:
        with Store(DB, read_only=True) as store:
            runs = store.runs(limit=30)
        if not runs.empty:
            st.dataframe(runs[["run_id", "kind", "started", "finished", "sources_ok", "sources_failed", "notes"]],
                         width="stretch", hide_index=True)
        else:
            st.caption("No runs recorded.")
    except Exception as exc:
        st.caption(f"Run history unavailable: {exc}")
else:
    st.caption("No market database yet.")
