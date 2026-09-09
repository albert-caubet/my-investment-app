from datetime import date

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import market_data as md
import portfolio_math as pm
from database import get_all_transactions

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

listing_info = md.fetch_listing_info(symbols)

# Live metadata first, the currency detected when the trade was logged as the
# fallback. Never EUR by default: Yahoo's metadata endpoint fails routinely and the
# failure is cached for a day, so a default would value a USD price one-for-one as
# euros -- the same silent error as an FX fallback of 1.0.
stored_ccy: dict[str, str] = {}
for aid, pos in positions.items():
    if pos.listing_ccy:
        stored_ccy.setdefault(price_symbol[aid], pos.listing_ccy)
listing_ccy: dict[str, str | None] = {
    sym: listing_info.get(sym, {}).get("currency") or stored_ccy.get(sym) for sym in symbols
}

spot = md.fetch_spot_prices(symbols)
rates = md.fetch_spot_fx(tuple(c for c in listing_ccy.values() if c))

quotes = {
    aid: pm.Quote(
        asset_id=aid,
        price=spot.get(price_symbol[aid]),
        listing_ccy=listing_ccy.get(price_symbol[aid]),
    )
    for aid in positions
}

valuations = {aid: pm.value_position(pos, quotes[aid], rates) for aid, pos in positions.items()}
open_ids = [aid for aid, pos in positions.items() if pos.is_open]
closed_ids = [aid for aid, pos in positions.items() if not pos.is_open]

totals = pm.portfolio_totals([valuations[aid] for aid in positions])
asset_weights = pm.weights([valuations[aid] for aid in open_ids])

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
r1 = st.columns(4)
r1[0].metric("Total Cost Basis (EUR)", f"€{totals.cost_basis_eur:,.0f}")
r1[1].metric("Total Value (EUR)", f"€{totals.market_value_eur:,.0f}")
r1[2].metric(
    "Unrealised PnL (EUR)",
    f"€{totals.unrealised_pnl_eur:,.0f}",
    _pct_delta(totals.unrealised_pnl_pct),
    help=(
        "Market value of what you still hold against what it cost, fees included. "
        "The percentage is on that open cost basis."
    ),
)
r1[3].metric(
    "Realised PnL (EUR)",
    f"€{totals.realised_pnl_eur:,.0f}",
    _pct_delta(totals.realised_pnl_pct),
    help=(
        "Banked by selling, net of fees. The percentage is on the cost of what was "
        f"sold (€{totals.cost_released_eur:,.0f}), not on what you still hold."
    ),
)

r2 = st.columns(4)
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
        "Weight (%)": asset_weights.get(aid),
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


def _warm_scaled_colours(series: pd.Series) -> list[str]:
    """Warm tan shades scaled independently from the largest value."""
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
        styles.append(f"background-color: rgba(181, 126, 71, {alpha:.3f});{text}")
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
    summary = pd.DataFrame([_row(aid) for aid in ordered])

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
            _warm_scaled_colours,
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
    )

    if not pie_df.empty:
        col_left, col_right = st.columns(2)
        with col_left:
            st.write("**By Category**")
            st.plotly_chart(
                px.pie(
                    pie_df.groupby("category", as_index=False)["Market Value (EUR)"].sum(),
                    values="Market Value (EUR)",
                    names="category",
                    hole=0.4,
                    color_discrete_sequence=px.colors.qualitative.Prism,
                ),
                width="stretch",
            )
        with col_right:
            st.write("**By Asset Name**")
            st.plotly_chart(
                px.pie(
                    pie_df.groupby("name", as_index=False)["Market Value (EUR)"].sum(),
                    values="Market Value (EUR)",
                    names="name",
                    hole=0.4,
                    color_discrete_sequence=px.colors.qualitative.Pastel,
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
    selected_label = st.selectbox("Select Time Range", options=list(time_options.keys()), index=2)
    selected_period = time_options[selected_label]

    for aid in sorted(open_ids, key=lambda a: valuations[a].market_value_eur or -1, reverse=True):
        pos, val = positions[aid], valuations[aid]
        symbol = price_symbol[aid]
        # Your own label first: for funds, Yahoo's "name" is an internal code like
        # 0P0001EI1P.F, which is less useful than what you typed when logging.
        official = pos.name or listing_info.get(symbol, {}).get("name") or aid
        listing = val.listing_ccy  # None when unknown; no cost line is drawn then
        ccy = listing or "unknown currency"
        sym = CURRENCY_SYMBOL.get(listing, "")

        with st.expander(f"📈 {pos.name or aid}", expanded=True):
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
                title=f"{official} ({ccy}, {symbol}) — {selected_label}, unadjusted close",
                labels={"Close": f"Price ({ccy})", date_col: "Timeline"},
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

            if avg_in_chart_ccy:
                label = f"Avg cost: {sym}{avg_in_chart_ccy:,.2f}"
                if listing != pm.BASE_CCY:
                    label += f" (€{pos.avg_cost_eur:,.2f} at today's FX)"
                fig.add_hline(
                    y=avg_in_chart_ccy,
                    line_dash="dash",
                    line_color="rgba(46, 204, 113, 0.7)",
                    annotation_text=label,
                    annotation_position="top left",
                )

                # Inflation hurdle: where the price must be to have merely preserved
                # purchasing power. Between the two lines is nominal profit that
                # bought nothing extra.
                real_pos = real_positions.get(aid)
                if real_pos and real_pos.cost_basis_eur > pos.cost_basis_eur:
                    try:
                        hurdle = pm.from_base(real_pos.avg_cost_eur, listing, rates)
                    except pm.MissingRate:
                        hurdle = None
                    if hurdle:
                        fig.add_hline(
                            y=hurdle,
                            line_dash="dot",
                            line_color="rgba(230, 126, 34, 0.9)",
                            annotation_text=(
                                f"Break-even after inflation: {sym}{hurdle:,.2f} "
                                f"(to {ref_month})"
                            ),
                            annotation_position="bottom left",
                        )

            fig.update_layout(showlegend=True, hovermode="x unified")
            st.plotly_chart(fig, width="stretch")
