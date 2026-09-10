"""Report page: the archived weekly reports and the run history."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from invest.data.cache import Store
from invest.paths import db_path, reports_dir

st.title("Weekly report")

directory: Path = reports_dir()
files = sorted(directory.glob("report_*.md"), reverse=True) if directory.exists() else []

if not files:
    st.info(
        "No archived report yet. Run `python -m invest.jobs.weekly` (add `--skip-refresh` to reuse the data "
        f"already stored). Reports are archived in `{directory}`."
    )
else:
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
