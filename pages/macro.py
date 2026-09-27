"""Macro page: regime, scorecard, charts, releases, freshness. Reads DuckDB only.

Every figure carries its observation date. Loading the page never calls the
network: the data is whatever ``python -m invest.jobs.refresh`` last stored, and
the footer says when that was. The refresh button runs that same command in a
separate process, so fetching happens only when asked for.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import charts
from invest.data.cache import Store, utcnow
from invest.data.releases import Release, append_release, load_releases
from invest.jobs import launcher
from invest.jobs.refresh import ingest_releases
from invest.macro import scorecard as sc
from invest.macro.catalog import GROUPS, load_catalog
from invest.macro.indicators import format_value, recession_spans
from invest.macro.regimes import thresholds_for
from invest.paths import db_path

st.title("Macro")

DB = db_path()

# ==========================================================================================
# Refresh from the app
# ==========================================================================================


@st.cache_resource
def _jobs() -> dict:
    """Shared by every session on this server, so a second tab sees a run in progress
    instead of starting another one against a locked database."""
    return {"lock": threading.Lock(), "current": None, "last": None}


def _start_refresh() -> None:
    jobs = _jobs()
    with jobs["lock"]:
        current = jobs["current"]
        if current is not None and current.running:
            return  # already going; the rerun reattaches to it
        jobs["current"] = launcher.start("refresh")


def _follow(run: launcher.JobRun) -> None:
    """Stream a running refresh until it ends, then reload the page on the new data.

    The database is not read meanwhile: the job holds DuckDB's write lock for the
    whole run. Leaving the page does not stop the job; coming back reattaches.
    """
    total = len(load_catalog().fetched())
    st.info(
        f"Refreshing the market database, started {run.started:%H:%M:%S}. The page reloads with "
        f"the new data when it finishes. You can leave and come back; the job keeps running."
    )
    bar = st.progress(0.0)
    box = st.empty()
    while run.running:
        done = run.series_done()
        bar.progress(
            min(done / total, 1.0) if total else 0.0,
            text=f"{done} of {total} series fetched, {run.elapsed_seconds():.0f}s",
        )
        box.code(run.tail(20) or "starting…", language=None)
        time.sleep(1.0)
    jobs = _jobs()
    with jobs["lock"]:
        if jobs["current"] is run:
            jobs["last"], jobs["current"] = run, None
    st.rerun()


def _last_result() -> None:
    run = _jobs()["last"]
    if run is None:
        return
    when = f"{run.started:%Y-%m-%d %H:%M}"
    if run.exit_code == 0:
        label = f"✅ Last refresh from the app, started {when}: finished, every critical series fresh"
    elif run.crashed:
        label = f"❌ Last refresh from the app, started {when}: stopped with an error"
    else:
        label = f"⚠️ Last refresh from the app, started {when}: finished with critical series failed or stale"
    with st.expander(label, expanded=run.crashed):
        if run.crashed:
            st.write(
                "If the weekly job or a command-line refresh was running at the same time, the database "
                "was locked. Wait for it to finish and try again."
            )
        elif run.exit_code:
            st.write("The data that did arrive is stored. The freshness table at the bottom names the rest.")
        st.code(run.tail(60), language=None)
        st.caption(f"Full log: {run.log_path}")


_current = _jobs()["current"]
if _current is not None and _current.running:
    _follow(_current)  # reruns when done; never falls through
elif _current is not None:  # finished while nobody was watching
    with _jobs()["lock"]:
        _jobs()["last"], _jobs()["current"] = _current, None

if not DB.exists():
    st.info(
        f"No market database yet at `{DB}`. The page only reads what a refresh has stored, so there "
        f"is nothing to show until one has run. The first run fetches the full history of every "
        f"series in the catalog and takes several minutes. Outside the app, "
        f"`python -m invest.jobs.refresh` does the same thing, and is what the scheduler runs."
    )
    st.button("Fetch the data now", type="primary", on_click=_start_refresh)
    _last_result()
    st.stop()


def _db_version() -> float:
    """Cache key: the file's modification time, so a refresh invalidates the page."""
    try:
        return os.path.getmtime(DB)
    except OSError:
        return 0.0


