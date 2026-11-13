"""The detail of one company, as the Screener and the Company financials page show it.

The price, market cap, both intrinsic values and the margin of safety; on the Company
financials page the measures the Screener's list has as columns; the share price and the
fiscal-year history of the accounts as charts; then the inputs of the two values, the
Piotroski checks, the tags read and the notes. What it shows comes as one ``CompanyData``:
from the market database for a company of a stored screener table (``from_table``), or
fetched just now for any other (``market_data.fetch_company``). Each page keeps its
charts apart from the other's by a key of its own.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import charts
import screener_help
import watchlist
from invest.data.cache import Store
from invest.fundamentals.facts import fundamentals_as_of
from invest.jobs.fundamentals import CompanyData

#: The Company financials page, and the URL parameter it reads the company from: ?company=7974.T
PAGE = "pages/company.py"
URL_KEY = "company"

#: The measures of a company's row from EBIT/EV on, as the Screener's list shows them:
#: (column, label, format). The Screener puts its rank columns after them.
MEASURES = (
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
)

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
#: The detail's time range: how many fiscal years it charts, by label; None is every year stored.
HISTORY_RANGES = {"5Y": 5, "10Y": 10, "Max": None}
DEFAULT_HISTORY_RANGE = "10Y"
HISTORY_RANGE_HELP = (
    "The fiscal years the charts show, and the share price over the same years. A US filer's accounts come from "
    "SEC EDGAR, which has them back to the late 2000s; any other company's from Yahoo Finance, which gives only its "
    "last four or five at a time, so 10Y and Max show no more of those."
)
#: Years back the share price goes for a company without a history to start it from.
PRICE_YEARS_WITHOUT_HISTORY = 10
#: The figures that make a fiscal year of the history, as ``facts.fundamentals_as_of`` counts them.
CORE_FIELDS = ("revenue", "net_income", "total_assets")
DETAIL_PER_ROW = 2
DETAIL_HEIGHT = charts.HEIGHTS["Small"]

# ==========================================================================================
# A company of a stored screener table
# ==========================================================================================


@st.cache_data(ttl=600, show_spinner=False)
def stored_history(db: str, version: float, cik: int) -> tuple[pd.DataFrame, str | None]:
    """The fiscal-year history of a company's accounts, a row per year, and the currency they are in."""
    with Store(db, read_only=True) as store:
        facts = store.read_facts(cik)
    fund = fundamentals_as_of(facts)
    return fund.history_frame(), fund.currency


@st.cache_data(ttl=600, show_spinner=False)
def stored_prices(db: str, version: float, symbol: str) -> tuple[pd.Series, str | None]:
    """A symbol's daily closes as the screener build stored them, and the currency they are quoted in."""
    with Store(db, read_only=True) as store:
        return store.read_prices(symbol), store.price_currency(symbol)


def from_table(db: str, version: float, row: pd.Series, source: str) -> CompanyData:
    """The company of ``row``, a row of a stored screener table whose accounts come from ``source``:
    its history and share price as the market database has them."""
    problems = []
    try:
        history, currency = stored_history(db, version, int(row["cik"]))
    except Exception as exc:
        history, currency = pd.DataFrame(), None
        problems.append(f"History unavailable: {exc}")
    prices, price_currency = stored_prices(db, version, row["ticker"])
    return CompanyData(row, history, currency, prices, price_currency, source, stored=True, problems=problems)


def link(container, symbol: str, *, label: str = "Financials") -> None:
    """A link in ``container`` to the Company financials page of ``symbol``, a Yahoo symbol."""
    container.page_link(PAGE, label=label, icon=":material/query_stats:", query_params={URL_KEY: symbol},
                        help="Its accounts, measures and valuation, on the Company financials page.")


# ==========================================================================================
# The watchlist
# ==========================================================================================


def sec_cik(row: pd.Series) -> int | None:
    """The company's SEC CIK; None for one from Yahoo, which is stored under a negative id instead."""
    cik = int(row["cik"])
    return cik if cik > 0 else None


def _watch(symbol: str, cik: int | None, label: str, origin: str) -> None:
    try:
        watchlist.add(symbol, cik=cik, note=f"from {origin} on {datetime.now():%Y-%m-%d}")
    except Exception as exc:
        st.toast(f"Could not change the watchlist: {exc}", icon=":material/error:")
    else:
        st.toast(f"{label} is on the watchlist.", icon=":material/star:")


