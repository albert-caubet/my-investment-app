"""Screener page: ranked companies from the stored metrics table, charts of those ticked, and the detail of one.

A selector at the top picks the market: the US table, built from SEC EDGAR filings
(point-in-time), or the European one, built from Yahoo Finance (not point-in-time).
Each is the latest snapshot of its region written by ``python -m invest.jobs.fundamentals``,
read with the facts behind each company. Every row shows its inputs; the footer says
which universe, as of when, and whether the data is point-in-time. The build
button runs that same command in a separate process, so fetching happens only
when asked for. The company in the detail can be put on the watchlist, which the
Watchlist page shows.
"""

from __future__ import annotations

import os
import zlib
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import charts
import job_panel
import screener_help
import watchlist
from invest import secrets
from invest.data.cache import Store
from invest.data.edgar import SEC_USER_AGENT_HELP, user_agent_ok
from invest.fundamentals import screens
from invest.fundamentals.facts import fundamentals_as_of
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


@st.cache_data(ttl=600, show_spinner=False)
def _history(db: str, version: float, cik: int) -> tuple[pd.DataFrame, str | None]:
    """The fiscal-year history of a company's accounts, a row per year, and the currency they are in."""
    with Store(db, read_only=True) as store:
        facts = store.read_facts(cik)
    fund = fundamentals_as_of(facts)
    return fund.history_frame(), fund.currency


@st.cache_data(ttl=600, show_spinner=False)
def _prices(db: str, version: float, symbol: str) -> tuple[pd.Series, str | None]:
    with Store(db, read_only=True) as store:
        return store.read_prices(symbol), store.price_currency(symbol)


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
    ("earnings_yield", "EBIT/EV", "{:.1%}"),
    ("ev_ebitda", "EV/EBITDA", "{:.1f}"),
    ("roic", "ROIC", "{:.1%}"),
    ("fcf_yield", "FCF yield", "{:.1%}"),
    ("f_score", "F", "{:.0f}"),
    ("accruals", "Accruals", "{:.2f}"),
    ("gross_margin_slope", "GM slope/yr", "{:+.2%}"),
    ("pb", "P/B", "{:.2f}"),
    ("pe", "P/E", "{:.1f}"),
    ("net_debt_to_ebitda", "ND/EBITDA", "{:.1f}"),
    ("shareholder_yield", "Sh. yield", "{:.1%}"),
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
        loaded[ticker], currency = _prices(str(DB), _version(), ticker)
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
    if view.common_start and len(view.series) > 1:
        st.caption(f"Every line starts on {view.start:%d %b %Y}, the first day in the range that all of them have data.")


