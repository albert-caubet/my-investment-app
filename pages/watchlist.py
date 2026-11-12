"""Watchlist page: the stocks put on the watchlist from the Screener or the Stock explorer.

The list comes first, then the dashboard's chart controls, a chart of every stock together
and a chart of each. The list is the market database's ``watchlist`` table, the one the
weekly report reads; a company in the latest screener table of either market, US or
European, shows that table's value and margin of safety too. The prices are Yahoo
Finance's daily closes, fetched and cached by
``market_data.py`` as on the Stock explorer, so a stock from any market Yahoo quotes can be
on it. The close button on a stock's chart takes it off the watchlist.
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

import charts
import job_panel
import market_data as md
import watchlist
from invest.data import search
from invest.data.cache import Store
from invest.fundamentals.universe import REGIONS, screener_kind
from invest.paths import db_path

st.title("Watchlist")

job_panel.follow_if_running()  # a job holds the database; nothing below may read it

DB = db_path()

try:
    entries = watchlist.entries()
except Exception as exc:
    st.warning(f"Could not read the watchlist from the market database: {exc}. If a job is running, wait for it "
               f"and reload.")
    st.stop()

if entries.empty:
    st.info("The watchlist is empty. Put a stock on it from the company detail on the Screener, or from a chart on "
            "the Stock explorer.")
    st.page_link("pages/screener.py", label="Open the Screener", icon="🔎")
    st.page_link("pages/plotter.py", label="Open the Stock explorer", icon="📈")
    st.stop()

# ==========================================================================================
# Prices, names and the screener's figures
# ==========================================================================================

# A Yahoo request that fails is kept as failed for five minutes, rather than asked again on
# every rerun of the page. What does arrive is kept for hours, by market_data.


@st.cache_data(ttl=300, show_spinner=False)
def _closes(symbol: str) -> pd.Series | str:
    """Every daily close Yahoo has for ``symbol``, or why there is none."""
    try:
        return md.fetch_close_history(symbol)
    except Exception as exc:
        return md.reason(exc)


@st.cache_data(ttl=300, show_spinner=False)
def _yahoo_about(symbol: str) -> tuple[str, str] | None:
    """Name and currency of ``symbol`` as Yahoo gives them; None when it does not answer."""
    try:
        meta = md.fetch_symbol_meta(symbol)
    except Exception:
        return None
    return meta["name"], meta["currency"] or ""


def _db_version() -> float:
    """Cache key: the file's modification time, so a new screener table shows on the next run."""
    try:
        return os.path.getmtime(DB)
    except OSError:
        return 0.0


SCREENER_COLUMNS = ["ticker", "name", "value_conservative", "mos_conservative", "f_score"]


@st.cache_data(ttl=600, show_spinner=False)
def _screener(db: str, version: float) -> tuple[str, pd.DataFrame] | None:
    """The day the latest screener table of each market was built, and their rows by ticker together;
    None before the first build. A European symbol (``SAP.DE``) never names a US company (``SAP``)."""
    tables, built = [], []
    with Store(db, read_only=True) as store:
        for region in REGIONS:
            snap = store.load_snapshot(screener_kind(region))
            rows = pd.DataFrame(snap[2].get("rows", [])) if snap else pd.DataFrame()
            if "ticker" in rows:
                tables.append(rows.reindex(columns=SCREENER_COLUMNS))
                built.append((region, f"{pd.Timestamp(snap[1]):%Y-%m-%d}"))
    if not tables:
        return None
    rows = pd.concat(tables, ignore_index=True).drop_duplicates("ticker").set_index("ticker")
    when = built[0][1] if len(built) == 1 else " and ".join(f"{day} ({region})" for region, day in built)
    return when, rows


symbols = entries["symbol"].tolist()
with st.spinner("Fetching the prices from Yahoo Finance…"):
    closes = {symbol: _closes(symbol) for symbol in symbols}
    yahoo_about = {symbol: _yahoo_about(symbol) for symbol in symbols}

try:
    screener = _screener(str(DB), _db_version())
except Exception:  # the file is held by a job: the list shows without the screener's figures
    screener = None
built, screened = screener or (None, pd.DataFrame(columns=SCREENER_COLUMNS[1:]))
covered = [symbol for symbol in symbols if symbol in screened.index]


def _describe(symbol: str) -> tuple[str, str]:
    """``(name, currency)``: Yahoo's, else the screener table's name and no currency, else the symbol alone."""
    if yahoo_about[symbol]:
        return yahoo_about[symbol]
    name = screened.at[symbol, "name"] if symbol in covered else None
    return (name if isinstance(name, str) and name else symbol), ""


about = {symbol: _describe(symbol) for symbol in symbols}
labels = search.series_labels(about)
units = {symbol: unit for symbol, (_, unit) in about.items()}
slots = {symbol: n for n, symbol in enumerate(symbols)}  # a stock's colour, the same on every chart of the page


def _take_off(symbol: str, label: str) -> None:
    try:
        watchlist.remove(symbol)
    except Exception as exc:
        st.toast(f"Could not change the watchlist: {md.reason(exc)}", icon=":material/error:")
    else:
        st.toast(f"{label} is off the watchlist.", icon=":material/star_border:")


# ==========================================================================================
# The list, above the chart controls whose time range it uses
# ==========================================================================================

listing = st.container()

