"""Screener page: ranked companies from the stored metrics table, charts of those ticked, and the detail of one.

A selector at the top picks the market: the US table, built from SEC EDGAR filings
(point-in-time), or the European one, built from Yahoo Finance (not point-in-time).
Each is the latest snapshot of its region written by ``python -m invest.jobs.fundamentals``,
read with the facts behind each company. Every row shows its inputs; the footer says
which universe, as of when, and whether the data is point-in-time. The build
button runs that same command in a separate process, so fetching happens only
when asked for. The company in the detail can be put on the watchlist, which the
Watchlist page shows. The detail is ``company_detail``'s, which the Company financials
page shows of any company.
"""

from __future__ import annotations

import os
import zlib

import pandas as pd
import streamlit as st

import charts
import company_detail
import job_panel
import screener_help
from invest import secrets
from invest.data.cache import Store
from invest.data.edgar import SEC_USER_AGENT_HELP, user_agent_ok
from invest.fundamentals import screens
from invest.fundamentals.universe import SURVIVORSHIP_NOTE, load_universe, screener_kind
from invest.jobs.fundamentals import COMPARE_CURRENCY, DEFAULT_UNIVERSE, DEFAULT_UNIVERSES
from invest.paths import db_path

st.title("Value screener")

job_panel.follow_if_running()  # a job holds the database; nothing below may read it

DB = db_path()
# Kept in the URL (?screener_market=EU), so a reload or a bookmark opens the same market.
region = st.segmented_control("Market", list(DEFAULT_UNIVERSES), default="US", required=True, key="screener_market",
                              bind="query-params", help=screener_help.MARKET_HELP)
UNIVERSE = load_universe()[DEFAULT_UNIVERSES[region]]
FROM_EDGAR = UNIVERSE.source == "edgar"
#: The build of this market's universe; the command line builds the US one without being told.
JOB_ARGS = () if UNIVERSE.name == DEFAULT_UNIVERSE else ("--universe", UNIVERSE.name)
COMMAND = " ".join(("python -m invest.jobs.fundamentals",) + JOB_ARGS)
FETCHES = ("the filings of every company in the universe from SEC EDGAR" if FROM_EDGAR
           else "the accounts of every company in the universe from Yahoo Finance")
TAKES = "several minutes" if FROM_EDGAR else "about a quarter of an hour"
BUILD_HELP = f"Runs `{COMMAND}` in the background: {FETCHES}, then their prices. It takes {TAKES}."
WHAT_BUILDING_DOES = (
    f"Building it fetches {FETCHES}, then their prices, and takes {TAKES}. Outside the app, "
    f"`{COMMAND}` does the same thing."
)
#: The market cap column: one currency for a market whose accounts are in many (euros for Europe).
CAP_CURRENCY = COMPARE_CURRENCY.get(region)
CAP_COLUMN = f"market_cap_{CAP_CURRENCY.lower()}" if CAP_CURRENCY else "market_cap"
CAP_LABEL = f"Mkt cap (bn {CAP_CURRENCY})" if CAP_CURRENCY else "Mkt cap (bn)"


def _edgar_ready() -> bool:
    secrets.reload()  # an edit to secrets.toml counts on the next rerun, without a restart
    return user_agent_ok(secrets.sec_user_agent())


def _ready() -> bool:
    """Whether the build can run: EDGAR answers only a request naming a contact; Yahoo needs nothing."""
    return _edgar_ready() if FROM_EDGAR else True


def _offer_build(message: str) -> None:
    """Nothing to show yet: say why, offer the build, and stop the page."""
    ready = _ready()
    st.info(message)
    if not ready:
        st.warning(f"The build needs one setting first.\n\n{SEC_USER_AGENT_HELP}\n\nThen reload this page.")
    job_panel.start_button("fundamentals", "Build the screener table", job_args=JOB_ARGS, type="primary",
                           disabled=not ready, help=BUILD_HELP)
    job_panel.last_result("fundamentals")
    st.stop()


