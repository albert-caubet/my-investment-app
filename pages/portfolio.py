from datetime import date

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import charts
import market_data as md
import portfolio_math as pm
from database import get_all_transactions, get_cash_accounts, get_portfolio_design

CURRENCY_SYMBOL = {"EUR": "€", "USD": "$"}


raw_data = get_all_transactions()

if not raw_data:
    st.title("Current Portfolio")
    st.warning("No transactions found.")
    st.stop()

# ==========================================================================================================
# 1. LOAD AND REPLAY TRANSACTIONS
# ==========================================================================================================

transactions, problems = pm.load_transactions(raw_data)

if not transactions:
    st.title("Current Portfolio")
    st.error("No usable transactions.")
    for problem in problems:
        st.write("- ", problem)
    st.stop()

positions = pm.build_positions(transactions)

txs_by_asset: dict[str, list[pm.Transaction]] = {}
for tx in transactions:
    txs_by_asset.setdefault(tx.asset_id, []).append(tx)

# The pricing symbol is not the identity: assets are keyed by ISIN so that grouping
# stays stable, but a resolved ticker is the better thing to send to Yahoo.
price_symbol = {aid: pos.price_symbol for aid, pos in positions.items()}
symbols = tuple(sorted(set(price_symbol.values())))

# ==========================================================================================================
# 2. MARKET DATA
# ==========================================================================================================

valuations, listing_ccy, rates = md.live_valuations(positions)
open_ids = [aid for aid, pos in positions.items() if pos.is_open]
closed_ids = [aid for aid, pos in positions.items() if not pos.is_open]

totals = pm.portfolio_totals([valuations[aid] for aid in positions])

# Bank balances entered by hand on the transactions page. Dry Powder is part of the
# portfolio: in its value, weights, charts and design. Cash in current accounts is the
# day-to-day reserve and emergency fund, shown on its own and outside all of that.
# Total Value and every P&L figure stay on the investments alone.
cash_accounts, cash_problems = pm.clean_cash_accounts(get_cash_accounts())
dry_powder = [a for a in cash_accounts if a.category != pm.RESERVE_CATEGORY]
reserve = [a for a in cash_accounts if a.category == pm.RESERVE_CATEGORY]
dry_total = sum(a.balance_eur for a in dry_powder)
reserve_total = sum(a.balance_eur for a in reserve)
portfolio_value = totals.market_value_eur + dry_total


def _weight(eur: float | None) -> float | None:
    """Share of the portfolio (investments plus Dry Powder), so the column sums to 100."""
    return eur / portfolio_value * 100 if eur is not None and portfolio_value > 0 else None


# The allocation against the design saved on the Portfolio design page, by category.
current_bands = pm.capital_by_band(
    [(positions[aid].category, valuations[aid].market_value_eur) for aid in open_ids]
    + [(a.category, a.balance_eur) for a in dry_powder]
)
design_doc = get_portfolio_design() or {}
design, design_problems = pm.clean_design(design_doc.get("targets"))
drift = pm.allocation_drift(current_bands, design) if design else []

# ==========================================================================================================
# 3. ALPHA & BETA (CAPM)
# ==========================================================================================================
# Estimated from a dedicated 2-year history, in EUR, and never from the spot fetch.
# Reusing one frame for both is how these became six-observation noise.

capm: dict[str, pm.CapmResult] = {aid: pm.CapmResult() for aid in positions}
rf_annual = md.RF_ANNUAL_DEFAULT

