"""Custom charts: a sandbox to chart anything, alone or side by side.

A chart holds one or more series, found with the search box on it: stocks, ETFs, funds,
indices, futures, currencies and crypto by name, ticker or ISIN, from Yahoo Finance's own
search; the macro series the refresh job stores (the catalog in ``config/series.toml``),
read from the database as on the Macro page; and, with a FRED API key, any series FRED
publishes. The charts are kept in the page URL with the controls above them
(``?chart=AAPL,MSFT&chart=macro:US_CPI``), so a reload or a bookmark brings them back.

Unlike the other analysis pages, this one asks the network: Yahoo and FRED answer the
searches and send the histories, and ``market_data.py`` caches both.
"""

from __future__ import annotations

import os
import re
from collections import Counter

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import charts
import market_data as md
from invest import secrets
from invest.data import search
from invest.data.cache import Store
from invest.macro.catalog import load_catalog
from invest.macro.derived import apply_transform
from invest.paths import db_path

st.title("Custom charts")

DB = db_path()
CHARTS = "plotter_charts"  # [{"id": int, "refs": [reference, ...], "slots": {reference: colour slot}}]
NEXT_ID = "plotter_next_id"
FOUND = "plotter_found"  # {reference: search.Hit}: what a search said a series is, so it is not asked again
URL_KEY = "chart"
MAX_SERIES = len(charts.SERIES_COLORS["light"])  # a colour each

catalog = load_catalog()
secrets.reload()  # an edit to secrets.toml counts on the next rerun, without a restart
fred_search = bool(secrets.fred_api_key())

st.caption(
    "Search a chart for anything Yahoo Finance quotes, by name, ticker or ISIN: stocks, ETFs, funds, indices, "
    "futures, currencies, crypto. Or for a macro series the refresh job stores"
    + (", or any of FRED's." if fred_search else ".")
    + " Pick a result to add it. The page URL keeps the charts, so a bookmark brings them back."
)
if not fred_search:
    st.caption(
        "FRED's series are searchable too once a free FRED API key is set as `FRED_API_KEY` in "
        "`.streamlit/secrets.toml`. Without one, chart a FRED series by its id: search `fred:CPIAUCSL`."
    )


# ==========================================================================================
# The charts, kept in session state and in the URL
# ==========================================================================================


def _charts_in_url() -> list[list[str]]:
    """The charts a URL carries: a ``chart`` parameter each, its references separated by commas."""
    return [[ref.strip() for ref in value.split(",") if ref.strip()] for value in st.query_params.get_all(URL_KEY)]


def _new_chart(refs: list[str]) -> dict:
    refs = list(dict.fromkeys(refs))[:MAX_SERIES]
    chart_id = st.session_state[NEXT_ID]
    st.session_state[NEXT_ID] += 1
    return {"id": chart_id, "refs": refs, "slots": {ref: slot for slot, ref in enumerate(refs)}}


if CHARTS not in st.session_state:
    st.session_state[NEXT_ID] = 0
    st.session_state[CHARTS] = [_new_chart(refs) for refs in _charts_in_url() or [[]]]  # one empty chart to start
st.session_state.setdefault(FOUND, {})

kept = [",".join(chart["refs"]) for chart in st.session_state[CHARTS]]
if kept:
    st.query_params[URL_KEY] = kept
elif URL_KEY in st.query_params:
    del st.query_params[URL_KEY]


def _chart(chart_id: int) -> dict | None:
    return next((c for c in st.session_state[CHARTS] if c["id"] == chart_id), None)


def _add_chart() -> None:
    st.session_state[CHARTS].append(_new_chart([]))


def _remove_chart(chart_id: int) -> None:
    st.session_state[CHARTS] = [c for c in st.session_state[CHARTS] if c["id"] != chart_id]


def _add_series(chart_id: int, hit: search.Hit) -> None:
    chart = _chart(chart_id)
    if chart is None:
        return
    if hit.ref not in chart["refs"] and len(chart["refs"]) < MAX_SERIES:
        # The first free colour, never the next by position: a series keeps its colour
        # when another is taken off the chart.
        chart["slots"][hit.ref] = min(set(range(MAX_SERIES)) - set(chart["slots"].values()))
        chart["refs"].append(hit.ref)
    st.session_state[FOUND][hit.ref] = hit
    st.session_state[f"plotter_query_{chart_id}"] = ""  # this search is done


