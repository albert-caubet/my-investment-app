from datetime import date

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import market_data as md
import portfolio_math as pm
from database import get_all_transactions

CURRENCY_SYMBOL = {"EUR": "€", "USD": "$"}

st.set_page_config(layout="wide", page_title="My Portfolio")

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

# The pricing symbol is not the identity: assets are keyed by ISIN so that grouping
# stays stable, but a resolved ticker is the better thing to send to Yahoo.
price_symbol = {aid: pos.price_symbol for aid, pos in positions.items()}
symbols = tuple(sorted(set(price_symbol.values())))

# ==========================================================================================================
# 2. MARKET DATA
# ==========================================================================================================

listing_info = md.fetch_listing_info(symbols)
listing_ccy = {sym: (listing_info.get(sym, {}).get("currency") or pm.BASE_CCY) for sym in symbols}

spot = md.fetch_spot_prices(symbols)
rates = md.fetch_spot_fx(tuple(listing_ccy.values()))

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
        fx_hist = md.fetch_fx_history(tuple(capm_ccy.values()), md.CAPM_PERIOD)
        eur_panel = md.to_eur_panel(hist, capm_ccy, fx_hist)
        returns = eur_panel.pct_change()

        for aid in positions:
            column = price_symbol[aid]
            if column not in returns.columns:
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

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Total Cost Basis (EUR)", f"€{totals.cost_basis_eur:,.0f}")
m2.metric("Total Value (EUR)", f"€{totals.market_value_eur:,.0f}")
m3.metric("Unrealised PnL (EUR)", f"€{totals.unrealised_pnl_eur:,.0f}")
m4.metric("Realised PnL (EUR)", f"€{totals.realised_pnl_eur:,.0f}")
m5.metric(
    "Total PnL (EUR)",
    f"€{totals.total_pnl_eur:,.0f}",
    f"{totals.pnl_pct:.2f}%" if totals.pnl_pct is not None else None,
)

mwr_help = (
    "Money-weighted return (XIRR): the annual rate that turns your actual deposits, "
    "on their actual dates, into today's value. Total PnL % is a plain ratio — it "
    "treats a euro invested last month the same as one invested two years ago. "
    f"{mwr.note}."
)
if totals.n_unvalued:
    mwr_help += " Understated: some positions could not be valued."
m6.metric(
    "Money-Weighted Return",
    f"{mwr.rate * 100:.2f}%" if mwr.credible else "–",
    help=mwr_help,
)

# ==========================================================================================================
# --- 1. ASSET BREAKDOWN ---
# ==========================================================================================================

st.subheader("Asset Breakdown")


def _row(aid):
    pos, val, cap = positions[aid], valuations[aid], capm[aid]
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
        "Market Value (EUR)": val.market_value_eur,
        "PnL (%)": val.unrealised_pnl_pct,
        "PnL (EUR)": val.unrealised_pnl_eur,
        "Realised (EUR)": pos.realised_pnl_eur,
        "Weight (%)": asset_weights.get(aid),
        "Beta": cap.beta,
        "R²": cap.r_squared,
        "n": cap.n_obs,
        "Alpha": cap.alpha_annual,
        "Note": val.error or "",
    }


if open_ids:
    ordered = sorted(
        open_ids, key=lambda a: valuations[a].market_value_eur or -1, reverse=True
    )
    summary = pd.DataFrame([_row(aid) for aid in ordered])

    pnl_series = summary["PnL (%)"].dropna()
    pnl_limit = max(abs(pnl_series.min()), abs(pnl_series.max()), 0.1) if len(pnl_series) else 0.1

    st.dataframe(
        summary.style.format(
            {
                "Shares": "{:,.4f}",
                "Avg Cost (EUR)": "€ {:,.2f}",
                "Curr Price (Nom)": "{:,.2f}",
                "Cost Basis (EUR)": "€ {:,.2f}",
                "Market Value (EUR)": "€ {:,.2f}",
                "PnL (%)": "{:.1f} %",
                "PnL (EUR)": "€ {:,.2f}",
                "Realised (EUR)": "€ {:,.2f}",
                "Weight (%)": "{:.1f} %",
                "Beta": "{:.2f}",
                "R²": "{:.2f}",
                "Alpha": "{:+.1%}",
            },
            na_rep="–",
        ).background_gradient(
            subset=["PnL (%)"], cmap="RdYlGn", vmin=-pnl_limit, vmax=pnl_limit
        ),
        # Alpha deliberately has no colour gradient: a red/green ramp reads as a
        # finding, and an alpha estimate is far noisier than a measured P&L.
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

    txs_by_asset: dict[str, list] = {}
    for tx in transactions:
        txs_by_asset.setdefault(tx.asset_id, []).append(tx)

    for aid in sorted(open_ids, key=lambda a: valuations[a].market_value_eur or -1, reverse=True):
        pos, val = positions[aid], valuations[aid]
        symbol = price_symbol[aid]
        # Your own label first: for funds, Yahoo's "name" is an internal code like
        # 0P0001EI1P.F, which is less useful than what you typed when logging.
        official = pos.name or listing_info.get(symbol, {}).get("name") or aid
        ccy = val.listing_ccy or pm.BASE_CCY
        sym = CURRENCY_SYMBOL.get(ccy, "")

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

            # The cost basis is held in EUR; show it in the chart's currency.
            try:
                avg_in_chart_ccy = pm.from_base(pos.avg_cost_eur, ccy, rates)
            except pm.MissingRate:
                avg_in_chart_ccy = None

            if avg_in_chart_ccy:
                label = f"Avg cost: {sym}{avg_in_chart_ccy:,.2f}"
                if ccy != pm.BASE_CCY:
                    label += f" (€{pos.avg_cost_eur:,.2f} at today's FX)"
                fig.add_hline(
                    y=avg_in_chart_ccy,
                    line_dash="dash",
                    line_color="rgba(46, 204, 113, 0.7)",
                    annotation_text=label,
                    annotation_position="top left",
                )

            fig.update_layout(showlegend=True, hovermode="x unified")
            st.plotly_chart(fig, width="stretch")