try:
    hist = md.fetch_price_history(symbols + (md.BENCHMARK_TICKER,), md.CAPM_PERIOD)
    if not hist.empty and md.BENCHMARK_TICKER in hist.columns:
        capm_ccy = dict(listing_ccy)
        capm_ccy[md.BENCHMARK_TICKER] = md.BENCHMARK_CCY
        fx_hist = md.fetch_fx_history(tuple(c for c in capm_ccy.values() if c), md.CAPM_PERIOD)
        eur_panel = md.to_eur_panel(hist, capm_ccy, fx_hist)
        returns = eur_panel.pct_change()

        for aid in positions:
            column = price_symbol[aid]
            if column not in returns.columns:
                continue
            if not listing_ccy.get(column):
                capm[aid] = pm.CapmResult(note="listing currency unknown")
                continue
            pair = (
                pd.concat([returns[column], returns[md.BENCHMARK_TICKER]], axis=1)
                .replace([np.inf, -np.inf], np.nan)
                .dropna()
            )
            capm[aid] = pm.estimate_capm(
                pair.iloc[:, 0].to_numpy(),
                pair.iloc[:, 1].to_numpy(),
                rf_annual=rf_annual,
            )
except Exception as exc:  # analytics must never take the dashboard down
    st.warning(f"Could not estimate Beta/Alpha: {exc}")

# ==========================================================================================================
# --- DISPLAY DASHBOARD ---
# ==========================================================================================================

st.title("Portfolio Dashboard")

if problems:
    with st.expander(f"⚠️ {len(problems)} data quality note(s)"):
        for problem in problems:
            st.write("- ", problem)

position_warnings = [(aid, w) for aid, pos in positions.items() for w in pos.warnings]
if position_warnings:
    with st.expander(f"⚠️ {len(position_warnings)} position warning(s)"):
        for aid, warning in position_warnings:
            st.write(f"- **{aid}**: {warning}")

if totals.n_unvalued:
    st.warning(
        f"{totals.n_unvalued} position(s) could not be valued and are excluded from the "
        f"totals: {', '.join(totals.unvalued_ids)}"
    )

for problem in cash_problems:
    st.warning(f"Cash account skipped: {problem}")

# Money-weighted return over every cash flow ever logged, closed with today's
# value. Total PnL % is a ratio with no time in it, so it cannot be compared to a
# benchmark's annual return; this can.
mwr = pm.xirr(pm.build_cashflows(transactions, totals.market_value_eur, date.today()))

# --- Inflation -------------------------------------------------------------
# The HICP series does not reach the present day, so every figure derived from it
# is labelled with the month it actually covers. A stale rate presented as current
# would understate exactly the thing this section exists to measure.
hicp_index = md.fetch_hicp_index()
hicp_latest = md.fetch_hicp_annual_rate()
hicp_area = md.HICP_AREA_LABEL.get(md.HICP_AREA, md.HICP_AREA)
ref_month = max(hicp_index) if hicp_index else None
months_stale = (
    (date.today().year - int(ref_month[:4])) * 12 + date.today().month - int(ref_month[5:7])
    if ref_month
    else 0
)

real_positions: dict[str, pm.PositionState] = {}
uncovered_months: set[str] = set()
if ref_month:
    for aid, rows in txs_by_asset.items():
        adjusted, uncovered = pm.to_real_terms(rows, ref_month, hicp_index)
        real_positions[aid] = pm.build_position(adjusted)
        uncovered_months.update(m for m in uncovered if "no index" not in m)


def _pct_delta(pct: float | None) -> str | None:
    return f"{pct:+.2f}%" if pct is not None else None


# Each P&L figure is shown in euros with its percentage as the delta, and each
# percentage is against the cost it was actually earned on. Dividing everything by
# the open cost basis, as before, measured realised gains on capital already
# withdrawn against capital still at work.
r1 = st.columns(6)
r1[0].metric("Total Cost Basis (EUR)", f"€{totals.cost_basis_eur:,.0f}")
r1[1].metric(
    "Total Value (EUR)",
    f"€{totals.market_value_eur:,.0f}",
    help="The investments at market value: the base for every P&L figure. Dry Powder is in Portfolio value.",
)