if not DB.exists():
    _offer_build(f"No market database yet, so no screener table. {WHAT_BUILDING_DOES}")


def _version() -> float:
    try:
        return os.path.getmtime(DB)
    except OSError:
        return 0.0


@st.cache_data(ttl=600, show_spinner="Reading the screener table…")
def _load(db: str, version: float, kind: str) -> dict | None:
    """The latest table of one market (its snapshot ``kind``) and the rows of the one before."""
    with Store(db, read_only=True) as store:
        snap = store.load_snapshot(kind)
        if snap is None:
            return None
        run_id, created_at, payload = snap
        previous = store.load_snapshot(kind, offset=1)
    return {
        "run_id": run_id,
        "created_at": created_at,
        "payload": payload,
        "previous_rows": previous[2].get("rows", []) if previous else None,
    }


try:
    data = _load(str(DB), _version(), screener_kind(region))
except Exception as exc:
    st.warning(f"Could not read the market database: {exc}. If a job is running, wait for it and reload.")
    st.stop()

if data is None:
    _offer_build(f"No screener table stored yet. {WHAT_BUILDING_DOES}")

payload = data["payload"]
table = pd.DataFrame(payload["rows"])
if table.empty:
    _offer_build(f"The stored screener table is empty. {WHAT_BUILDING_DOES}")

left, right = st.columns([5, 1], vertical_alignment="center")
left.caption(f"Table built {pd.Timestamp(data['created_at']):%Y-%m-%d} for {payload.get('universe_label')}; "
             f"details at the bottom of the page.")
if not payload.get("point_in_time", True):
    left.caption(screener_help.NOT_POINT_IN_TIME)
ready = _ready()
with right:
    job_panel.start_button("fundamentals", "Rebuild now", job_args=JOB_ARGS, disabled=not ready,
                           help=BUILD_HELP if ready else f"Needs one setting first.\n\n{SEC_USER_AGENT_HELP}")
job_panel.last_result("fundamentals")

# ==========================================================================================
# Controls
# ==========================================================================================

c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
screen_labels = {label: name for name, (label, _) in screens.SCREENS.items()}
screen_label = c1.selectbox("Screen", list(screen_labels), help=screener_help.SCREEN_HELP)
screen = screen_labels[screen_label]
min_cap_bn = c2.number_input("Min market cap (bn)", min_value=0.0, value=0.0, step=1.0, help=screener_help.MIN_CAP_HELP)
exclude_fin = c3.checkbox("Exclude financials", value=True, help=screener_help.EXCLUDE_FINANCIALS_HELP)
filter_traps = c4.checkbox("Filter value traps", value=True, help=screener_help.VALUE_TRAP_HELP)
sector_neutral = st.checkbox("Sector-neutral z-scores (quality-value only)", value=False,
                             help=screener_help.SECTOR_NEUTRAL_HELP) if screen == "quality_value" else False

options = {}
if screen in ("magic_formula", "quality_value"):
    options["exclude_financials"] = exclude_fin
if screen == "magic_formula" and min_cap_bn > 0:
    options["min_market_cap"] = min_cap_bn * 1e9
    options["cap_column"] = CAP_COLUMN
if screen == "quality_value":
    options["sector_neutral"] = sector_neutral

ranked, excluded = screens.run_screen(screen, table, filter_traps=filter_traps, **options)
if screen != "magic_formula" and min_cap_bn > 0 and CAP_COLUMN in ranked.columns:
    ranked = ranked[pd.to_numeric(ranked[CAP_COLUMN], errors="coerce") >= min_cap_bn * 1e9]

# ==========================================================================================
# Ranked list and charts
# ==========================================================================================

company_names = dict(zip(ranked["ticker"], ranked["name"]))


def _company_label(ticker: str) -> str:
    name = company_names.get(ticker)
    return f"{name} ({ticker})" if pd.notna(name) and name else ticker