@st.cache_data(ttl=600, show_spinner="Reading the market database…")
def _load(db: str, version: float, today: date) -> dict:
    with Store(db, read_only=True) as store:
        catalog = load_catalog()
        readings = sc.build_readings(store, catalog, today=today)
        regime, results = sc.build_regime(store, catalog)
        previous = sc.previous_snapshot(store, "scorecard")
        changes = sc.changes_since(readings, results, previous[2] if previous else None)
        freshness = sc.freshness_table(store, catalog, today=today)
        runs = store.runs("refresh", limit=1)
        usrec = store.read_series("US_RECESSION")
    return {
        "readings": readings,
        "regime": regime,
        "results": results,
        "previous_at": previous[1] if previous else None,
        "changes": changes,
        "freshness": freshness,
        "last_run": runs.iloc[0].to_dict() if not runs.empty else None,
        "recessions": recession_spans(usrec),
    }


@st.cache_data(ttl=600, show_spinner=False)
def _series(db: str, version: float, series_id: str, transform: str) -> pd.Series:
    from invest.macro.derived import apply_transform

    with Store(db, read_only=True) as store:
        raw = store.read_series(series_id)
    return apply_transform(raw, transform) if not raw.empty else raw


try:
    data = _load(str(DB), _db_version(), date.today())
except Exception as exc:  # a locked file while the job writes, or a half-built database
    st.warning(
        f"Could not read the market database: {exc}. If the refresh job is running, wait for it "
        f"to finish and reload."
    )
    st.stop()

catalog = load_catalog()
readings = data["readings"]
regime = data["regime"]
results = data["results"]


def _local(stored_utc) -> datetime:
    """Run times are stored as naive UTC; everything on this page shows local time,
    or one run reads as two (12:54 in one line, 14:54 in the next)."""
    local_zone = datetime.now().astimezone().tzinfo
    return pd.Timestamp(stored_utc).tz_localize("UTC").tz_convert(local_zone).tz_localize(None).to_pydatetime()


def _age(stored_utc) -> str:
    hours = (utcnow() - pd.Timestamp(stored_utc).to_pydatetime()).total_seconds() / 3600
    if hours < 1:
        return "under an hour ago"
    if hours < 48:
        return f"{hours:.0f} hours ago"
    return f"{hours / 24:.0f} days ago"


left, right = st.columns([5, 1], vertical_alignment="center")
if data["last_run"]:
    started = data["last_run"]["started"]
    left.caption(f"Data from the refresh started {_local(started):%Y-%m-%d %H:%M}, {_age(started)}.")
else:
    left.caption("No refresh run recorded in this database.")
right.button("Refresh now", on_click=_start_refresh, help="Runs `python -m invest.jobs.refresh` in the background.")
_last_result()

# ==========================================================================================
# Regime
# ==========================================================================================

st.subheader(f"Regime: {regime.label}")
st.caption(regime.explanation)

with st.expander(f"Rules ({len(regime.firing)} counted firing, {len(regime.informational)} informational)"):
    rules_df = pd.DataFrame(
        [
            {
                "Rule": r.rule.description,
                "Group": r.rule.group,
                "Counts": "yes" if r.rule.counts else "no",
                "Firing": {True: "yes", False: "no", None: "no data"}[r.fired],
                "As of": r.as_of.isoformat() if r.as_of else "–",
                "Detail": r.detail,
            }
            for r in results
        ]
    )
    st.dataframe(rules_df, width="stretch", hide_index=True)
    st.caption(
        "Track record and known failures for each indicator are in PLAN.md section 4.5. The 2022 to "
        "2024 episode, when the curve, LEI, ISM manufacturing, the loan officer survey and the Sahm "
        "rule all signalled without a recession, is the reason the label is a summary and not a verdict."
    )

# ==========================================================================================
# Changed since last run
# ==========================================================================================