dry_saved = max((a.updated for a in dry_powder if a.updated), default=None)
r1[2].metric(
    "Portfolio value (EUR)",
    f"€{portfolio_value:,.0f}",
    help=(
        f"Investments €{totals.market_value_eur:,.0f} plus Dry Powder €{dry_total:,.0f} "
        f"({len(dry_powder)} account(s), entered by hand on the transactions page"
        + (f", last saved {dry_saved}" if dry_saved else "")
        + "). The base for the weights, the charts and the portfolio design. "
        "Your cash reserve is not part of it."
    ),
)
r1[3].metric(
    "Dry Powder (EUR)",
    f"€{dry_total:,.0f}",
    help=(
        f"Money set aside to invest, across {len(dry_powder)} account(s), entered by hand on the "
        "transactions page" + (f", last saved {dry_saved}" if dry_saved else "") + ". Part of "
        "Portfolio value; not in Total Value or any P&L figure."
    ),
)
r1[4].metric(
    "Unrealised PnL (EUR)",
    f"€{totals.unrealised_pnl_eur:,.0f}",
    _pct_delta(totals.unrealised_pnl_pct),
    help=(
        "Market value of what you still hold against what it cost, fees included. "
        "The percentage is on that open cost basis."
    ),
)
r1[5].metric(
    "Realised PnL (EUR)",
    f"€{totals.realised_pnl_eur:,.0f}",
    _pct_delta(totals.realised_pnl_pct),
    help=(
        "Banked by selling, net of fees. The percentage is on the cost of what was "
        f"sold (€{totals.cost_released_eur:,.0f}), not on what you still hold."
    ),
)

r2 = st.columns(6)  # six, like the row above, so the two rows line up
r2[0].metric(
    "Total PnL (EUR)",
    f"€{totals.total_pnl_eur:,.0f}",
    _pct_delta(totals.pnl_pct),
    help=(
        "Unrealised plus realised. The percentage is on every euro of cost ever "
        f"deployed (€{totals.invested_eur:,.0f}: the open cost basis plus the cost "
        "of what has since been sold), so it is the cost-weighted blend of the two "
        "percentages above. It carries no time; the money-weighted return next to "
        "it does."
    ),
)

mwr_help = (
    "Money-weighted return (XIRR): the annual rate that turns your actual deposits, "
    "on their actual dates, into today's value. Total PnL % is a plain ratio — it "
    "treats a euro invested last month the same as one invested two years ago. "
    f"{mwr.note}."
)
if totals.n_unvalued:
    mwr_help += " Understated: some positions could not be valued."
r2[1].metric(
    "Money-Weighted Return",
    f"{mwr.rate * 100:.2f}%" if mwr.credible else "–",
    help=mwr_help,
)

if hicp_latest:
    rate, month = hicp_latest
    r2[2].metric(
        f"Inflation ({hicp_area} HICP)",
        f"{rate * 100:.1f}%",
        f"as of {month}",
        delta_color="off",
        help=(
            f"Annual change in the {hicp_area} Harmonised Index of Consumer Prices, "
            f"from the ECB. This is the most recent published figure — the series ends "
            f"at {month}, {months_stale} month(s) ago, so it is not a live number the "
            f"way the prices above are."
        ),
    )
    r2[3].metric(
        "Real Return",
        f"{pm.real_rate(mwr.rate, rate) * 100:.2f}%" if mwr.credible else "–",
        help=(
            "Money-weighted return with inflation stripped out, via the Fisher "
            "relation rather than simple subtraction. What your money actually gained "
            f"in purchasing power. Uses the {month} inflation rate; the months since "
            f"are not yet published, so the true figure is likely a little lower."
        ),
    )
else:
    r2[2].metric(f"Inflation ({hicp_area} HICP)", "–", help="Could not reach the ECB Data Portal.")
    r2[3].metric("Real Return", "–")

if drift:
    widest = max(drift, key=lambda d: abs(d.off_by_pp))
    r2[4].metric(
        "Largest gap vs design",
        f"{widest.off_by_pp:+.1f} pp",
        f"{widest.band} {'over' if widest.off_by_pp > 0 else 'under'}" if round(widest.off_by_pp, 1) else None,
        delta_color="off",
        help=(
            "The category furthest from your saved portfolio design, in percentage points of the "
            "portfolio value. Every category is in Allocation vs. design below."
        ),
    )