def _unwatch(symbol: str, label: str) -> None:
    try:
        watchlist.remove(symbol)
    except Exception as exc:
        st.toast(f"Could not change the watchlist: {exc}", icon=":material/error:")
    else:
        st.toast(f"{label} is off the watchlist.", icon=":material/star_border:")


def watch_button(container, symbol: str, *, cik: int | None, label: str, origin: str) -> None:
    """A button in ``container`` that puts the company on the watchlist, or takes it off when it is
    on it. The watchlist's note says where it was put on from: ``origin``, and the day."""
    try:
        watched = symbol.upper() in watchlist.symbols()
    except Exception as exc:  # the market database is held by a job
        container.button("Add to watchlist", icon=":material/star_border:", key="watch_add", disabled=True,
                         help=f"The watchlist could not be read: {exc}")
        return
    if watched:
        container.button("Remove from watchlist", icon=":material/star:", key="watch_remove", on_click=_unwatch,
                         args=(symbol, label), help="It is on the watchlist, which the Watchlist page charts.")
    else:
        container.button("Add to watchlist", icon=":material/star_border:", key="watch_add", on_click=_watch,
                         args=(symbol, cik, label, origin), help="The Watchlist page charts every stock on the watchlist.")


# ==========================================================================================
# The detail
# ==========================================================================================


def _money(value, currency: str | None) -> str:
    """A per-share amount with its currency: the price and the values can be in different ones."""
    return f"{value:,.2f} {currency or ''}".strip() if pd.notna(value) else "–"


def _tiles(company: CompanyData) -> None:
    row = company.row
    accounts_currency = row.get("currency") if pd.notna(row.get("currency")) else None
    market_cap = pd.to_numeric(row.get("market_cap"), errors="coerce")
    d1, d2, d3, d4, d5, d6 = st.columns(6)
    d1.metric("Price", _money(row.get("price"), company.price_currency),
              help=screener_help.PRICE_HELP.format(date=row.get("price_date") or "an unknown date"))
    d2.metric("Market cap", f"{market_cap / 1e9:,.1f} bn {accounts_currency or ''}".strip() if pd.notna(market_cap)
              else "–", help=screener_help.MARKET_CAP_HELP)
    d3.metric("EPV / share", _money(row.get("epv_per_share"), accounts_currency), help=screener_help.EPV_HELP)
    d4.metric("DCF / share", _money(row.get("dcf_per_share"), accounts_currency), help=screener_help.DCF_HELP)
    d5.metric("Margin of safety", f"{row.get('mos_conservative'):+.0%}" if pd.notna(row.get("mos_conservative"))
              else "–", help=screener_help.MARGIN_OF_SAFETY_HELP)
    d6.metric("Data to", str(row.get("period_end") or "–"),
              help=f"{screener_help.COLUMN_HELP['Data to']}\n\nBasis: {row.get('basis')}; fiscal year end "
                   f"{row.get('fy_end')}.")


def _measures(company: CompanyData) -> None:
    """The company's measures in one row, as the Screener's list has them, each with its help."""
    row = company.row
    table = pd.DataFrame({label: [pd.to_numeric(row.get(column), errors="coerce")] for column, label, _ in MEASURES})
    st.dataframe(
        table.style.format({label: fmt for _, label, fmt in MEASURES}, na_rep="–"), width="stretch", hide_index=True,
        column_config={label: st.column_config.Column(label, help=screener_help.COLUMN_HELP[label])
                       for _, label, _ in MEASURES},
        placeholder="–",  # a column given help shows a blank as "None", whatever the styler says
    )


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


def _share_price_chart(company: CompanyData, since: pd.Timestamp | None, years: int | None, key: str) -> None:
    """The daily closes since the first fiscal year the history shows; without one, over the time
    range's ``years`` (ten for Max), back from the last close."""
    ticker, prices = company.row["ticker"], company.prices
    if prices.empty:
        st.caption(f"No prices stored for {ticker}. Rebuilding the screener table downloads them." if company.stored
                   else f"Yahoo Finance sent no prices for {ticker}.")
        return
    back = pd.DateOffset(years=years or PRICE_YEARS_WITHOUT_HISTORY)
    start = since if since is not None else prices.index[-1] - back
    shown = prices[prices.index >= start]
    currency = company.price_currency or "currency not recorded"
    fig = go.Figure(go.Scatter(
        x=shown.index, y=shown.to_numpy(), name=ticker, mode="lines", line=charts.series_line(0),
        hovertemplate=f"%{{y:,.2f}} {currency}<extra></extra>",
    ))
    fig.update_xaxes(hoverformat="%d %b %Y")
    fig.update_yaxes(title_text=currency, tickformat=",~f")
    _detail_figure(fig, [ticker], f"{key}_detail_price_{ticker}")
    st.caption(f"Daily closes, adjusted for splits, not for dividends: {shown.iloc[-1] / shown.iloc[0] - 1:+.0%} "
               f"from {shown.index[0]:%d %b %Y} to {shown.index[-1]:%d %b %Y}.")


