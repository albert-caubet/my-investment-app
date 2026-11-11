"""Macro page: market charts, regime, scorecard, charts, releases, freshness. Reads DuckDB only.

Every figure carries its observation date. Loading the page never calls the
network: the data is whatever ``python -m invest.jobs.refresh`` last stored, and
the footer says when that was. The refresh button runs that same command in a
separate process, so fetching happens only when asked for.
"""

from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import charts
import job_panel
from invest.data.cache import Store, utcnow
from invest.data.releases import Release, append_release, load_releases
from invest.jobs.refresh import ingest_releases
from invest.macro import scorecard as sc
from invest.macro.catalog import GROUPS, load_catalog
from invest.macro import markets
from invest.macro.indicators import format_value, recession_spans
from invest.macro.regimes import thresholds_for
from invest.paths import db_path

st.title("Macro")

DB = db_path()

job_panel.follow_if_running()  # a job holds the database; nothing below may read it

if not DB.exists():
    st.info(
        f"No market database yet at `{DB}`. The page only reads what a refresh has stored, so there "
        f"is nothing to show until one has run. The first run fetches the full history of every "
        f"series in the catalog and takes several minutes. Outside the app, "
        f"`python -m invest.jobs.refresh` does the same thing, and is what the scheduler runs."
    )
    job_panel.start_button("refresh", "Fetch the data now", type="primary")
    job_panel.last_result("refresh")
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
with right:
    job_panel.start_button("refresh", "Refresh now", help="Runs `python -m invest.jobs.refresh` in the background.")
job_panel.last_result("refresh")

# ==========================================================================================
# Markets
# ==========================================================================================


@st.cache_data(ttl=600, show_spinner=False)
def _market_closes(db: str, version: float, symbols: tuple[str, ...]) -> dict[str, pd.Series]:
    """``{symbol: daily closes}`` for every symbol with prices stored; the others are absent."""
    with Store(db, read_only=True) as store:
        panel = store.read_price_panel(symbols, adjusted=False)
    closes = {s: markets.weekdays(panel[s].dropna()) for s in symbols if s in panel.columns}
    return {s: c for s, c in closes.items() if not c.empty}