else:
    r2[4].metric(
        "Largest gap vs design",
        "–",
        help="No portfolio design saved yet. Set one on the Portfolio design page, under Analysis.",
    )

# The cash reserve, on its own line so it is never read as part of the figures above.
reserve_saved = max((a.updated for a in reserve if a.updated), default=None)
reserve_metric, reserve_note = st.columns([1, 4], vertical_alignment="center")
reserve_metric.metric(
    "Cash reserve (EUR)",
    f"€{reserve_total:,.0f}",
    help=(
        f"Cash in {len(reserve)} current account(s), entered by hand on the transactions page"
        + (f", last saved {reserve_saved}" if reserve_saved else "")
        + "."
    ),
)
reserve_note.caption(
    "Current accounts: your day-to-day cash and emergency fund. Outside the portfolio, so it is "
    "in no value, weight, chart or design on this page."
)

# ==========================================================================================================
# --- 1. ASSET BREAKDOWN ---
# ==========================================================================================================

st.subheader("Asset Breakdown")


def _row(aid):
    pos, val, cap = positions[aid], valuations[aid], capm[aid]
    real = real_positions.get(aid)
    real_basis = real.cost_basis_eur if real else None
    real_pnl = (
        val.market_value_eur - real_basis
        if real_basis is not None and val.market_value_eur is not None
        else None
    )
    return {
        "category": pos.category or "Unknown",
        "name": pos.name or aid,
        "ticker": pos.ticker or "",
        "isin": pos.isin or "",
        "Ccy": val.listing_ccy or "?",
        "Shares": pos.quantity,
        "Avg Cost (EUR)": pos.avg_cost_eur,
        "Curr Price (Nom)": val.price,
        "Cost Basis (EUR)": val.cost_basis_eur,
        "Real Cost Basis (EUR)": real_basis,
        "Market Value (EUR)": val.market_value_eur,
        "PnL (%)": val.unrealised_pnl_pct,
        "PnL (EUR)": val.unrealised_pnl_eur,
        "Real PnL (EUR)": real_pnl,
        "Realised (EUR)": pos.realised_pnl_eur,
        "Weight (%)": _weight(val.market_value_eur),
        "Beta": cap.beta,
        "R²": cap.r_squared,
        "n": cap.n_obs,
        "Alpha": cap.alpha_annual,
        "Note": val.error or "",
    }


def _sign_scaled_colours(series: pd.Series) -> list[str]:
    """Red shades for losses, green for gains, each scaled within its own sign.

    A single symmetric gradient across the column washes losses out: with one
    holding at +74%, a -8% loss lands almost dead centre of a -74..+74 scale and
    renders nearly white. Scaling each sign against its own extreme keeps every
    loss visibly red however large the biggest gain happens to be.
    """
    values = pd.to_numeric(series, errors="coerce")
    worst = abs(values[values < 0].min()) if (values < 0).any() else 0.0
    best = values[values > 0].max() if (values > 0).any() else 0.0

    styles = []
    for value in values:
        if pd.isna(value) or value == 0:
            styles.append("")
            continue
        if value < 0:
            share = abs(value) / worst if worst else 1.0
            rgb = "214, 39, 40"
        else:
            share = value / best if best else 1.0
            rgb = "44, 160, 44"
        # Floored so the mildest move is still unmistakably coloured.
        alpha = 0.18 + 0.62 * share
        text = " color: #ffffff;" if alpha > 0.55 else ""
        styles.append(f"background-color: rgba({rgb}, {alpha:.3f});{text}")
    return styles


def _size_scaled_colours(series: pd.Series) -> list[str]:
    """Blue shades scaled by size against the largest value: "how big", not good or bad."""
    values = pd.to_numeric(series, errors="coerce")
    largest = values[values > 0].max() if (values > 0).any() else 0.0

    styles = []
    for value in values:
        if pd.isna(value) or value <= 0:
            styles.append("")
            continue
        share = value / largest if largest else 1.0
        alpha = 0.18 + 0.62 * share
        text = " color: #ffffff;" if alpha > 0.55 else ""
        styles.append(f"background-color: rgba(31, 119, 180, {alpha:.3f});{text}")  # blue
    return styles