SHOW = [
    ("rank", "Rank", "{:.0f}"),
    ("ticker", "Ticker", None),
    ("name", "Name", None),
    ("sector", "Sector", None),
    (CAP_COLUMN, CAP_LABEL, "{:,.1f}"),
    *company_detail.MEASURES,  # EBIT/EV to shareholder yield, as the Company financials page has them too
    ("mos_conservative", "Margin of safety", "{:+.0%}"),
    ("magic_rank", "Rank sum", "{:.0f}"),
    ("composite", "Composite z", "{:+.2f}"),
    ("period_end", "Data to", None),
    ("basis", "Basis", None),
]
#: The companies charted, kept in the URL (?screener_chart=MMM,SAP) in the order they were
#: ticked, so a reload or a bookmark ticks them again and each keeps its colour.
CHART_URL = "screener_chart"


def _ranked_table() -> list[str]:
    """The ranked list, a checkbox on each row. Returns the tickers ticked in it, in rank order."""
    st.subheader(f"{screen_label}: {len(ranked)} companies")
    view = ranked.copy()
    if CAP_COLUMN in view.columns:
        view[CAP_COLUMN] = pd.to_numeric(view[CAP_COLUMN], errors="coerce") / 1e9
    columns = [c for c, _, _ in SHOW if c in view.columns]
    formats = {c: f for c, _, f in SHOW if c in view.columns and f}
    labels = {c: label for c, label, _ in SHOW}
    styled = view[columns].rename(columns=labels).style.format({labels[c]: f for c, f in formats.items()}, na_rep="–")
    tickers = ranked["ticker"].tolist()
    in_url = set(charts.in_url(CHART_URL))
    shown_labels = [labels[c] for c in columns]
    event = st.dataframe(
        styled, width="stretch", hide_index=True, height=min(600, 40 + 35 * len(view)),
        # Hovering a measure's header, from EBIT/EV on, says what it is, how to read it and
        # where it misleads.
        column_config={label: st.column_config.Column(label, help=text)
                       for label, text in screener_help.COLUMN_HELP.items() if label in shown_labels},
        placeholder="–",  # a column given help shows a blank as "None", whatever the styler says
        # A key per list: a tick is a row's place in it, which another screen or filter
        # gives to another company. A new list starts from the URL's tickers instead.
        key=f"screener_list_{zlib.crc32(','.join(tickers).encode())}",
        on_select="rerun", selection_mode="multi-row",
        selection_default={"selection": {"rows": [n for n, t in enumerate(tickers) if t in in_url]}},
    )
    st.caption(
        "EBIT/EV and ROIC are Greenblatt's earnings yield and return on capital; accruals is (net income minus "
        "operating cash flow) over average assets; F is the Piotroski score out of 9; the margin of safety is against "
        "the lower of the earnings power value and a conservative DCF. TTM figures combine the last fiscal year with "
        "the year-to-date filings; the Basis column says which. Tick companies to chart their prices below."
    )

    if data["previous_rows"]:
        prev_table = pd.DataFrame(data["previous_rows"])
        try:
            prev_ranked, _ = screens.run_screen(screen, prev_table, filter_traps=filter_traps, **options)
            entrants, dropouts = screens.entrants_and_dropouts(tickers, prev_ranked["ticker"].tolist(), top_n=20)
            if entrants or dropouts:
                st.caption(f"Top 20 since the previous table: entered {', '.join(entrants) or 'none'}; "
                           f"left {', '.join(dropouts) or 'none'}.")
        except Exception:
            pass

    if not excluded.empty:
        with st.expander(f"Excluded as possible value traps ({len(excluded)})"):
            st.dataframe(excluded[["ticker", "name", "reason"]], width="stretch", hide_index=True)
    return [tickers[n] for n in event.selection.rows if n < len(tickers)]