def _market_chart(
    chart: markets.MarketChart, closes: dict[str, pd.Series], preset: str, height: int, per_row: int
) -> None:
    stored = {s.symbol: closes[s.symbol] for s in chart.charted if s.symbol in closes}
    if not stored:
        st.info("No prices stored for this chart yet. The next refresh downloads them.")
        _market_notes(chart)
        return
    first = min(c.index[0] for c in stored.values()).date()
    last = max(c.index[-1] for c in stored.values()).date()
    # Max is where every line has data, so they all start together at 0. From the
    # very first close, one index with decades more history would dwarf the rest.
    start = markets.common_start(stored) if preset == "Max" else charts.range_start(preset, first, last)
    weekly = (last - start).days > markets.WEEKLY_AFTER_DAYS
    late = pd.Timedelta(days=7)  # more than a week of holidays: the line starts or stops on its own date

    fig = go.Figure()
    fig.add_hline(y=0, line_width=1, line_color="rgba(128, 128, 128, 0.6)")
    # Said under the chart rather than in the legend, where every name must stay short.
    starts_late, ends_early, out_of_range = [], [], []
    for i, series in enumerate(chart.charted):  # by place in the list, so a line keeps its look if another is missing
        if series.symbol not in stored:
            continue
        change = markets.rebase(stored[series.symbol], start, last)
        if change.empty:
            out_of_range.append(f"{series.label} ({series.symbol}, last close {stored[series.symbol].index[-1]:%d %b %Y})")
            continue
        if change.index[0] - pd.Timestamp(start) > late:
            starts_late.append(f"{series.label} on {change.index[0]:%d %b %Y}")
        if pd.Timestamp(last) - change.index[-1] > late:
            ends_early.append(f"{series.label} on {change.index[-1]:%d %b %Y}")
        shown = markets.weekly(change) if weekly else change
        fig.add_scatter(
            x=shown.index, y=shown.to_numpy(), customdata=stored[series.symbol].reindex(shown.index).to_numpy(),
            name=series.legend, mode="lines", line=charts.series_line(i),
            hovertemplate=f"{series.legend}: %{{y:+.1%}} · %{{customdata:,.2f}} {series.currency}<extra></extra>",
        )
    fig.update_yaxes(tickformat="+.1~%", title_text=f"Change since {start:%d %b %Y}")
    # The legend above the plot, as on the portfolio page, so it takes no width from the
    # lines. The chart is taller by the rows the names take, so the plot keeps about the
    # height chosen however many lines it has.
    names = [trace.name for trace in fig.data]
    fig.update_layout(
        template="plotly_white", hovermode="x unified", showlegend=True, xaxis_hoverformat="%d %b %Y",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    charts.readable(fig, height=height + charts.legend_height(names, per_row))
    # After readable, which sets the app's 18px and 20px: at those sizes a dozen names take
    # a third of the chart, and the hover's list of every line overflows the plot.
    fig.update_layout(legend_font_size=charts.LEGEND_FONT, hoverlabel_font_size=charts.LEGEND_FONT)
    st.plotly_chart(fig, width="stretch")

    notes = [f"Latest close {last:%d %b %Y}" + (f"; Max starts on {start:%d %b %Y}, the first day with a close "
                                                f"for every line" if preset == "Max" else "") + "."]
    if starts_late:
        notes.append(f"Starting later, at 0% on their first close: {', '.join(starts_late)}.")
    if ends_early:
        notes.append(f"Ending earlier, at their last close: {', '.join(ends_early)}.")
    not_stored = [f"{s.label} ({s.symbol})" for s in chart.charted if s.symbol not in stored]
    if not_stored:
        notes.append(f"No prices stored for {', '.join(not_stored)}; the next refresh downloads them.")
    if out_of_range:
        notes.append(f"No close in the time range for {', '.join(out_of_range)}.")
    st.caption(" ".join(notes))
    _market_notes(chart)


def _market_notes(chart: markets.MarketChart) -> None:
    """What each line covers, which ones are proxies, and what the chart leaves out."""
    covers = [f"{s.label}: {s.about}" for s in chart.charted if s.about]
    if covers:
        st.caption("**Covers.** " + " · ".join(covers))
    proxies = [f"{s.label}: {s.symbol}, {s.proxy}." for s in chart.charted if s.proxy]
    if proxies:
        st.caption("\\* Proxies. " + " ".join(proxies))
    left_out = [f"{s.label} ({s.about}): {s.note}" if s.about else f"{s.label}: {s.note}"
                for s in chart.series if not s.symbol]
    if left_out:
        st.caption(f"Not charted: {'; '.join(left_out)}.")


EURUSD = "EURUSD"  # the catalog's series: Yahoo's EURUSD=X, stored by the refresh with the other macro series


def _eurusd_chart(preset: str, height: int) -> None:
    """The exchange rate itself, in dollars per euro, rather than a % change like the indices beside it:
    one line has nothing to be compared with, and the level (1.08, 1.20) is what a reader knows."""
    rate = _series(str(DB), _db_version(), EURUSD, "level")
    rate = markets.weekdays(rate.dropna()) if not rate.empty else rate
    if rate.empty:
        st.info("No EUR/USD rate stored yet. The next refresh downloads it.")
        return
    first, last = rate.index[0].date(), rate.index[-1].date()
    start = charts.range_start(preset, first, last)
    shown = rate[rate.index >= pd.Timestamp(start)]
    if (last - start).days > markets.WEEKLY_AFTER_DAYS:  # as the indices: weekly closes past two years
        shown = markets.weekly(shown)
    fig = go.Figure(go.Scatter(
        x=shown.index, y=shown.to_numpy(), name="EUR/USD", mode="lines", line=charts.series_line(0),
        hovertemplate="%{y:.4f} USD per EUR<extra></extra>",
    ))
    fig.update_yaxes(title_text="USD per EUR", tickformat=".2f")
    fig.update_layout(template="plotly_white", hovermode="x unified", showlegend=False, xaxis_hoverformat="%d %b %Y")
    charts.readable(fig, height=height)
    fig.update_layout(hoverlabel_font_size=charts.LEGEND_FONT)
    st.plotly_chart(fig, width="stretch", key="macro_eurusd")
    change = shown.iloc[-1] / shown.iloc[0] - 1
    st.caption(
        f"{shown.iloc[-1]:.4f} dollars per euro on {shown.index[-1]:%d %b %Y}, {change:+.1%} since "
        f"{shown.index[0]:%d %b %Y}. A rise is a stronger euro. Yahoo's EURUSD=X rate."
    )


@st.fragment  # a control reruns the market charts only, not the scorecard below
def _market_section(market_charts: tuple[markets.MarketChart, ...]) -> None:
    st.subheader("Markets")
    # Kept in the URL (?market_range=5Y&market_per_row=3&market_height=Large), as on the
    # portfolio page, so a reload or a bookmark keeps the choice. Keys of their own: the
    # portfolio page's would carry its choice over to this page, ahead of this page's URL.
    controls = st.container(horizontal=True, vertical_alignment="bottom", gap="medium")
    preset = controls.segmented_control(
        "Time range", charts.RANGES, default=charts.DEFAULT_RANGE, required=True, key="market_range",
        bind="query-params",
    )
    per_row = controls.segmented_control(
        "Charts per row", (1, 2, 3), default=2, required=True, key="market_per_row", bind="query-params"
    )
    chart_height = controls.segmented_control(
        "Chart height", tuple(charts.HEIGHTS), default="Medium", required=True, key="market_height",
        bind="query-params",
    )
    st.caption(
        "Each line is the % change from its first close in the time range, in its own currency; the hover "
        "shows the close too. Most are price indices, without dividends; the DAX family and the total-return "
        "proxies include them. Futures are Yahoo's front-month contracts, so a roll can show as a jump. Ranges "
        "over two years plot weekly closes. Click a legend entry to hide its line, double-click to show it alone. "
        "EUR/USD, first, is the exchange rate itself, in dollars per euro."
    )
    closes = _market_closes(str(DB), _db_version(), markets.all_symbols(market_charts))
    for i in range(len(market_charts) + 1):  # EUR/USD first
        if i % per_row == 0:
            cells = st.columns(per_row)  # a row per group, so neighbours line up at the top
        if i == 0:
            with cells[0].expander("💱 EUR/USD", expanded=True):
                _eurusd_chart(preset, charts.HEIGHTS[chart_height])
            continue
        chart = market_charts[i - 1]
        # The chart has no title, which half a row could not fit: this label names it.
        with cells[i % per_row].expander(f"📈 {chart.title}", expanded=True):
            _market_chart(chart, closes, preset, charts.HEIGHTS[chart_height], per_row)


try:
    market_charts = markets.load_markets()
except (OSError, ValueError) as exc:  # a MarketsError, or tomllib's TOMLDecodeError
    st.subheader("Markets")
    st.warning(f"The market charts are not shown: config/markets.toml: {exc}")
else:
    _market_section(market_charts)

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
# Scorecard and chart
# ==========================================================================================

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

#: The indicators charted, kept in the URL (?macro_chart=US_UNRATE,US_CPI) in the order they
#: were ticked, so a reload or a bookmark ticks them again and each keeps its colour.
CHART_URL = "macro_chart"
CHART_RANGE = "10Y"  # most indicators are monthly: a year is a dozen points
THRESHOLD_COLOR = "rgba(214, 39, 40, 0.8)"


def _scorecard() -> list[str]:
    """The scorecard's tables, one per group. Returns the ids of the rows ticked in them, in table order."""
    st.subheader("Scorecard")
    st.caption(
        "z-scores against the last ten years of each series (the catalog can set another window); "
        "percentiles over the whole history; the concern column is the z-score signed in the direction "
        "of concern, so red means the indicator sits where it has historically been worrying. "
        "Tick rows, in any of the tables, to chart them below."
    )
    in_url = set(charts.in_url(CHART_URL))
    ticked = []
    for group in catalog.groups():
        rows = [r for r in readings if r.group == group]
        if not rows:
            continue
        st.markdown(f"**{GROUPS.get(group, group)}**")
        table = _group_table(rows)
        if not table["Note"].astype(bool).any():
            table = table.drop(columns=["Note"])
        event = st.dataframe(
            table.style.format({"z": "{:+.2f}", "Pct": "{:.0f}", "3m chg": "{:+.2f}", "Concern": "{:+.2f}"}, na_rep="–")
            .apply(_concern_colours, subset=["Concern"]),
            column_config={c: st.column_config.Column(c, help=h) for c, h in HELP.items() if c in table.columns},
            width="stretch",
            hide_index=True,
            placeholder="–",  # a column given help shows a blank as "None", whatever the styler says
            key=f"macro_scorecard_{group}",
            on_select="rerun",
            selection_mode="multi-row",
            # The URL's ticks, on the first run only: after that the table keeps the viewer's.
            selection_default={"selection": {"rows": [n for n, r in enumerate(rows) if r.id in in_url]}},
        )
        ticked += [rows[n].id for n in event.selection.rows if n < len(rows)]
    return ticked


def _indicator(sid: str) -> pd.Series:
    """An indicator's history as the scorecard reads it: its fallback's when the scorecard used that."""
    reading = readings_by_id[sid]
    stored = reading.note.split()[-1] if reading.note.startswith("via fallback") else sid
    return _series(str(DB), _db_version(), stored, catalog[sid].transform)


def _indicator_chart(ids: list[str], slots: dict[str, int], preset: str, height: int) -> None:
    """The indicators on one figure, a panel per unit, with NBER recessions shaded and rule thresholds drawn."""
    view = charts.window_series({sid: _indicator(sid) for sid in ids}, preset)
    names = {sid: catalog[sid].label for sid in ids}
    if view.series:
        units = {sid: catalog[sid].units for sid in view.series}
        fig = charts.lines_figure(view, names, units, slots, in_pct=False, change_in_hover=False)
        first = min(s.index[0] for s in view.series.values())
        last = max(s.index[-1] for s in view.series.values())
        for start, end in data["recessions"]:
            end = pd.Timestamp(end) + pd.offsets.MonthEnd(0)
            if end >= first and pd.Timestamp(start) <= last:
                # Across every panel, which add_vrect would not do: it shades the first one only.
                fig.add_shape(type="rect", xref="x", yref="paper", x0=start, x1=end, y0=0, y1=1,
                              fillcolor="grey", opacity=0.18, line_width=0, layer="below")
        axis = {trace.name: trace.yaxis or "y" for trace in fig.data}  # the panel each line is on
        for n, sid in enumerate(view.series):
            # Every other indicator's names on the right, so two on one panel do not overprint
            # where their levels are close, as the Sahm rule's 0.5 and the curve's 0.
            side = dict(x=1, xanchor="right") if n % 2 else dict(x=0, xanchor="left")
            levels: dict[float, list[str]] = {}  # rules on one level share a line, or their names overprint
            for rule_name, level in thresholds_for(sid).items():
                levels.setdefault(level, []).append(rule_name)
            for level, rule_names in levels.items():
                fig.add_shape(type="line", xref="paper", x0=0, x1=1, yref=axis[names[sid]], y0=level, y1=level,
                              line=dict(dash="dash", color=THRESHOLD_COLOR, width=1.5))
                fig.add_annotation(xref="paper", yref=axis[names[sid]], y=level, text=", ".join(rule_names),
                                   showarrow=False, yanchor="bottom", **side)
        legend = charts.legend_height(list(names.values())) if len(view.series) > 1 else 0
        charts.readable(fig, height=height + legend)
        fig.update_layout(legend_font_size=charts.LEGEND_FONT, hoverlabel_font_size=charts.LEGEND_FONT)
        st.plotly_chart(fig, width="stretch", key=f"macro_figure_{'_'.join(ids)}")
    if view.missing:
        st.caption("Not drawn: " + "; ".join(f"{names[sid]}, {why}" for sid, why in view.missing.items()) + ".")


def _indicator_notes(sid: str, *, named: bool) -> None:
    """What the scorecard reads in an indicator, and where it comes from."""
    spec, reading = catalog[sid], readings_by_id[sid]
    st.markdown(f"**{spec.label}.** {reading.reading}" if named else reading.reading)
    shown = "; shown as the change over a year" if spec.transform == "yoy" else ""
    st.caption((f"{spec.notes} " if spec.notes else "")
               + f"Source: {spec.source} {spec.key or spec.formula or ''}{shown}; publication lag about "
               f"{spec.lag_days} days.")


def _chart_section(ticked: list[str]) -> None:
    st.subheader("Chart")
    layout = charts.ticked_layout(st.container(horizontal=True, vertical_alignment="bottom", gap="medium"),
                                  "macro", noun="indicators", default_range=CHART_RANGE)
    if not ticked:
        st.info("Tick one or more rows of the scorecard above to chart them.", icon=":material/check_box:")
        return
    st.caption("Shaded: NBER recessions. Dashed red lines: the levels at which a regime rule fires.")
    slots = {sid: n for n, sid in enumerate(ticked)}
    if layout.together:
        _indicator_chart(ticked, slots, layout.preset, layout.height)
        for sid in ticked:
            _indicator_notes(sid, named=True)
        return
    for n, sid in enumerate(ticked):
        if n % layout.per_row == 0:
            cells = st.columns(layout.per_row)  # a row per group, so neighbours line up at the top
        with cells[n % layout.per_row].expander(f"📊 {catalog[sid].label}", expanded=True):
            _indicator_chart([sid], slots, layout.preset, layout.height)
            _indicator_notes(sid, named=False)


@st.fragment  # ticking a row reruns the scorecard and the chart only
def _scorecard_and_chart() -> None:
    _chart_section(charts.keep_ticked(CHART_URL, _scorecard()))


readings_by_id = {r.id: r for r in readings}
_scorecard_and_chart()

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