previous_at = data["previous_at"]
label = f"Changed since {previous_at:%Y-%m-%d}" if isinstance(previous_at, datetime) else "Changed since the previous run"
with st.expander(f"{label} ({len(data['changes'])})", expanded=bool(data["changes"])):
    if previous_at is None:
        st.write("No earlier scorecard snapshot to compare with yet. Each refresh run stores one.")
    elif not data["changes"]:
        st.write("Nothing crossed a threshold and no series printed a new observation.")
    else:
        st.dataframe(
            pd.DataFrame(
                [{"What": c.label, "Change": c.kind, "Before": c.before, "After": c.after, "Detail": c.detail}
                 for c in data["changes"]]
            ),
            width="stretch",
            hide_index=True,
        )

# ==========================================================================================
# Scorecard
# ==========================================================================================

st.subheader("Scorecard")
st.caption(
    "z-scores against the last ten years of each series (the catalog can set another window); "
    "percentiles over the whole history; the concern column is the z-score signed in the direction "
    "of concern, so red means the indicator sits where it has historically been worrying."
)


def _concern_colours(series: pd.Series) -> list[str]:
    styles = []
    for value in pd.to_numeric(series, errors="coerce"):
        if pd.isna(value):
            styles.append("")
            continue
        share = min(abs(float(value)) / 3.0, 1.0)
        alpha = 0.12 + 0.6 * share
        rgb = "214, 39, 40" if value > 0 else "44, 160, 44"
        text = " color: #ffffff;" if alpha > 0.5 else ""
        styles.append(f"background-color: rgba({rgb}, {alpha:.3f});{text}")
    return styles


def _group_table(rows) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Indicator": r.label + (" *" if r.stale else ""),
                "Value": r.value_text,
                "As of": r.obs_date.isoformat() if r.obs_date else "–",
                "z": r.z,
                "Pct": r.percentile,
                "3m chg": r.change_3m,
                "Concern": r.concern,
                "Note": r.note,
            }
            for r in rows
        ]
    )


HELP = {
    "z": "Latest value minus the mean of the trailing window, over its standard deviation (ddof=1).",
    "Pct": "Mid-rank percentile of the latest value within the full history of the series.",
    "3m chg": "Latest value minus the value three months earlier, in the units of the row.",
    "Concern": "z-score signed in the direction of concern: positive is worrying. Blank for neutral indicators.",
    "As of": "Observation date of the latest value. An asterisk on the name marks a series older than its schedule allows.",
}

for group in catalog.groups():
    rows = [r for r in readings if r.group == group]
    if not rows:
        continue
    st.markdown(f"**{GROUPS.get(group, group)}**")
    table = _group_table(rows)
    if not table["Note"].astype(bool).any():
        table = table.drop(columns=["Note"])
    st.dataframe(
        table.style.format({"z": "{:+.2f}", "Pct": "{:.0f}", "3m chg": "{:+.2f}", "Concern": "{:+.2f}"}, na_rep="–")
        .apply(_concern_colours, subset=["Concern"]),
        column_config={c: st.column_config.Column(c, help=h) for c, h in HELP.items() if c in table.columns},
        width="stretch",
        hide_index=True,
    )

# ==========================================================================================
# Chart
# ==========================================================================================