CAPM_HELP = {
    "Beta": (
        "How much this holding moves when the benchmark moves. 1.0 tracks the S&P 500 "
        "one-for-one, 0.5 moves half as much, negative moves the opposite way. "
        "Measured on EUR returns over 2 years."
    ),
    "R²": (
        "How much of this holding's movement the benchmark actually explains, from 0 to "
        "1. Low R² means Beta and Alpha describe a relationship that is barely there — "
        "for European or Japanese funds measured against the S&P 500, expect it to be low."
    ),
    "n": (
        "Daily observations behind Beta, Alpha and R². Below 60 the estimate is "
        "suppressed rather than shown, because a handful of days annualises into nonsense."
    ),
    "Alpha": (
        "Annualised return beyond what Beta alone would predict — the part not explained "
        "by simply riding the benchmark. Only meaningful when R² is high enough for Beta "
        "to mean anything in the first place."
    ),
    "Weight (%)": (
        "Share of the portfolio: investments at market value plus Dry Powder. "
        "Your cash reserve is outside it."
    ),
    "PnL (%)": "Unrealised gain or loss on what you still hold, against its cost basis.",
    "PnL (EUR)": "Unrealised gain or loss in euros on what you still hold.",
    "Realised (EUR)": "Gain or loss already banked by selling. Zero until you sell.",
    "Note": "Why a position could not be valued. Empty means it valued cleanly.",
    "Real Cost Basis (EUR)": (
        "What you paid, restated into the purchasing power of the most recent month "
        "the inflation index covers. Higher than the nominal cost basis because those "
        "euros bought more back then."
    ),
    "Real PnL (EUR)": (
        "Gain or loss after inflation — market value against the restated cost basis. "
        "A position that merely kept pace with inflation shows near zero here while "
        "still showing a nominal profit."
    ),
}


if open_ids:
    ordered = sorted(
        open_ids, key=lambda a: valuations[a].market_value_eur or -1, reverse=True
    )
    # Dry Powder accounts follow the investments; the cash reserve is not part of the
    # portfolio and is not listed. Only the columns that mean something for a bank
    # balance are filled; Streamlit draws the empty numeric cells with its own grey
    # placeholder. The text columns get "" like an investment without a ticker, and
    # Note is set so an empty value does not count as a note.
    cash_rows = [
        {
            "category": a.category,
            "name": a.name,
            "ticker": "",
            "isin": "",
            "Ccy": pm.BASE_CCY,
            "Market Value (EUR)": a.balance_eur,
            "Weight (%)": _weight(a.balance_eur),
            "Note": "",
        }
        for a in sorted(dry_powder, key=lambda a: a.balance_eur, reverse=True)
    ]
    summary = pd.DataFrame([_row(aid) for aid in ordered] + cash_rows)

    # The Note column is pure noise when nothing is wrong, which is the normal case.
    if not summary["Note"].astype(bool).any():
        summary = summary.drop(columns=["Note"])

    st.dataframe(
        summary.style.format(
            {
                "Shares": "{:,.4f}",
                "Avg Cost (EUR)": "€ {:,.2f}",
                "Curr Price (Nom)": "{:,.2f}",
                "Cost Basis (EUR)": "€ {:,.2f}",
                "Real Cost Basis (EUR)": "€ {:,.2f}",
                "Market Value (EUR)": "€ {:,.2f}",
                "PnL (%)": "{:.1f} %",
                "PnL (EUR)": "€ {:,.2f}",
                "Real PnL (EUR)": "€ {:,.2f}",
                "Realised (EUR)": "€ {:,.2f}",
                "Weight (%)": "{:.1f} %",
                "Beta": "{:.2f}",
                "R²": "{:.2f}",
                "n": "{:.0f}",  # float once a cash row leaves it empty
                "Alpha": "{:+.1%}",
            },
            na_rep="–",
        ).apply(
            _sign_scaled_colours,
            subset=[
                c
                for c in ("PnL (%)", "PnL (EUR)", "Real PnL (EUR)", "Realised (EUR)")
                if c in summary
            ],
        ).apply(
            _size_scaled_colours,
            subset=[
                c for c in ("Market Value (EUR)", "Weight (%)") if c in summary
            ],
        ),
        # Alpha deliberately has no colour scale: a red/green ramp reads as a
        # finding, and an alpha estimate is far noisier than a measured P&L.
        column_config={
            col: st.column_config.Column(col, help=text)
            for col, text in CAPM_HELP.items()
            if col in summary.columns
        },
        width="stretch",
        hide_index=True,
    )

    weak = [
        f"{positions[a].name or a} ({capm[a].note})" for a in ordered if not capm[a].credible
    ]
    caption = (
        f"Beta/Alpha vs {md.BENCHMARK_TICKER} in EUR over {md.CAPM_PERIOD}, "
        f"risk-free {rf_annual:.1%}. R² is how much of the asset's movement the "
        f"benchmark actually explains — a low R² means Beta and Alpha carry little meaning."
    )
    if weak:
        caption += "  \nNot estimated: " + "; ".join(weak)
    st.caption(caption)

    if ref_month:
        inflation_note = (
            f"Real figures restate cost into **{ref_month}** purchasing power using "
            f"{hicp_area} HICP — the latest month the index covers. Inflation over the "
            f"{months_stale} month(s) since is not yet published, so real gains here are "
            f"flattered by that much."
        )
        if uncovered_months:
            inflation_note += (
                f"  \nNot adjusted at all (bought after the index ends): "
                f"{', '.join(sorted(uncovered_months))}."
            )
        st.caption(inflation_note)