def _keep_series(chart_id: int) -> None:
    """The series left on a chart once one is taken off it."""
    chart = _chart(chart_id)
    if chart is None:
        return
    chart["refs"] = [ref for ref in st.session_state[f"plotter_series_{chart_id}"] if ref in chart["refs"]]
    chart["slots"] = {ref: slot for ref, slot in chart["slots"].items() if ref in chart["refs"]}


# ==========================================================================================
# Search
# ==========================================================================================


def _reason(exc: Exception) -> str:
    """An error as a reader wants it: the cause, without the URL a request error starts with."""
    return re.sub(r"https?://\S+", "", str(exc)).strip(" :") or type(exc).__name__


def _plain(text: str) -> str:
    """``text`` as it is, in a label Streamlit reads as Markdown: a "$" would open a formula."""
    return re.sub(r"([\\`*_{}\[\]<>#+\-!|~$])", r"\\\1", text)


def _hit_label(hit: search.Hit) -> str:
    """The name, then in grey its symbol or id, what it is and where: the name is what a reader scans for."""
    code = search.parse_ref(hit.ref)[1]
    rest = ([code] if code != hit.name else []) + [", ".join(p for p in (hit.kind, hit.detail) if p)]
    return f"{_plain(hit.name)} :gray[{_plain(' · '.join(rest))}]"


def _results(chart_id: int, query: str, refs: list[str]) -> None:
    """What a search finds, each a button that adds it to the chart, ticked off when it is on it already."""
    if len(query) < 2:
        st.caption("Type at least two characters.")
        return
    notes = []
    typed = search.typed_hit(query, catalog)
    if typed:
        groups = [[typed]]
    else:
        groups = [search.catalog_hits(query, catalog)]
        try:
            groups.append(search.yahoo_hits(md.search_yahoo(query)))
        except Exception as exc:
            notes.append(f"Yahoo Finance did not answer: {_reason(exc)}")
        if fred_search:
            try:
                groups.append(search.fred_hits(md.search_fred(query)))
            except Exception as exc:
                notes.append(f"FRED did not answer: {_reason(exc)}")
    hits = search.rank(query, groups)
    if not hits and not notes:
        st.caption(f"Nothing found for “{query}”.")
    found = st.container(gap=None)  # a list, not a column of separate buttons
    for n, hit in enumerate(hits):
        shown = hit.ref in refs
        found.button(_hit_label(hit), key=f"plotter_hit_{chart_id}_{n}", on_click=_add_series, args=(chart_id, hit),
                     type="tertiary", icon=":material/check:" if shown else ":material/add:", disabled=shown)
    for note in notes:
        st.caption(note)


# ==========================================================================================
# Series
# ==========================================================================================


def _db_version() -> float:
    """Cache key: the file's modification time, so a refresh invalidates what was read."""
    try:
        return os.path.getmtime(DB)
    except OSError:
        return 0.0


@st.cache_data(ttl=600, show_spinner=False)
def _macro_series(db: str, version: float, series_id: str) -> pd.Series:
    """A catalog series as the Macro page charts it: stored, and transformed the way the scorecard reads it."""
    spec = catalog[series_id]
    with Store(db, read_only=True) as store:
        raw = store.read_series(series_id)
        if raw.empty and spec.fallback:
            raw = store.read_series(spec.fallback)
    if raw.empty:
        raise LookupError("not stored yet; the refresh on the Macro page fetches it")
    return apply_transform(raw, spec.transform)


# A Yahoo or FRED request that fails is kept as failed for five minutes, rather than asked
# again on every rerun of the page while the series sits on a chart. What does arrive is
# kept for hours, by market_data.


@st.cache_data(ttl=300, show_spinner=False)
def _online_series(ref: str) -> pd.Series | str:
    """The history of a Yahoo or FRED series, or why there is none."""
    source, key = search.parse_ref(ref)
    try:
        return md.fetch_fred_series(key) if source == "fred" else md.fetch_close_history(key)
    except Exception as exc:
        return _reason(exc)


