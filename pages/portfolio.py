import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.express as px
import numpy as np
from database import get_all_transactions

st.set_page_config(layout="wide", page_title="My Portfolio")

raw_data = get_all_transactions()

if not raw_data:
    st.title("Current Portfolio")
    st.warning("No transactions found.")
else:
    df = pd.DataFrame(raw_data)

    # Defensive columns
    for col in ["ticker", "isin", "quantity", "action", "category", "currency", "name"]:
        if col not in df.columns:
            df[col] = "Unknown" if col != "quantity" else 0

    df['id'] = df['ticker'].replace("", None).fillna(df['isin']).fillna("Unknown")
    df['adj_qty'] = df.apply(lambda x: x['quantity'] if x['action'] == 'Buy' else -x['quantity'], axis=1)


    # 1. PRE-CALCULATE AVG BUY PRICE (Weighted)
    def calc_avg_price(group):
        buys = group[group['action'] == 'Buy']
        if buys.empty: return 0
        return (buys['quantity'] * buys['price']).sum() / buys['quantity'].sum()


    avg_prices = df.groupby('id').apply(calc_avg_price, include_groups=False).to_dict()

    # 2. AGGREGATE SUMMARY
    summary = df.groupby("id").agg({
        "adj_qty": "sum",
        "category": "first",
        "name": "first",
        "ticker": "first",
        "isin": "first",
        "currency": "first"
    }).reset_index()

    summary.rename(columns={"adj_qty": "Total Shares", "id": "Asset"}, inplace=True)
    summary = summary[summary["Total Shares"] > 0]

    if not summary.empty:
        try:
            # 1. FETCH DATA (Assets + Benchmark + FX + 10Y Treasury)
            asset_list = summary["Asset"].tolist()
            benchmark_ticker = "^GSPC"  # S&P 500
            rf_ticker = "^TNX"  # 10-Year Treasury Yield
            all_tickers = asset_list + [benchmark_ticker, rf_ticker, "EURUSD=X"]

            # Fetch 1 year of daily data
            market_data = yf.download(all_tickers, period="1y", progress=False)['Close'].ffill()

            # Current Prices & FX
            current_prices = market_data.iloc[-1]
            eur_usd = float(market_data["EURUSD=X"].iloc[-1])

            # Risk-Free Rate: ^TNX returns the yield as a percentage (e.g., 4.25)
            # We divide by 100 to get the decimal (0.0425)
            current_rf_annual = float(market_data["^TNX"].iloc[-1]) / 100
            # Daily risk-free rate (approximate)
            rf_daily = current_rf_annual / 252

            # 2. CALCULATE PERFORMANCE COLUMNS
            summary["Avg Buy Price"] = summary["Asset"].map(avg_prices)
            summary["Current Price"] = summary["Asset"].map(current_prices)


            def convert_to_eur(row, price_col):
                if row['currency'] == 'USD':
                    return row[price_col] / eur_usd
                return row[price_col]


            summary["Current Price (EUR)"] = summary.apply(lambda x: convert_to_eur(x, "Current Price"), axis=1)
            summary["Avg Buy Price (EUR)"] = summary.apply(lambda x: convert_to_eur(x, "Avg Buy Price"), axis=1)
            summary["Market Value (EUR)"] = summary["Total Shares"] * summary["Current Price (EUR)"]
            summary["PnL (EUR)"] = (summary["Current Price (EUR)"] - summary["Avg Buy Price (EUR)"]) * summary[
                "Total Shares"]
            summary["PnL (%)"] = (summary["Current Price (EUR)"] / summary["Avg Buy Price (EUR)"] - 1) * 100

            total_port_value = summary["Market Value (EUR)"].sum()
            summary["Weight (%)"] = (summary["Market Value (EUR)"] / total_port_value) * 100

            # 3. CALCULATE ALPHA & BETA (CAPM Model)
            returns = market_data.pct_change().dropna()
            betas = {}
            alphas = {}

            for ticker in asset_list:
                try:
                    # Excess returns (Asset - Rf and Market - Rf)
                    asset_excess = returns[ticker] - rf_daily
                    market_excess = returns[benchmark_ticker] - rf_daily

                    # Beta = Covariance(Asset, Market) / Variance(Market)
                    # We use excess returns for a more professional calculation
                    covariance = np.cov(asset_excess, market_excess)[0, 1]
                    variance = np.var(market_excess)
                    beta = covariance / variance
                    betas[ticker] = beta

                    # Jensen's Alpha (Annualized)
                    # Alpha = (Asset Annual Return - Rf) - Beta * (Market Annual Return - Rf)
                    asset_ann_ret = returns[ticker].mean() * 252
                    market_ann_ret = returns[benchmark_ticker].mean() * 252

                    alpha = (asset_ann_ret - current_rf_annual) - beta * (market_ann_ret - current_rf_annual)
                    alphas[ticker] = alpha
                except:
                    betas[ticker], alphas[ticker] = 0, 0

            summary["Beta"] = summary["Asset"].map(betas)
            summary["Alpha"] = summary["Asset"].map(alphas)

            # --- DISPLAY DASHBOARD ---
            st.title("Portfolio Dashboard")

            # Top Metrics
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Value (EUR)", f"€{total_port_value:,.2f}")

            total_pnl_eur = summary["PnL (EUR)"].sum()
            m2.metric("Total PnL (EUR)", f"€{total_pnl_eur:,.2f}", f"{(total_pnl_eur / total_port_value) * 100:.2f}%")

            m3.metric("Risk-Free Rate (10Y)", f"{current_rf_annual * 100:.2f}%")

            # REFINED TABLE
            st.subheader("Asset Breakdown")
            display_cols = [
                "category", "name", "ticker", "isin", "Avg Buy Price (EUR)",
                "Current Price (EUR)", "Market Value (EUR)", "PnL (%)",
                "PnL (EUR)", "Weight (%)", "Beta", "Alpha"
            ]

            st.dataframe(
                summary[display_cols].style.format({
                    "Avg Buy Price (EUR)": "€{:.2f}",
                    "Current Price (EUR)": "€{:.2f}",
                    "Market Value (EUR)": "€{:,.2f}",
                    "PnL (%)": "{:.2f}%",
                    "PnL (EUR)": "€{:,.2f}",
                    "Weight (%)": "{:.2f}%",
                    "Beta": "{:.2f}",
                    "Alpha": "{:.4f}"
                }).background_gradient(subset=['PnL (%)'], cmap='RdYlGn')
                .background_gradient(subset=['Alpha'], cmap='RdYlGn'),
                width='stretch', hide_index=True
            )

            # --- 2. ALLOCATION PIE CHART ---
            st.markdown("---")
            st.subheader("Portfolio Diversification")

            # Create two equal-width columns
            col_left, col_right = st.columns(2)

            with col_left:
                st.write("**By Category**")
                cat_data = summary.groupby("category")["Market Value (EUR)"].sum().reset_index()
                fig_cat = px.pie(
                    cat_data,
                    values='Market Value (EUR)',
                    names='category',
                    hole=0.4,
                    color_discrete_sequence=px.colors.qualitative.Prism
                )
                # This 'width' parameter is what makes it fit inside the column
                st.plotly_chart(fig_cat, width='stretch')

            with col_right:
                st.write("**By Asset Name**")
                # Group by Name/Ticker
                name_data = summary.groupby("name")["Market Value (EUR)"].sum().reset_index()
                fig_name = px.pie(
                    name_data,
                    values='Market Value (EUR)',
                    names='name',
                    hole=0.4,
                    color_discrete_sequence=px.colors.qualitative.Pastel
                )
                # Again, 'stretch' ensures it respects the column boundary
                st.plotly_chart(fig_name, width='stretch')

            # with col_stats:
            #     st.subheader("Concentration Risk")
            #     # Find your most heavily weighted asset
            #     top_asset = summary.loc[summary["Weight (%)"].idxmax()]
            #     st.write(f"**Largest Holding:** {top_asset['name'] or top_asset['Asset']}")
            #     st.progress(top_asset["Weight (%)"] / 100)
            #     st.write(f"This asset represents **{top_asset['Weight (%)']:.2f}%** of your total portfolio.")
            #     # Display total count
            #     st.write(f"**Total Positions:** {len(summary)}")

            # --- 3. ALL ASSET PERFORMANCE HISTORIES ---
            st.markdown("---")
            st.subheader("Asset Performance & Transaction History")

            # We fetch all historical data in one go if possible, or iterate
            for asset in asset_list:
                with st.expander(f"📈 {asset} Detail Analysis", expanded=True):
                    # Fetch 1y history
                    hist_data = yf.download(asset, period="1y", progress=False)

                    if not hist_data.empty:
                        # Flatten columns and reset index for Plotly
                        if isinstance(hist_data.columns, pd.MultiIndex):
                            hist_data.columns = hist_data.columns.get_level_values(0)
                        hist_plot_df = hist_data.reset_index()

                        # 1. Create the Base Line Chart
                        fig = px.line(
                            hist_plot_df,
                            x="Date",
                            y="Close",
                            title=f"{asset} Historical Price (vs. Transactions)",
                            labels={"Close": "Price ($)", "Date": "Timeline"},
                            template="plotly_white"
                        )

                        # 2. Add "Buy" and "Sell" markers from your Firestore 'df'
                        # Filter transactions for THIS specific asset
                        asset_txs = df[df['id'] == asset].copy()
                        # Ensure date is in datetime format for alignment with x-axis
                        asset_txs['date'] = pd.to_datetime(asset_txs['date'])

                        # Add Buy Markers (Green Up-Arrows)
                        buys = asset_txs[asset_txs['action'] == 'Buy']
                        if not buys.empty:
                            fig.add_trace(px.scatter(
                                buys, x='date', y='price',
                                color_discrete_sequence=['#2ECC71']
                            ).data[0].update(
                                name="Buy",
                                marker=dict(size=12, symbol='triangle-up', line=dict(width=2, color='DarkGreen')),
                                hovertemplate="<b>BUY</b><br>Date: %{x}<br>Price: $%{y:.2f}"
                            ))

                        # Add Sell Markers (Red Down-Arrows)
                        sells = asset_txs[asset_txs['action'] == 'Sell']
                        if not sells.empty:
                            fig.add_trace(px.scatter(
                                sells, x='date', y='price',
                                color_discrete_sequence=['#E74C3C']
                            ).data[0].update(
                                name="Sell",
                                marker=dict(size=12, symbol='triangle-down', line=dict(width=2, color='DarkRed')),
                                hovertemplate="<b>SELL</b><br>Date: %{x}<br>Price: $%{y:.2f}"
                            ))

                        # 3. Add the Average Cost Basis Line (Break-even)
                        asset_summary = summary[summary['Asset'] == asset].iloc[0]
                        avg_price = asset_summary['Avg Buy Price']

                        fig.add_hline(
                            y=avg_price,
                            line_dash="dash",
                            line_color="rgba(46, 204, 113, 0.5)",
                            annotation_text=f"Cost Basis: ${avg_price:.2f}",
                            annotation_position="top left"
                        )

                        # Final styling
                        fig.update_layout(showlegend=True, hovermode="x unified")
                        st.plotly_chart(fig, width="stretch")
                    else:
                        st.error(f"Could not load historical data for {asset}")

        except Exception as e:
            st.error(f"Analysis Error: {e}")