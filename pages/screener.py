"""Screener page: ranked companies from the stored metrics table, detail and watchlist.

Reads the latest ``screener`` snapshot written by ``python -m invest.jobs.fundamentals``
and the facts behind each company. Every row shows its inputs; the footer says
which universe, as of when, and whether the data is point-in-time. The build
button runs that same command in a separate process, so fetching happens only
when asked for.
"""

from __future__ import annotations

import os
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import charts
import job_panel
from invest import secrets
from invest.data.cache import Store
from invest.data.edgar import SEC_USER_AGENT_HELP, user_agent_ok
from invest.fundamentals import screens
from invest.fundamentals.facts import fundamentals_as_of
from invest.fundamentals.universe import SURVIVORSHIP_NOTE
from invest.paths import db_path

st.title("Value screener")

job_panel.follow_if_running()  # a job holds the database; nothing below may read it

DB = db_path()
BUILD_HELP = (
    "Runs `python -m invest.jobs.fundamentals` in the background: the filings of every company in the "
    "universe from SEC EDGAR, then their prices. It takes several minutes."
)
WHAT_BUILDING_DOES = (
    "Building it fetches the filings of every company in the universe from SEC EDGAR, then their prices, "
    "and takes several minutes. Outside the app, `python -m invest.jobs.fundamentals` does the same thing."
)


def _edgar_ready() -> bool:
    secrets.reload()  # an edit to secrets.toml counts on the next rerun, without a restart
    return user_agent_ok(secrets.sec_user_agent())


def _offer_build(message: str) -> None:
    """Nothing to show yet: say why, offer the build, and stop the page."""
    ready = _edgar_ready()
    st.info(message)
    if not ready:
        st.warning(f"The build needs one setting first.\n\n{SEC_USER_AGENT_HELP}\n\nThen reload this page.")
    job_panel.start_button("fundamentals", "Build the screener table", type="primary", disabled=not ready,
                           help=BUILD_HELP)
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
def _load(db: str, version: float) -> dict | None:
    with Store(db, read_only=True) as store:
        snap = store.load_snapshot("screener")
        if snap is None:
            return None
        run_id, created_at, payload = snap
        previous = store.load_snapshot("screener", offset=1)
        watch = store.watchlist()
    return {
        "run_id": run_id,
        "created_at": created_at,
        "payload": payload,
        "previous_rows": previous[2].get("rows", []) if previous else None,
        "watchlist": watch,
    }


@st.cache_data(ttl=600, show_spinner=False)
def _history(db: str, version: float, cik: int) -> pd.DataFrame:
    with Store(db, read_only=True) as store:
        facts = store.read_facts(cik)
    return fundamentals_as_of(facts).history_frame()


@st.cache_data(ttl=600, show_spinner=False)
def _prices(db: str, version: float, symbol: str) -> tuple[pd.Series, str | None]:
    with Store(db, read_only=True) as store:
        return store.read_prices(symbol), store.price_currency(symbol)


RANGE_KEY, WINDOW_KEY = "price_range", "price_window"


def _window_dragged() -> None:
    """A dragged window is no preset's: no button stays lit for dates it no longer shows."""
    st.session_state[RANGE_KEY] = None


@st.fragment  # a range button or the slider reruns the chart only, not the screen above it
def _price_chart(symbol: str, name: str) -> None:
    prices, currency = _prices(str(DB), _version(), symbol)
    if prices.empty:
        st.caption(f"No prices stored for {symbol}. Rebuilding the screener table downloads them.")
        return
    first, last = prices.index[0].date(), prices.index[-1].date()

    # Both values are set here, before the widgets, and never passed as their defaults:
    # Streamlit warns when a widget has a default and Session State sets it too.
    st.session_state.setdefault(RANGE_KEY, charts.DEFAULT_RANGE)
    preset = st.session_state[RANGE_KEY]
    if preset:
        st.session_state[WINDOW_KEY] = (charts.range_start(preset, first, last), last)
    else:  # dragged, maybe on another company's chart: kept as far as this history allows
        st.session_state[WINDOW_KEY] = charts.clamp_window(st.session_state.get(WINDOW_KEY, (first, last)), first, last)

    left, right = st.columns(2, vertical_alignment="bottom")
    left.segmented_control("Range", charts.RANGES, key=RANGE_KEY)
    start, end = right.slider("Dates shown", min_value=first, max_value=last, key=WINDOW_KEY,
                              on_change=_window_dragged)

    shown = prices[pd.Timestamp(start):pd.Timestamp(end)]
    if shown.empty:
        st.caption("No trading day in the dates shown.")
        return
    # Short enough not to be cut on a narrow screen: Plotly does not wrap a title.
    title = f"{name} ({symbol})"
    if len(shown) > 1:
        title += f": {shown.iloc[-1] / shown.iloc[0] - 1:+.1%}"
    fig = go.Figure(go.Scatter(
        x=shown.index, y=shown.to_numpy(), name=symbol, mode="lines" if len(shown) > 1 else "markers",
        line=dict(color="#1f77b4", width=2),
        hovertemplate=f"%{{y:,.2f}} {currency or ''}<extra></extra>",
    ))
    fig.update_layout(
        title=title, template="plotly_white", hovermode="x unified", showlegend=False,
        xaxis_hoverformat="%Y-%m-%d", yaxis_title=f"Daily close ({currency or 'currency not recorded'})",
        height=380,  # charts.readable makes it taller
    )
    st.plotly_chart(charts.readable(fig), width="stretch")
    st.caption(f"The change in the title runs from the first close shown to the last. Closes as stored by the "
               f"screener build, {first} to {last}, in the listing currency: adjusted for splits, not for dividends.")