def _charts(ticked: list[str]) -> None:
    st.subheader("Charts")
    layout = charts.ticked_layout(
        st.container(horizontal=True, vertical_alignment="bottom", gap="medium"), "screener", noun="companies",
        value="Price", change_help="Change: each close in % from the first day of the time range on which the "
                                   "companies of a chart all have a close.",
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


def _sec_cik(company) -> int | None:
    """The company's SEC CIK; None for one from Yahoo, which is stored under a negative id instead."""
    cik = int(company["cik"])
    return cik if cik > 0 else None


def _watch(ticker: str, cik: int | None, label: str, screen_name: str) -> None:
    try:
        watchlist.add(ticker, cik=cik, note=f"from {screen_name} on {datetime.now():%Y-%m-%d}")
    except Exception as exc:
        st.toast(f"Could not change the watchlist: {exc}", icon=":material/error:")
    else:
        st.toast(f"{label} is on the watchlist.", icon=":material/star:")


def _unwatch(ticker: str, label: str) -> None:
    try:
        watchlist.remove(ticker)
    except Exception as exc:
        st.toast(f"Could not change the watchlist: {exc}", icon=":material/error:")
    else:
        st.toast(f"{label} is off the watchlist.", icon=":material/star_border:")


picker = st.container(horizontal=True, vertical_alignment="bottom")
pick = picker.selectbox("Company", tickers, index=0, format_func=_company_label)
row = ranked[ranked["ticker"] == pick].iloc[0]
try:
    watched = pick.upper() in watchlist.symbols()
except Exception as exc:  # the market database is held by a job
    picker.button("Add to watchlist", icon=":material/star_border:", key="watch_add", disabled=True,
                  help=f"The watchlist could not be read: {exc}")
else:
    if watched:
        picker.button("Remove from watchlist", icon=":material/star:", key="watch_remove", on_click=_unwatch,
                      args=(pick, _company_label(pick)), help="It is on the watchlist, which the Watchlist page charts.")
    else:
        picker.button("Add to watchlist", icon=":material/star_border:", key="watch_add", on_click=_watch,
                      args=(pick, _sec_cik(row), _company_label(pick), screen),
                      help="The Watchlist page charts every stock on the watchlist.")
def _money(value, currency: str | None) -> str:
    """A per-share amount with its currency: the price and the values can be in different ones."""
    return f"{value:,.2f} {currency or ''}".strip() if pd.notna(value) else "–"


price_currency = _prices(str(DB), _version(), pick)[1]
accounts_currency = row.get("currency") if pd.notna(row.get("currency")) else None
market_cap = pd.to_numeric(row.get("market_cap"), errors="coerce")
d1, d2, d3, d4, d5, d6 = st.columns(6)
d1.metric("Price", _money(row.get("price"), price_currency),
          help=screener_help.PRICE_HELP.format(date=row.get("price_date") or "an unknown date"))
d2.metric("Market cap", f"{market_cap / 1e9:,.1f} bn {accounts_currency or ''}".strip() if pd.notna(market_cap) else "–",
          help=screener_help.MARKET_CAP_HELP)
d3.metric("EPV / share", _money(row.get("epv_per_share"), accounts_currency), help=screener_help.EPV_HELP)
d4.metric("DCF / share", _money(row.get("dcf_per_share"), accounts_currency), help=screener_help.DCF_HELP)
d5.metric("Margin of safety", f"{row.get('mos_conservative'):+.0%}" if pd.notna(row.get("mos_conservative")) else "–",
          help=screener_help.MARGIN_OF_SAFETY_HELP)
d6.metric("Data to", str(row.get("period_end") or "–"),
          help=f"{screener_help.COLUMN_HELP['Data to']}\n\nBasis: {row.get('basis')}; fiscal year end {row.get('fy_end')}.")

#: The fiscal-year history, drawn as charts of fields that read together: (title, unit, fields).
#: Money is in millions of the accounts' currency, shares in millions, EPS as filed, per share,
#: and margins, which the facts hold as fractions of revenue, in %. Every field the facts
#: define has a place, so none is drawn in "Other", as filed.
HISTORY_CHARTS = (
    ("Revenue and profit", "money", ("revenue", "gross_profit", "operating_income", "pretax_income", "net_income")),
    ("Margins", "ratio", ("gross_margin", "operating_margin")),
    ("Costs, interest and tax", "money", ("cost_of_revenue", "depreciation", "interest_expense", "interest_income",
                                          "income_tax")),
    ("Cash flow", "money", ("cfo", "capex", "fcf", "dividends", "buybacks")),
    ("Assets and liabilities", "money", ("total_assets", "total_liabilities", "equity", "current_assets",
                                         "current_liabilities", "retained_earnings")),
    ("Long-lived assets", "money", ("ppe_net", "goodwill", "intangibles")),
    ("Cash and debt", "money", ("cash", "short_term_investments", "debt_current", "debt_noncurrent")),
    ("Deposits", "money", ("deposits",)),  # a bank's: they would dwarf its cash and debt
    ("Public float", "money", ("public_float",)),
    ("Shares outstanding", "shares", ("shares_outstanding",)),
    ("Diluted EPS", "per_share", ("eps_diluted",)),
)
FIELD_LABELS = {
    "revenue": "Revenue", "gross_profit": "Gross profit", "operating_income": "Operating income",
    "pretax_income": "Pretax income", "net_income": "Net income", "cost_of_revenue": "Cost of revenue",
    "depreciation": "Depreciation and amortisation", "interest_expense": "Interest expense",
    "interest_income": "Interest income", "income_tax": "Income tax",
    "cfo": "Operating cash flow", "capex": "Capex", "dividends": "Dividends", "buybacks": "Buybacks",
    "total_assets": "Total assets", "total_liabilities": "Total liabilities", "equity": "Equity",
    "current_assets": "Current assets", "current_liabilities": "Current liabilities",
    "retained_earnings": "Retained earnings", "cash": "Cash", "short_term_investments": "Short-term investments",
    "debt_current": "Debt due within a year", "debt_noncurrent": "Debt due later", "deposits": "Deposits",
    "shares_outstanding": "Shares outstanding", "eps_diluted": "Diluted EPS",
    "gross_margin": "Gross margin", "operating_margin": "Operating margin", "fcf": "Free cash flow",
    "ppe_net": "Property, plant and equipment", "goodwill": "Goodwill", "intangibles": "Intangibles",
    "public_float": "Public float",
}
HISTORY_YEARS = 10
DETAIL_PER_ROW = 2
DETAIL_HEIGHT = charts.HEIGHTS["Small"]


def _detail_figure(fig: go.Figure, names: list[str], key: str) -> None:
    """A chart of the detail's grid: its legend above the plot, the plot at the grid's height."""
    fig.update_layout(
        template="plotly_white", hovermode="x unified", showlegend=len(names) > 1,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    legend = charts.legend_height(names, DETAIL_PER_ROW) if len(names) > 1 else 0
    charts.readable(fig, height=DETAIL_HEIGHT + legend)
    fig.update_layout(legend_font_size=charts.LEGEND_FONT, hoverlabel_font_size=charts.LEGEND_FONT)
    st.plotly_chart(fig, width="stretch", key=key)


def _share_price_chart(ticker: str, since: pd.Timestamp | None) -> None:
    """The daily closes since the first fiscal year the history shows, ten years back without one."""
    prices, currency = _prices(str(DB), _version(), ticker)
    if prices.empty:
        st.caption(f"No prices stored for {ticker}. Rebuilding the screener table downloads them.")
        return
    start = since if since is not None else prices.index[-1] - pd.DateOffset(years=HISTORY_YEARS)
    shown = prices[prices.index >= start]
    currency = currency or "currency not recorded"
    fig = go.Figure(go.Scatter(
        x=shown.index, y=shown.to_numpy(), name=ticker, mode="lines", line=charts.series_line(0),
        hovertemplate=f"%{{y:,.2f}} {currency}<extra></extra>",
    ))
    fig.update_xaxes(hoverformat="%d %b %Y")
    fig.update_yaxes(title_text=currency, tickformat=",~f")
    _detail_figure(fig, [ticker], f"screener_detail_price_{ticker}")
    st.caption(f"Daily closes, adjusted for splits, not for dividends: {shown.iloc[-1] / shown.iloc[0] - 1:+.0%} "
               f"from {shown.index[0]:%d %b %Y} to {shown.index[-1]:%d %b %Y}.")


def _history_chart(title: str, unit: str, fields: list[str], history: pd.DataFrame, currency: str) -> None:
    """A line per field over the fiscal years, a marker on each year that has a value."""
    scale, axis, hover = {
        "money": (1e6, f"{currency} m", f"%{{y:,.0f}}m {currency}"),
        "shares": (1e6, "Shares, m", "%{y:,.1f}m"),
        "per_share": (1, f"{currency} per share", f"%{{y:,.2f}} {currency}"),
        "ratio": (0.01, "% of revenue", "%{y:.1f}%"),
        "as_filed": (1, "As filed", "%{y:,.4~g}"),
    }[unit]
    history = history[fields].dropna(how="all")  # no year on the axis that this chart has nothing for
    fig = go.Figure()
    negative = False
    for n, field in enumerate(fields):
        values = pd.to_numeric(history[field], errors="coerce") / scale
        negative |= bool((values < 0).any())
        name = FIELD_LABELS.get(field, field)
        fig.add_scatter(x=history.index, y=values, name=name, mode="lines+markers", line=charts.series_line(n),
                        marker=dict(size=7), connectgaps=False, hovertemplate=f"{name}: {hover}<extra></extra>")
    # Every year while they fit across half a row, every other one past six; written 2024, not 2,024.
    fig.update_xaxes(dtick=1 if len(history) <= 6 else 2, tickformat="d")
    # 12,500 rather than Plotly's 12.5k, which in millions would read as thousands
    fig.update_yaxes(title_text=axis, tickformat=",~f")
    if negative:  # a loss, or a negative balance: the zero line shows which side of it a year is on
        fig.add_hline(y=0, line_width=1, line_color="rgba(128, 128, 128, 0.6)")
    _detail_figure(fig, [FIELD_LABELS.get(f, f) for f in fields], f"screener_history_{title}")


def _detail_charts(ticker: str) -> None:
    """The share price, then the fiscal-year history of the accounts, two charts to a row."""
    try:
        history, currency = _history(str(DB), _version(), int(row["cik"]))
    except Exception as exc:
        history, currency = pd.DataFrame(), None
        st.caption(f"History unavailable: {exc}")
    shown = history.tail(HISTORY_YEARS).dropna(axis=1, how="all")
    currency = currency or "currency not recorded"
    drawn = [(title, unit, [f for f in fields if f in shown.columns]) for title, unit, fields in HISTORY_CHARTS]
    grouped = {f for _, _, fields in HISTORY_CHARTS for f in fields}
    # A field added to the facts and not yet placed here: drawn as filed, since its unit is unknown.
    other = [f for f in shown.columns if f not in grouped]
    if other:
        drawn.append(("Other", "as_filed", other))
    drawn = [(title, unit, fields) for title, unit, fields in drawn if fields]
    since = pd.Timestamp(year=int(shown.index[0]), month=1, day=1) if not shown.empty else None

    cells = []
    for n in range(len(drawn) + 1):  # the share price first
        if n % DETAIL_PER_ROW == 0:
            cells = st.columns(DETAIL_PER_ROW)  # a row per pair, so neighbours line up at the top
        with cells[n % DETAIL_PER_ROW].container(border=True):
            if n == 0:
                st.markdown("**Share price**")
                _share_price_chart(ticker, since)
                continue
            title, unit, fields = drawn[n - 1]
            st.markdown(f"**{title}**")
            _history_chart(title, unit, fields, shown, currency)
    if drawn:
        as_given = "as filed, latest restatement" if FROM_EDGAR else "as Yahoo Finance gives it, restatements included"
        st.caption(f"Fiscal-year history {as_given}. Money in millions of {currency}, the currency of the accounts; "
                   f"shares in millions; EPS per share, as reported. A gap is a year without the figure. Click a "
                   f"legend entry to hide its line.")


_detail_charts(pick)

with st.expander("Intrinsic value inputs", expanded=True):
    inputs = row.get("valuation_inputs") or {}
    left, right = st.columns(2)
    epv_inputs = inputs.get("epv", {}) if isinstance(inputs, dict) else {}
    dcf_inputs = inputs.get("dcf", {}) if isinstance(inputs, dict) else {}
    left.markdown(f"**EPV** per share: {row.get('epv_per_share') if pd.notna(row.get('epv_per_share')) else '–'}")
    left.json({k: v for k, v in epv_inputs.items()}, expanded=False)
    if inputs.get("epv_notes"):
        left.caption("; ".join(inputs["epv_notes"]))
    right.markdown(f"**DCF** per share: {row.get('dcf_per_share') if pd.notna(row.get('dcf_per_share')) else '–'}")
    right.json({k: v for k, v in dcf_inputs.items()}, expanded=False)
    if inputs.get("dcf_notes"):
        right.caption("; ".join(inputs["dcf_notes"]))

with st.expander("Piotroski checks, tags used and notes"):
    checks = row.get("f_checks") or {}
    if isinstance(checks, dict):
        st.write(", ".join(f"{k}: {'yes' if v else ('no' if v is False else 'n/a')}" for k, v in checks.items()))
    tags = row.get("tags") or {}
    if isinstance(tags, dict):
        source = "XBRL tag" if FROM_EDGAR else "Yahoo Finance item"
        st.dataframe(pd.DataFrame({"field": list(tags), source: [str(t).removeprefix("yahoo:") for t in tags.values()]}),
                     width="stretch", hide_index=True)
    notes = row.get("notes") or []
    if notes:
        st.write("; ".join(str(n) for n in notes))

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