st.subheader("Chart")
options = {f"{r.label} ({r.id})": r.id for r in readings if r.value is not None}
if options:
    choice = st.selectbox("Indicator", list(options), index=0)
    sid = options[choice]
    spec = catalog[sid]
    reading = next(r for r in readings if r.id == sid)
    series_id = sid if not reading.note.startswith("via fallback") else reading.note.split()[-1]
    series = _series(str(DB), _db_version(), series_id, spec.transform)
    years = st.slider("Years shown", 1, 60, 15)
    if not series.empty:
        shown = series[series.index >= series.index[-1] - pd.DateOffset(years=years)]
        fig = go.Figure()
        fig.add_scatter(x=shown.index, y=shown.values, mode="lines", name=spec.label, line=dict(color="#1f77b4"))
        for start, end in data["recessions"]:
            end_ts = pd.Timestamp(end) + pd.offsets.MonthEnd(0)
            if end_ts < shown.index[0] or pd.Timestamp(start) > shown.index[-1]:
                continue
            fig.add_vrect(x0=start, x1=end_ts, fillcolor="grey", opacity=0.18, line_width=0)
        for rule_name, level in thresholds_for(sid).items():
            fig.add_hline(y=level, line_dash="dash", line_color="rgba(214, 39, 40, 0.8)",
                          annotation_text=rule_name, annotation_position="top left")
        fig.update_layout(
            title=f"{spec.label} — {spec.transform}, {spec.units or 'level'}; shaded: NBER recessions",
            template="plotly_white", hovermode="x unified", showlegend=False,
            height=630,  # was 420; 50% taller like every chart in the app
        )
        st.plotly_chart(charts.readable(fig), width="stretch")
    st.write(reading.reading)
    if spec.notes:
        st.caption(spec.notes)
    st.caption(f"Source: {spec.source} {spec.key or spec.formula or ''}; publication lag about {spec.lag_days} days.")

# ==========================================================================================
# Record a release
# ==========================================================================================

release_specs = catalog.releases()
with st.expander("Record a release (ISM, PMIs, LEI, nowcasts, sentiment surveys)"):
    st.write(
        "Headline figures whose histories are licensed are recorded by hand on release day. The entry "
        "is appended to `config/releases.toml` and loaded into the database with its release date as "
        "the vintage, so history accrues from today."
    )
    with st.form("release_form"):
        labels = {f"{s.label} ({s.id})": s for s in release_specs}
        picked = st.selectbox("Series", list(labels))
        c1, c2, c3 = st.columns(3)
        period_text = c1.text_input("Period (YYYY-MM, YYYY-Qn, or a date)", value=f"{date.today():%Y-%m}")
        released = c2.date_input("Release date", value=date.today())
        value = c3.number_input("Value", value=0.0, step=0.1, format="%.2f")
        note = st.text_input("Note (source)", value="")
        submitted = st.form_submit_button("Record", key="record_release")
    if submitted:
        from invest.data.ecb import parse_period

        try:
            release = Release(labels[picked].id, parse_period(period_text), released, float(value), note)
            append_release(release)
            with Store(DB) as writer:
                ingest_releases(writer, [release], catalog, fetched_at=datetime.now())
            _load.clear()
            _series.clear()
            st.success(
                f"Recorded {labels[picked].label} = {format_value(release.value, labels[picked].units)} for "
                f"{release.period:%Y-%m}, released {release.released:%Y-%m-%d}."
            )
        except Exception as exc:
            st.error(f"Not recorded: {exc}")
    recorded = load_releases()
    if recorded:
        st.caption(f"{len(recorded)} release entries on file; the latest for "
                   f"{max(recorded, key=lambda r: r.released).series_id} released "
                   f"{max(r.released for r in recorded):%Y-%m-%d}.")

# ==========================================================================================
# Freshness footer
# ==========================================================================================

st.markdown("---")
st.subheader("Data freshness")
last_run = data["last_run"]
if last_run:
    st.caption(
        f"Last refresh run {last_run['run_id']} started {_local(last_run['started']):%Y-%m-%d %H:%M:%S}, "
        f"{last_run.get('sources_ok', 0)} sources fetched, {last_run.get('sources_failed', 0)} failed."
    )
fresh = data["freshness"]
counts = fresh["status"].value_counts().to_dict()
st.caption("Status counts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
problem = fresh[fresh["status"].isin(["stale", "failed", "missing"])]
if not problem.empty:
    st.warning(f"{len(problem)} series stale, failed or missing; they are marked * in the scorecard and listed below.")
with st.expander("Every series", expanded=False):
    st.dataframe(
        fresh[["label", "source", "key", "last_obs", "age_days", "limit_days", "status", "critical", "message"]],
        width="stretch",
        hide_index=True,
    )