@st.cache_data(ttl=300, show_spinner=False)
def _online_about(ref: str) -> tuple[str, str] | None:
    """Name and unit of a Yahoo or FRED series as its source gives them; None when it does not."""
    source, key = search.parse_ref(ref)
    try:
        if source == "fred":
            meta = md.fetch_fred_meta(key)
            return meta["title"], meta["units"]
        meta = md.fetch_symbol_meta(key)
        return meta["name"], meta["currency"] or ""
    except Exception:  # no FRED key, or the source did not answer
        return None


def _load(ref: str) -> pd.Series:
    """The whole history of one series. Raises with a reason a reader can act on when there is none."""
    source, key = search.parse_ref(ref)
    if source != "macro":
        got = _online_series(ref)
        if isinstance(got, str):
            raise LookupError(got)
        return got
    if key not in catalog.by_id:
        raise LookupError("not in the catalog, config/series.toml")
    if not DB.exists():
        raise LookupError("no market database yet; the refresh on the Macro page builds it")
    try:
        return _macro_series(str(DB), _db_version(), key)
    except duckdb.Error as exc:
        raise LookupError(f"the market database could not be read; if a job is writing it, wait for it ({exc})")


def _describe(ref: str) -> tuple[str, str]:
    """``(name, unit)``: what the legend calls a series, and what its values are in."""
    source, key = search.parse_ref(ref)
    if source == "macro":
        spec = catalog.by_id.get(key)
        return (spec.label, spec.units) if spec else (key, "")
    found = st.session_state[FOUND].get(ref)
    if source == "fred" and found:  # FRED's search said it all; Yahoo's search gives no currency
        return found.name, found.unit
    return _online_about(ref) or ((found.name, found.unit) if found else (key, ""))


# ==========================================================================================
# Drawing
# ==========================================================================================


#: Characters a legend entry has room for, by charts per row, in a window about 1280 pixels
#: wide. Plotly does not wrap one, and the page cannot know the window's width.
LEGEND_ROOM = {1: 70, 2: 32, 3: 22}


def _labels(about: dict[str, tuple[str, str]]) -> dict[str, str]:
    """What the chips and the legend call each series: its name, with its symbol or id when two names are alike."""
    names = Counter(name for name, _ in about.values())
    return {ref: f"{name} ({search.parse_ref(ref)[1]})" if names[name] > 1 else name for ref, (name, _) in about.items()}


def _fit(label: str, ref: str, room: int) -> str:
    """``label`` cut to ``room`` characters. A cut one ends with its symbol or id, so two cut alike still differ."""
    if len(label) <= room:
        return label
    code = search.parse_ref(ref)[1]
    return f"{label[:max(room - len(code) - 4, 8)].rstrip()}… ({code})"