else:
    st.info("No open positions.")

if closed_ids:
    st.subheader("Closed Positions")
    closed = pd.DataFrame(
        [
            {
                "name": positions[aid].name or aid,
                "isin": positions[aid].isin or "",
                "ticker": positions[aid].ticker or "",
                "Bought": positions[aid].qty_bought_lifetime,
                "Sold": positions[aid].qty_sold_lifetime,
                "Realised PnL (EUR)": positions[aid].realised_pnl_eur,
                "Last trade": positions[aid].last_trade_date,
            }
            for aid in closed_ids
        ]
    )
    st.dataframe(
        closed.style.format(
            {"Bought": "{:,.4f}", "Sold": "{:,.4f}", "Realised PnL (EUR)": "€ {:,.2f}"}
        ),
        width="stretch",
        hide_index=True,
    )

# ==========================================================================================================
# --- ALLOCATION VS. DESIGN ---
# ==========================================================================================================

st.markdown("---")
st.subheader("Allocation vs. design")
for problem in design_problems:
    st.warning(f"Saved design ignored: {problem}")

if drift:
    gaps = pd.DataFrame(
        [
            {
                "Category": d.band,
                "Today (%)": d.current_pct,
                "Design (%)": d.design_pct,
                "Off by (pp)": d.off_by_pp,
                "To reach design (EUR)": d.to_design_eur,
            }
            for d in drift
        ]
    )
    st.dataframe(
        gaps.style.format(
            {
                "Today (%)": "{:.1f} %",
                "Design (%)": "{:.0f} %",
                "Off by (pp)": "{:+.1f}",
                "To reach design (EUR)": "€ {:+,.0f}",
            }
        )
        # Shaded by size, not red and green: being over or under a design is neither a
        # gain nor a loss, just a distance.
        .apply(lambda s: _size_scaled_colours(s.abs()), subset=["Off by (pp)"]),
        column_config={
            "Off by (pp)": st.column_config.Column(
                "Off by (pp)",
                help="Today minus design, in percentage points of the portfolio: positive is over the design.",
            ),
            "To reach design (EUR)": st.column_config.Column(
                "To reach design (EUR)",
                help=(
                    "Euros to add (+) or take out (−) of the category to match the design at "
                    "today's portfolio value. The column adds up to zero."
                ),
            ),
        },
        hide_index=True,
    )
    st.caption(
        f"Against the design saved {design_doc.get('saved') or 'on an unknown date'}, as a share of "
        f"the portfolio (€{portfolio_value:,.0f}: investments plus Dry Powder). Your cash reserve "
        f"is outside it."
    )