def _price_chart(tickers: list[str], slots: dict[str, int], layout: charts.TickedLayout) -> None:
    """The companies' closes on one figure: a panel per currency, or one axis in % change."""
    loaded, currencies = {}, {}
    for ticker in tickers:
        loaded[ticker], currency = company_detail.stored_prices(str(DB), _version(), ticker)
        currencies[ticker] = currency or ""
    view = charts.window_series(loaded, layout.preset, rebase=layout.in_pct)
    names = {ticker: _company_label(ticker) for ticker in tickers}
    if view.series:
        fig = charts.lines_figure(view, names, currencies, slots, layout.in_pct)
        legend = charts.legend_height(list(names.values())) if len(view.series) > 1 else 0
        charts.readable(fig, height=layout.height + legend)
        fig.update_layout(legend_font_size=charts.LEGEND_FONT, hoverlabel_font_size=charts.LEGEND_FONT)
        st.plotly_chart(fig, width="stretch", key=f"screener_figure_{'_'.join(tickers)}")
    if len(tickers) == 1 and view.series:
        (shown,) = view.series.values()
        if len(shown) > 1:
            st.caption(f"{shown.iloc[-1] / shown.iloc[0] - 1:+.1%} from {shown.index[0]:%d %b %Y} to "
                       f"{shown.index[-1]:%d %b %Y}.")
    missing = {ticker: "no prices stored; rebuilding the screener table downloads them" if why == "no data" else why
               for ticker, why in view.missing.items()}
    if missing:
        st.caption("Not drawn: " + "; ".join(f"{names[t]}, {why}" for t, why in missing.items()) + ".")
    if late := charts.late_caption(view, names):
        st.caption(late)


def _charts(ticked: list[str]) -> None:
    st.subheader("Charts")
    layout = charts.ticked_layout(
        st.container(horizontal=True, vertical_alignment="bottom", gap="medium"), "screener", noun="companies",
        value="Price", change_help="Change: each close in % from the company's first close in the time range. One "
                                   "listed since starts later, at 0% on its own first close.",
    )
    if not ticked:
        st.info("Tick one or more companies in the list above to chart their prices.", icon=":material/check_box:")
        return
    st.caption("Daily closes as stored by the screener build, in the listing currency: adjusted for splits, not "
               "for dividends.")
    slots = {ticker: n for n, ticker in enumerate(ticked)}
    if layout.together:
        _price_chart(ticked, slots, layout)
        return
    for n, ticker in enumerate(ticked):
        if n % layout.per_row == 0:
            cells = st.columns(layout.per_row)  # a row per group, so neighbours line up at the top
        with cells[n % layout.per_row].expander(f"📈 {_company_label(ticker)}", expanded=True):
            _price_chart([ticker], slots, layout)


@st.fragment  # ticking a company reruns the list and the charts only, not the screen or the detail
def _list_and_charts() -> None:
    _charts(charts.keep_ticked(CHART_URL, _ranked_table()))


_list_and_charts()

# ==========================================================================================
# Company detail
# ==========================================================================================

st.subheader("Company detail")
if ranked.empty:
    st.write("No company passes this screen with these settings.")
    st.stop()
tickers = ranked["ticker"].tolist()
picker = st.container(horizontal=True, vertical_alignment="bottom")
pick = picker.selectbox("Company", tickers, index=0, format_func=_company_label)
row = ranked[ranked["ticker"] == pick].iloc[0]
company_detail.watch_button(picker, pick, cik=company_detail.sec_cik(row), label=_company_label(pick), origin=screen)
company_detail.show(company_detail.from_table(str(DB), _version(), row, UNIVERSE.source), key="screener")

# ==========================================================================================
# Footer
# ==========================================================================================

st.markdown("---")
as_of = payload.get("as_of")
st.caption(
    f"Table {data['run_id']} built {data['created_at']} for universe {payload.get('universe_label')} "
    f"({payload.get('n_companies')} companies)"
    + (f", as of {as_of}" if as_of else ", as of the latest filings")
    + ". " + SURVIVORSHIP_NOTE
    + ("" if payload.get("point_in_time", True) else " Fundamentals for this universe are not point-in-time.")
)