try:
    data = _load(str(DB), _version())
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
ready = _edgar_ready()
with right:
    job_panel.start_button("fundamentals", "Rebuild now", disabled=not ready,
                           help=BUILD_HELP if ready else f"Needs one setting first.\n\n{SEC_USER_AGENT_HELP}")
job_panel.last_result("fundamentals")

# ==========================================================================================
# Controls
# ==========================================================================================

c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
screen_labels = {label: name for name, (label, _) in screens.SCREENS.items()}
screen_label = c1.selectbox("Screen", list(screen_labels))
screen = screen_labels[screen_label]
min_cap_bn = c2.number_input("Min market cap (bn)", min_value=0.0, value=0.0, step=1.0)
exclude_fin = c3.checkbox("Exclude financials", value=True)
filter_traps = c4.checkbox("Filter value traps", value=True)
sector_neutral = st.checkbox("Sector-neutral z-scores (quality-value only)", value=False) if screen == "quality_value" else False

options = {}
if screen in ("magic_formula", "quality_value"):
    options["exclude_financials"] = exclude_fin
if screen == "magic_formula" and min_cap_bn > 0:
    options["min_market_cap"] = min_cap_bn * 1e9
if screen == "quality_value":
    options["sector_neutral"] = sector_neutral

ranked, excluded = screens.run_screen(screen, table, filter_traps=filter_traps, **options)
if screen != "magic_formula" and min_cap_bn > 0 and "market_cap" in ranked.columns:
    ranked = ranked[pd.to_numeric(ranked["market_cap"], errors="coerce") >= min_cap_bn * 1e9]

# ==========================================================================================
# Ranked table
# ==========================================================================================

st.subheader(f"{screen_label}: {len(ranked)} companies")

SHOW = [
    ("rank", "Rank", "{:.0f}"),
    ("ticker", "Ticker", None),
    ("name", "Name", None),
    ("sector", "Sector", None),
    ("market_cap", "Mkt cap (bn)", None),
    ("earnings_yield", "EBIT/EV", "{:.1%}"),
    ("roic", "ROIC", "{:.1%}"),
    ("fcf_yield", "FCF yield", "{:.1%}"),
    ("f_score", "F", "{:.0f}"),
    ("accruals", "Accruals", "{:.2f}"),
    ("gross_margin_slope", "GM slope/yr", "{:+.2%}"),
    ("pb", "P/B", "{:.2f}"),
    ("pe", "P/E", "{:.1f}"),
    ("ev_ebitda", "EV/EBITDA", "{:.1f}"),
    ("net_debt_to_ebitda", "ND/EBITDA", "{:.1f}"),
    ("shareholder_yield", "Sh. yield", "{:.1%}"),
    ("mos_conservative", "Margin of safety", "{:+.0%}"),
    ("magic_rank", "Rank sum", "{:.0f}"),
    ("composite", "Composite z", "{:+.2f}"),
    ("period_end", "Data to", None),
    ("basis", "Basis", None),
]
view = ranked.copy()
if "market_cap" in view.columns:
    view["market_cap"] = pd.to_numeric(view["market_cap"], errors="coerce") / 1e9
columns = [c for c, _, _ in SHOW if c in view.columns]
formats = {c: f for c, _, f in SHOW if c in view.columns and f}
labels = {c: label for c, label, _ in SHOW}
styled = view[columns].rename(columns=labels).style.format(
    {labels[c]: f for c, f in formats.items()} | {"Mkt cap (bn)": "{:,.1f}"}, na_rep="–")
st.dataframe(styled, width="stretch", hide_index=True, height=min(600, 40 + 35 * len(view)))
st.caption(
    "EBIT/EV and ROIC are Greenblatt's earnings yield and return on capital; accruals is (net income minus "
    "operating cash flow) over average assets; F is the Piotroski score out of 9; the margin of safety is against "
    "the lower of the earnings power value and a conservative DCF. TTM figures combine the last fiscal year with "
    "the year-to-date filings; the Basis column says which."
)