else:
    st.info("No portfolio design saved yet.")
st.page_link("pages/design.py", label="Open Portfolio design", icon="🧭")

# ==========================================================================================================
# --- 2. ALLOCATION PIE CHARTS ---
# ==========================================================================================================

if open_ids:
    st.markdown("---")
    st.subheader("Portfolio Diversification")

    pie_df = pd.DataFrame(
        [
            {
                "category": positions[aid].category or "Unknown",
                "name": positions[aid].name or aid,
                "Market Value (EUR)": valuations[aid].market_value_eur,
            }
            for aid in open_ids
            if valuations[aid].market_value_eur
        ]
        + [
            {"category": a.category, "name": a.name, "Market Value (EUR)": a.balance_eur}
            for a in dry_powder  # the cash reserve is not part of the portfolio
            if a.balance_eur > 0
        ]
    )

    if not pie_df.empty:
        col_left, col_right = st.columns(2)
        with col_left:
            st.write("**By Category**")
            st.plotly_chart(
                charts.readable(
                    px.pie(
                        pie_df.groupby("category", as_index=False)["Market Value (EUR)"].sum(),
                        values="Market Value (EUR)",
                        names="category",
                        hole=0.4,
                        color_discrete_sequence=px.colors.qualitative.Prism,
                    )
                ),
                width="stretch",
            )
        with col_right:
            st.write("**By Asset Name**")
            st.plotly_chart(
                charts.readable(
                    px.pie(
                        pie_df.groupby("name", as_index=False)["Market Value (EUR)"].sum(),
                        values="Market Value (EUR)",
                        names="name",
                        hole=0.4,
                        color_discrete_sequence=px.colors.qualitative.Pastel,
                    )
                ),
                width="stretch",
            )

# ==========================================================================================================
# --- 3. ASSET PERFORMANCE HISTORIES ---
# ==========================================================================================================