st.subheader("Price history")
# All kept in the URL, as on the dashboard (?time_range=5Y&charts_per_row=3&chart_height=Large&y_axis=Change),
# so a reload or a bookmark keeps the choice.
controls = st.container(horizontal=True, vertical_alignment="bottom", gap="medium")
preset = controls.segmented_control(
    "Time range", charts.RANGES, default=charts.DEFAULT_RANGE, required=True, key="time_range", bind="query-params"
)
per_row, height, in_pct = charts.grid_controls(
    controls, value="Price",
    change_help="Change: each stock in % from its first close in the time range. On the chart of every stock, from "
                "the first day in the range that all of them have data.",
)
if in_pct:
    st.caption(
        "% change from each chart's first close in the time range; the chart of every stock starts them together, on "
        "the first day in the range that all of them have data. The closes are unadjusted, so dividends paid out are "
        "not counted in it."
    )

change_column = f"Change ({preset})"
column_help = {
    "Last close": "Yahoo Finance's latest daily close, as quoted on the day: not adjusted for dividends.",
    "Day": "The last close against the one before it.",
    change_column: "From the first close in the time range chosen above the charts to the last close.",
    "Value / share": f"The conservative intrinsic value per share (the lower of EPV and DCF) in the screener table "
                     f"built {built}, in the currency of the company's accounts.",
    "Margin of safety": f"How far below that value the price was when the screener table was built, {built}.",
    "F-score": "The Piotroski score out of 9, in the same screener table.",
    "Note": "Where the stock was put on the watchlist from, and when.",
    "Added": "The day it was put on the watchlist.",
}


def _row(entry) -> dict:
    name, currency = about[entry.symbol]
    row = {"Symbol": entry.symbol, "Name": name, "Last close": None, "Ccy": currency or None, "As of": None,
           "Day": None, change_column: None}
    got = closes[entry.symbol]
    if isinstance(got, pd.Series) and not got.empty:
        row["Last close"], row["As of"] = float(got.iloc[-1]), got.index[-1].date()
        if len(got) > 1 and got.iloc[-2] > 0:
            row["Day"] = got.iloc[-1] / got.iloc[-2] - 1
        shown = charts.window_series({entry.symbol: got}, preset).series.get(entry.symbol)
        if shown is not None and len(shown) > 1 and shown.iloc[0] > 0:
            row[change_column] = shown.iloc[-1] / shown.iloc[0] - 1
    if covered:  # the screener's columns, where any stock on the list is in its table
        screen = screened.loc[entry.symbol] if entry.symbol in covered else {}
        row["Value / share"] = screen.get("value_conservative")
        row["Margin of safety"] = screen.get("mos_conservative")
        row["F-score"] = screen.get("f_score")
    row["Note"] = entry.note or None
    row["Added"] = pd.Timestamp(entry.added_at).date()
    return row


with listing:
    table = pd.DataFrame([_row(entry) for entry in entries.itertuples(index=False)])
    formats = {"Last close": "{:,.2f}", "Day": "{:+.1%}", change_column: "{:+.1%}", "Value / share": "{:,.2f}",
               "Margin of safety": "{:+.0%}", "F-score": "{:.0f}"}
    st.dataframe(
        table.style.format({c: f for c, f in formats.items() if c in table}, na_rep="–"),
        column_config={c: st.column_config.Column(c, help=text) for c, text in column_help.items() if c in table},
        width="stretch", hide_index=True,
    )
    caption = f"{len(symbols)} stock(s), first added first. Prices from Yahoo Finance."
    if covered:
        caption += (f" Value, margin of safety and F-score from the screener table built {built}, for the companies "
                    f"in it.")
    st.caption(caption)

# ==========================================================================================
# Every stock together, then each on its own
# ==========================================================================================

loaded = {symbol: got for symbol, got in closes.items() if isinstance(got, pd.Series)}
failed = {symbol: got for symbol, got in closes.items() if isinstance(got, str)}

with st.container(border=True):
    st.markdown("**Every stock on the watchlist**")
    view = charts.window_series(loaded, preset, rebase=in_pct)
    if view.series:
        names = {symbol: search.fit_label(labels[symbol], symbol, charts.LEGEND_ROOM[1]) for symbol in view.series}
        fig = charts.lines_figure(view, names, units, slots, in_pct)
        st.plotly_chart(charts.readable(fig, height=height), width="stretch", key="watchlist_all")
    left_out = failed | view.missing
    if left_out:
        st.caption("Not drawn: " + "; ".join(f"{labels[s]}, {why}" for s, why in left_out.items()) + ".")
    if view.common_start:
        st.caption(f"Every line starts on {view.start:%d %b %Y}, the first day in the range that all of them have data.")

for i, symbol in enumerate(symbols):
    if i % per_row == 0:
        cells = st.columns(per_row)  # a row per group, so neighbours line up at the top
    with cells[i % per_row], st.container(border=True):
        name = about[symbol][0]
        head = st.container(horizontal=True, vertical_alignment="center", gap="small")
        head.markdown(f"**{search.plain(name)}** ({search.plain(symbol)})" if name != symbol
                      else f"**{search.plain(symbol)}**", width="stretch")  # the button to the far right
        head.button("", icon=":material/close:", key=f"watchlist_remove_{symbol}", help="Take it off the watchlist",
                    on_click=_take_off, args=(symbol, labels[symbol]), type="tertiary")
        got = closes[symbol]
        view = charts.window_series({symbol: got} if isinstance(got, pd.Series) else {}, preset, rebase=in_pct)
        if not view.series:
            st.caption(f"Not drawn: {got if isinstance(got, str) else view.missing[symbol]}.")
            continue
        fig = charts.lines_figure(view, {symbol: labels[symbol]}, units, slots, in_pct)
        st.plotly_chart(charts.readable(fig, height=height), width="stretch", key=f"watchlist_chart_{symbol}")

st.caption(
    "Daily closes from Yahoo Finance, unadjusted: as quoted on the day, dividends paid out not added back. A stock keeps "
    "its colour on every chart. The close button on a chart takes its stock off the watchlist; put one on from the "
    "company detail on the Screener or from a chart on the Stock explorer."
)