def _figure(view: charts.SeriesWindow, about: dict[str, tuple[str, str]], slots: dict[str, int],
            in_pct: bool, room: int) -> go.Figure:
    """One line per series. In % change they share one axis; in value, each unit gets a panel.

    Panels stack on one time axis, so their dates line up, rather than a second scale on
    the right: two scales on one plot line up wherever their ranges happen to put them,
    which shows a relationship the data does not have.
    """
    units = [None] if in_pct else list(dict.fromkeys(about[ref][1] for ref in view.series))
    labels = _labels(about)
    gap = 0.08
    tall = (1 - gap * (len(units) - 1)) / len(units)
    fig = go.Figure()
    for n, unit in enumerate(units):  # top to bottom
        top = 1 - n * (tall + gap)
        title = f"Change since {view.start:%d %b %Y}" if in_pct else unit or None
        fig.update_layout({f"yaxis{n + 1 if n else ''}": dict(domain=[max(top - tall, 0.0), top], anchor="x",
                                                                title=title)})
    for ref, values in view.series.items():
        name, unit = _fit(labels[ref], ref, room), about[ref][1]
        base = float(values.iloc[0])
        change = values / base - 1 if base > 0 else pd.Series(np.nan, index=values.index)
        panel = 0 if in_pct else units.index(unit)
        hover = f"%{{customdata[0]:{',.2f' if values.abs().max() >= 1 else ',.4f'}}}" + (f" {unit}" if unit else "")
        if base > 0:
            hover += " · %{customdata[1]:+.1%}"
        line = charts.series_line(slots[ref])
        fig.add_scatter(
            x=values.index, y=change if in_pct else values, name=name, yaxis=f"y{panel + 1 if panel else ''}",
            mode="lines" if len(values) > 1 else "markers",
            line=line, marker=dict(color=line["color"], size=8),
            customdata=np.column_stack([values.to_numpy(), change.to_numpy()]),
            hovertemplate=hover + "<extra></extra>",
        )
    if in_pct:
        fig.update_yaxes(tickformat="+.1~%")  # +12%, -0.5%: no trailing zeros
    fig.update_layout(
        template="plotly_white", hovermode="x unified", hoversubplots="axis",
        xaxis=dict(anchor=f"y{len(units) if len(units) > 1 else ''}", hoverformat="%d %b %Y"),
        # A legend only where it tells lines apart; the chips above the chart name a single one.
        # In a row above the plot, so it takes no width from a chart in a narrow column.
        showlegend=len(view.series) > 1,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    return fig


def _draw(chart: dict, about: dict[str, tuple[str, str]], preset: str, in_pct: bool, height: int, room: int) -> None:
    loaded, failed = {}, {}
    for ref in chart["refs"]:
        try:
            loaded[ref] = _load(ref)
        except Exception as exc:
            failed[ref] = _reason(exc)
    view = charts.window_series(loaded, preset, rebase=in_pct)
    if view.series:
        fig = _figure(view, about, chart["slots"], in_pct, room)
        st.plotly_chart(charts.readable(fig, height=height), width="stretch", key=f"plotter_figure_{chart['id']}")
    left_out = failed | view.missing
    if left_out:
        labels = _labels(about)
        st.caption("Not drawn: " + "; ".join(f"{labels[ref]}, {why}" for ref, why in left_out.items()) + ".")
    if view.common_start:
        st.caption(f"Every line starts on {view.start:%d %b %Y}, the first day in the range that all of them have data.")


def _card(chart: dict, preset: str, in_pct: bool, height: int, room: int) -> None:
    chart_id, refs = chart["id"], chart["refs"]
    with st.container(border=True):
        head = st.container(horizontal=True, vertical_alignment="center", gap="small")
        query = head.text_input("Add a series", key=f"plotter_query_{chart_id}", label_visibility="collapsed",
                                placeholder="Add a series: name, ticker, ISIN or indicator", icon=":material/search:")
        head.button("", icon=":material/close:", key=f"plotter_remove_{chart_id}", help="Remove this chart",
                    on_click=_remove_chart, args=(chart_id,), type="tertiary")
        if query.strip():
            if len(refs) < MAX_SERIES:
                _results(chart_id, query.strip(), refs)
            else:
                st.caption(f"A chart holds {MAX_SERIES} series, a colour each. Add another chart for more.")
        if not refs:
            st.caption("Search for a stock, fund, ETF, index or macro series, then pick it to chart it.")
            return
        about = {ref: _describe(ref) for ref in refs}
        labels = _labels(about)
        # The chart's list is the truth: set before the widget and never as its default, so a
        # series added by a search shows here, and taking one off removes it from the chart.
        st.session_state[f"plotter_series_{chart_id}"] = list(refs)
        st.multiselect("Series on this chart", refs, key=f"plotter_series_{chart_id}", label_visibility="collapsed",
                       format_func=labels.get, on_change=_keep_series, args=(chart_id,))
        _draw(chart, about, preset, in_pct, height, room)


# ==========================================================================================
# Page
# ==========================================================================================

controls = st.container(horizontal=True, vertical_alignment="bottom", gap="medium")
preset = controls.segmented_control(
    "Time range", charts.RANGES, default=charts.DEFAULT_RANGE, required=True, key="time_range", bind="query-params"
)
per_row, height, in_pct = charts.grid_controls(
    controls, value="Value",
    change_help="Change: every series in % from the first day of the time range that a chart's series all have data.",
)
if in_pct:
    st.caption(
        "% change from the first day in the time range on which every series of the chart has data, so the lines "
        "start together. Yahoo closes are unadjusted: dividends paid out are not counted in it."
    )

cards = st.session_state[CHARTS]
for n in range(len(cards) + 1):  # one cell more, for the button that adds a chart
    if n % per_row == 0:
        cells = st.columns(per_row)  # a row per group, so neighbours line up at the top
    with cells[n % per_row]:
        if n < len(cards):
            _card(cards[n], preset, in_pct, height, LEGEND_ROOM[per_row])
        else:
            st.button("Add chart", icon=":material/add:", key="plotter_add_chart", on_click=_add_chart,
                      width="stretch")