if open_ids:
    st.markdown("---")
    st.subheader("Asset Performance & Transaction History")

    time_options = {
        "6 Months": "6mo",
        "1 Year": "1y",
        "3 Years": "3y",
        "5 Years": "5y",
        "10 Years": "10y",
        "All Time": "max",
    }
    range_col, layout_col, _ = st.columns([1, 1, 2], vertical_alignment="bottom")
    selected_label = range_col.selectbox("Select Time Range", options=list(time_options.keys()), index=2)
    selected_period = time_options[selected_label]
    # Kept in the URL (?charts_per_row=3), so a reload or a bookmark keeps the choice.
    per_row = layout_col.segmented_control(
        "Charts per row", (1, 2, 3), default=2, required=True, key="charts_per_row", bind="query-params"
    )
    if ref_month:
        st.caption(
            f"Dashed line: average cost. Dotted line: inflation break-even, the average cost carried "
            f"forward with {hicp_area} HICP to {ref_month}; a price above it has kept its purchasing power."
        )

    largest_first = sorted(open_ids, key=lambda a: valuations[a].market_value_eur or -1, reverse=True)
    for i, aid in enumerate(largest_first):
        if i % per_row == 0:
            cells = st.columns(per_row)  # a row per group, so neighbours line up at the top

        pos, val = positions[aid], valuations[aid]
        symbol = price_symbol[aid]
        # The name you typed when logging. Yahoo's name is not fetched here: it
        # comes from the slow quote-summary endpoint, and for funds it is an
        # internal code like 0P0001EI1P.F anyway.
        official = pos.name or aid
        listing = val.listing_ccy  # None when unknown; no cost line is drawn then
        ccy = listing or "unknown currency"
        sym = CURRENCY_SYMBOL.get(listing, "")

        # The chart has no title, which half a row could not fit: this label names it.
        heading = official if official == symbol else f"{official} ({symbol})"
        with cells[i % per_row].expander(f"📈 {heading}", expanded=True):
            # Unadjusted, so the series is on the same scale as the raw trade prices
            # plotted on top of it. An adjusted series would sit below them and drift
            # further apart with every dividend, and jump by the split ratio.
            hist = md.fetch_price_history((symbol,), selected_period, adjusted=False)
            if hist.empty or symbol not in hist.columns:
                st.error(f"Could not load historical data for {symbol}")
                continue

            plot_df = hist[[symbol]].rename(columns={symbol: "Close"}).reset_index()
            date_col = plot_df.columns[0]

            fig = px.line(
                plot_df,
                x=date_col,
                y="Close",
                labels={"Close": f"Unadjusted close ({ccy})", date_col: "Timeline"},
                template="plotly_white",
            )

            asset_txs = txs_by_asset.get(aid, [])
            for action, colour, symbol_shape, edge in (
                ("Buy", "#2ECC71", "triangle-up", "DarkGreen"),
                ("Sell", "#E74C3C", "triangle-down", "DarkRed"),
            ):
                rows = [t for t in asset_txs if t.action == action]
                if not rows:
                    continue
                fig.add_scatter(
                    x=[t.trade_date for t in rows],
                    y=[t.price_nominal for t in rows],
                    mode="markers",
                    name=action,
                    marker=dict(
                        size=12,
                        color=colour,
                        symbol=symbol_shape,
                        line=dict(width=2, color=edge),
                    ),
                    hovertemplate=(
                        f"<b>{action.upper()}</b><br>Date: %{{x}}<br>"
                        f"Price: {sym}%{{y:.2f}}<extra></extra>"
                    ),
                )

            # The cost basis is held in EUR; show it in the chart's currency. Skipped
            # when the listing currency is unknown: a EUR line under a price in some
            # other currency is a wrong line, not a fallback.
            try:
                avg_in_chart_ccy = (
                    pm.from_base(pos.avg_cost_eur, listing, rates) if listing else None
                )
            except pm.MissingRate:
                avg_in_chart_ccy = None

            # Inflation hurdle: where the price must be to have merely preserved
            # purchasing power. Between the two lines is nominal profit that
            # bought nothing extra.
            hurdle = None
            real_pos = real_positions.get(aid)
            if avg_in_chart_ccy and real_pos and real_pos.cost_basis_eur > pos.cost_basis_eur:
                try:
                    hurdle = pm.from_base(real_pos.avg_cost_eur, listing, rates)
                except pm.MissingRate:
                    pass
            # Each label on the far side of its line from the other line, so two
            # lines close together never stack their labels in the gap between them.
            hurdle_above = bool(hurdle) and hurdle > avg_in_chart_ccy

            if avg_in_chart_ccy:
                label = f"Avg cost: {sym}{avg_in_chart_ccy:,.2f}"
                if listing != pm.BASE_CCY:
                    label += f" (€{pos.avg_cost_eur:,.2f} at today's FX)"
                fig.add_hline(
                    y=avg_in_chart_ccy,
                    line_dash="dash",
                    line_color="rgba(46, 204, 113, 0.7)",
                    annotation_text=label,
                    annotation_position="bottom left" if hurdle_above else "top left",
                )
            if hurdle:
                fig.add_hline(
                    y=hurdle,
                    line_dash="dot",
                    line_color="rgba(230, 126, 34, 0.9)",
                    annotation_text=f"Inflation break-even: {sym}{hurdle:,.2f}",  # HICP month in the caption
                    annotation_position="top left" if hurdle_above else "bottom left",
                )

            # The legend in a row above the plot, so it takes no width from a chart in a narrow column.
            fig.update_layout(
                showlegend=True, hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            )
            st.plotly_chart(charts.readable(fig), width="stretch")