def _history_chart(title: str, unit: str, fields: list[str], history: pd.DataFrame, currency: str, key: str) -> None:
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
    # Every year while they fit across half a row, every other one past six, every fifth past twelve
    # (2010, 2015…); written 2024, not 2,024.
    fig.update_xaxes(dtick=1 if len(history) <= 6 else 2 if len(history) <= 12 else 5, tickformat="d")
    # 12,500 rather than Plotly's 12.5k, which in millions would read as thousands
    fig.update_yaxes(title_text=axis, tickformat=",~f")
    if negative:  # a loss, or a negative balance: the zero line shows which side of it a year is on
        fig.add_hline(y=0, line_width=1, line_color="rgba(128, 128, 128, 0.6)")
    _detail_figure(fig, [FIELD_LABELS.get(f, f) for f in fields], f"{key}_history_{title}")


def _time_range(key: str) -> int | None:
    """The detail's time range control, kept in the URL: how many fiscal years to chart, None for all."""
    label = st.segmented_control("Time range", list(HISTORY_RANGES), default=DEFAULT_HISTORY_RANGE, required=True,
                                 key=f"{key}_detail_range", bind="query-params", help=HISTORY_RANGE_HELP)
    return HISTORY_RANGES[label]


def _window(history: pd.DataFrame, years: int | None) -> pd.DataFrame:
    """The last ``years`` fiscal years of ``history`` with a core figure, and any year after them:
    the share count a 10-K gives as of the January after the year end is no fiscal year of its own.
    Every year with None."""
    if years is None or history.empty:
        return history
    core = history.reindex(columns=list(CORE_FIELDS)).dropna(how="all").index
    if core.empty:
        return history.tail(years)
    return history[history.index >= core[-years:][0]]


def _detail_charts(company: CompanyData, key: str, years: int | None) -> None:
    """The share price, then the fiscal-year history of the accounts over the last ``years``, all
    with None, two charts to a row."""
    for problem in company.problems:
        st.caption(problem)
    shown = _window(company.history, years).dropna(axis=1, how="all")
    currency = company.currency or "currency not recorded"
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
                _share_price_chart(company, since, years, key)
                continue
            title, unit, fields = drawn[n - 1]
            st.markdown(f"**{title}**")
            _history_chart(title, unit, fields, shown, currency, key)
    if drawn:
        as_given = ("as filed, latest restatement" if company.source == "edgar"
                    else "as Yahoo Finance gives it, restatements included")
        st.caption(f"Fiscal-year history {as_given}. Money in millions of {currency}, the currency of the accounts; "
                   f"shares in millions; EPS per share, as reported. A gap is a year without the figure. Click a "
                   f"legend entry to hide its line.")


def _inputs(company: CompanyData) -> None:
    row = company.row
    with st.expander("Intrinsic value inputs", expanded=True):
        # A row whose metrics failed has none: NaN in a table of others, which is truthy
        inputs = row.get("valuation_inputs")
        inputs = inputs if isinstance(inputs, dict) else {}
        left, right = st.columns(2)
        epv_inputs, dcf_inputs = inputs.get("epv") or {}, inputs.get("dcf") or {}
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
            source = "XBRL tag" if company.source == "edgar" else "Yahoo Finance item"
            st.dataframe(pd.DataFrame({"field": list(tags),
                                       source: [str(t).removeprefix("yahoo:") for t in tags.values()]}),
                         width="stretch", hide_index=True)
        notes = row.get("notes")
        if isinstance(notes, list) and notes:
            st.write("; ".join(str(n) for n in notes))


def show(company: CompanyData, *, key: str, measures: bool = False) -> None:
    """The detail of ``company``: the tiles, then its measures in a row when ``measures`` (the
    Screener has them in its list), the time range, the charts and the inputs. ``key`` names its
    charts and its time range, which the URL keeps as ``?<key>_detail_range=Max``."""
    _tiles(company)
    if measures:
        _measures(company)
    _detail_charts(company, key, _time_range(key))
    _inputs(company)