if data["previous_rows"]:
    prev_table = pd.DataFrame(data["previous_rows"])
    try:
        prev_ranked, _ = screens.run_screen(screen, prev_table, filter_traps=filter_traps, **options)
        entrants, dropouts = screens.entrants_and_dropouts(ranked["ticker"].tolist(), prev_ranked["ticker"].tolist(), top_n=20)
        if entrants or dropouts:
            st.caption(f"Top 20 since the previous table: entered {', '.join(entrants) or 'none'}; left {', '.join(dropouts) or 'none'}.")
    except Exception:
        pass

if not excluded.empty:
    with st.expander(f"Excluded as possible value traps ({len(excluded)})"):
        st.dataframe(excluded[["ticker", "name", "reason"]], width="stretch", hide_index=True)

# ==========================================================================================
# Company detail
# ==========================================================================================

st.subheader("Company detail")
if ranked.empty:
    st.write("No company passes this screen with these settings.")
    st.stop()
tickers = ranked["ticker"].tolist()
company_names = dict(zip(ranked["ticker"], ranked["name"]))


def _company_label(ticker: str) -> str:
    name = company_names.get(ticker)
    return f"{name} ({ticker})" if pd.notna(name) and name else ticker


pick = st.selectbox("Company", tickers, index=0, format_func=_company_label)
row = ranked[ranked["ticker"] == pick].iloc[0]
d1, d2, d3, d4 = st.columns(4)
d1.metric("Price", f"{row.get('price'):,.2f}" if pd.notna(row.get("price")) else "–", help=f"as of {row.get('price_date')}")
d2.metric("Conservative value / share", f"{row.get('value_conservative'):,.2f}" if pd.notna(row.get("value_conservative")) else "–",
          help="The lower of EPV and DCF per share, in the currency of the accounts.")
d3.metric("Margin of safety", f"{row.get('mos_conservative'):+.0%}" if pd.notna(row.get("mos_conservative")) else "–")
d4.metric("Data to", str(row.get("period_end") or "–"), help=f"basis: {row.get('basis')}; fiscal year end {row.get('fy_end')}")

_price_chart(pick, row.get("name") if pd.notna(row.get("name")) else pick)

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
        st.dataframe(pd.DataFrame({"field": list(tags), "XBRL tag": list(tags.values())}), width="stretch", hide_index=True)
    notes = row.get("notes") or []
    if notes:
        st.write("; ".join(str(n) for n in notes))

try:
    history = _history(str(DB), _version(), int(row["cik"]))
    if not history.empty:
        st.markdown("**Fiscal-year history (as filed, latest restatement)**")
        shown = history.tail(10).T
        shown.columns = [str(c) for c in shown.columns]
        st.dataframe(shown.style.format("{:,.3g}", na_rep="–"), width="stretch")
except Exception as exc:
    st.caption(f"History unavailable: {exc}")

# ==========================================================================================
# Watchlist
# ==========================================================================================

st.subheader("Watchlist")
watch = data["watchlist"]
w1, w2 = st.columns([1, 3])
if w1.button(f"Add {pick} to watchlist", key="watch_add"):
    try:
        with Store(DB) as writer:
            writer.add_watch(pick, cik=int(row["cik"]), note=f"from {screen} on {datetime.now():%Y-%m-%d}")
        _load.clear()
        st.success(f"{pick} added.")
    except Exception as exc:
        st.error(f"Could not write the watchlist: {exc}")
if not watch.empty:
    merged = watch.merge(table[["ticker", "name", "price", "price_date", "value_conservative", "mos_conservative", "f_score"]],
                         left_on="symbol", right_on="ticker", how="left")
    st.dataframe(
        merged[["symbol", "name", "price", "value_conservative", "mos_conservative", "f_score", "note", "added_at"]]
        .style.format({"price": "{:,.2f}", "value_conservative": "{:,.2f}", "mos_conservative": "{:+.0%}", "f_score": "{:.0f}"}, na_rep="–"),
        width="stretch", hide_index=True,
    )
    remove = w2.selectbox("Remove from watchlist", ["–"] + watch["symbol"].tolist(), key="watch_remove")
    if remove != "–" and st.button("Remove", key="watch_remove_btn"):
        try:
            with Store(DB) as writer:
                writer.remove_watch(remove)
            _load.clear()
            st.rerun()
        except Exception as exc:
            st.error(f"Could not write the watchlist: {exc}")
else:
    st.caption("Empty. Add a company from the detail above; price against value and the margin of safety show here.")

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
