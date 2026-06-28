import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.express as px
import numpy as np
from database import get_all_transactions

# TODO: add the list of historical transactions in the transactions tab


# --- CACHED FUNCTIONS ---

@st.cache_data(ttl=3600)  # Cache market data for 1 hour
def fetch_live_market_data(tickers):
    """Fetches current prices, benchmark, and FX rates."""
    data = yf.download(tickers, period="7d", progress=True)['Close'].ffill()
    return data

@st.cache_data(ttl=3600)
def fetch_historical_data(ticker, period):
    """Fetches historical data for the selected time range."""
    data = yf.download(ticker, period=period, progress=True)
    if not data.empty and isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


@st.cache_data(ttl=86400) # Cache for 24 hours as currency rarely changes
def fetch_ticker_currency(tickers):
    """Fetches the official listing currency from Yahoo Finance."""
    currencies = {}
    for t in tickers:
        try:
            # Skip benchmark/fx tickers
            if "=" in t or "^" in t: continue
            info = yf.Ticker(t).info
            currencies[t] = info.get("currency", "???")
        except:
            currencies[t] = "Unknown"
    return currencies


@st.cache_data(ttl=3600)  # Refresh every hour
def fetch_rich_metadata(tickers):
    """Fetches Analyst Targets, Currency, and Full Name."""
    metadata = {}
    for t in tickers:
        try:
            if "^" in t or "=" in t: continue
            ticker_obj = yf.Ticker(t)
            info = ticker_obj.info

            metadata[t] = {
                "Market Currency": info.get("currency", "???"),
                "Company Name": info.get("displayName", t),  # shortName, longName
                "Current Price": info.get("currentPrice") or info.get("regularMarketPrice")
            }
        except Exception:
            metadata[t] = {"Market Currency": "???", "Analyst Target": np.nan, "Company Name": t}
    return metadata


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

    df['id'] = df['isin'].replace("", None).fillna(df['ticker']).fillna("Unknown")
    df['adj_qty'] = df.apply(lambda x: x['quantity'] if x['action'] == 'Buy' else -x['quantity'], axis=1)


    # # 1. PRE-CALCULATE AVG BUY PRICE (Weighted)
    # def calc_avg_price(group):
    #     buys = group[group['action'] == 'Buy']
    #     if buys.empty: return 0
    #     return (buys['quantity'] * buys['price']).sum() / buys['quantity'].sum()
    # avg_prices = df.groupby('id').apply(calc_avg_price, include_groups=False).to_dict()

    # ==========================================================================================================
    # 1. CALCULATE WEIGHTED COST BASIS (EUR)
    # ==========================================================================================================

    def calc_accounting(group):
        buys = group[group['action'] == 'Buy']
        if buys.empty: return pd.Series([0, 0], index=['avg_nom', 'total_cost_eur'])

        avg_nominal = (buys['quantity'] * buys['price_nominal']).sum() / buys['quantity'].sum()
        total_cost_eur = buys['cost_eur'].sum()
        return pd.Series([avg_nominal, total_cost_eur], index=['avg_nom', 'total_cost_eur'])

    acct_df = df.groupby('id').apply(calc_accounting, include_groups=False) #

    # ==========================================================================================================
    # 2. AGGREGATE SUMMARY (Sorted by Latest Activity)
    # ==========================================================================================================

    # First, ensure 'date' is a datetime object for accurate sorting
    df['date'] = pd.to_datetime(df['date'])

    summary = df.groupby("id").agg({
        "adj_qty": "sum",
        "category": "first",
        "name": "first",
        "ticker": "first",
        "isin": "first",
        "currency": "first",
        "date": "max"  # We capture the LATEST transaction date for each asset
    }).reset_index()

    summary = summary.merge(acct_df, on='id')
    summary.rename(columns={"adj_qty": "Shares", "id": "Asset", "avg_nom": "Avg Buy (Nom)"}, inplace=True)
    summary = summary[summary["Shares"] > 0]

    # Sort the summary by the latest transaction date (Newest at the top)
    # This matches the "First in, Last out" feel of your transaction log
    summary = summary.sort_values(by="date", ascending=False)

    if not summary.empty:
        try:
            # # 3. LIVE MARKET DATA (Assets + Benchmark + FX + 10Y Treasury)
            asset_list = summary["Asset"].tolist()

            # Fetch official currencies
            # official_currencies = fetch_ticker_currency(asset_list)
            # summary["Ccy"] = summary["Asset"].map(official_currencies) # Map them to a new column
            # or...
            metadata = fetch_rich_metadata(asset_list)
            # summary["Ccy"] = summary["Asset"].map(lambda x: metadata.get(x, {}).get("Market Currency"))
            summary["Ccy"] = [metadata.get(ticker, {}).get("Market Currency") for ticker in summary["Asset"]]

            # Fetch the OFFICIAL company display name
            summary["Official Name"] = [metadata.get(ticker, {}).get("Company Name") for ticker in summary["Asset"]]

            # Benchmarks
            benchmark_ticker = "^GSPC"  # S&P 500, USA
            rf_ticker = "^TNX"  # 10-Year Treasury Yield, USA

            all_tickers = asset_list + ["EURUSD=X", benchmark_ticker, rf_ticker]
            # all_tickers = summary["Asset"].tolist() + ["EURUSD=X", benchmark_ticker, rf_ticker]

            # Fetch daily data
            # market_data = yf.download(all_tickers, period="5d", progress=False)['Close'].ffill()
            market_data = fetch_live_market_data(all_tickers)

            # Current Prices & FX
            current_prices = market_data.iloc[-1]
            eur_usd = float(market_data["EURUSD=X"].iloc[-1])
            # curr_fx = float(market_data["EURUSD=X"].iloc[-1])

            # Risk-Free Rate: ^TNX returns the yield as a percentage (e.g., 4.25)
            # We divide by 100 to get the decimal (0.0425)
            current_rf_annual = float(market_data[rf_ticker].iloc[-1]) / 100
            # Daily risk-free rate (approximate)
            rf_daily = current_rf_annual / 252

            # 4. CALCULATE PERFORMANCE COLUMNS
            # summary["Avg Buy Price"] = summary["Asset"].map(avg_prices)
            # summary["Current Price"] = summary["Asset"].map(current_prices)

            summary["Curr Price (Nom)"] = summary["Asset"].map(market_data.iloc[-1]).astype(float)
            summary["Price Change (%)"] = (summary["Curr Price (Nom)"] / summary["Avg Buy (Nom)"] - 1) * 100
            summary["Cost Basis (EUR)"] = summary["total_cost_eur"]
            # summary["Cost Basis (EUR)"] = summary["total_cost_eur"]  / summary["Shares"] # Cost Basis (EUR) per share = Total EUR Spent / Total Shares
            # Market Value (EUR) = (Shares * Nominal Price) / Current FX (if USD)
            summary["Market Value (EUR)"] = summary.apply(
                lambda x: (x['Shares'] * x['Curr Price (Nom)']) / eur_usd if x['currency'] == 'USD'
                else (x['Shares'] * x['Curr Price (Nom)']), axis=1
            )
            # Total PnL (EUR) = Market Value (EUR) - Cost Basis EUR
            summary["PnL (EUR)"] = summary["Market Value (EUR)"] - summary["Cost Basis (EUR)"]
            summary["PnL (%)"] = summary["PnL (EUR)"] / summary["Cost Basis (EUR)"] * 100


            # def convert_to_eur(row, price_col):
            #     if row['currency'] == 'USD':
            #         return row[price_col] / eur_usd
            #     return row[price_col]
            # summary["Current Price (EUR)"] = summary.apply(lambda x: convert_to_eur(x, "Current Price"), axis=1)
            # summary["Avg Buy Price (EUR)"] = summary.apply(lambda x: convert_to_eur(x, "Avg Buy Price"), axis=1)
            # summary["Market Value (EUR)"] = summary["Total Shares"] * summary["Current Price (EUR)"]
            # summary["PnL (EUR)"] = (summary["Current Price (EUR)"] - summary["Avg Buy Price (EUR)"]) * summary[
            #     "Total Shares"]
            # summary["PnL (%)"] = (summary["Current Price (EUR)"] / summary["Avg Buy Price (EUR)"] - 1) * 100


            total_portfolio_value = summary["Market Value (EUR)"].sum()
            total_cost_basis = summary["Cost Basis (EUR)"].sum()
            summary["Weight (%)"] = (summary["Market Value (EUR)"] / total_portfolio_value) * 100

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

            # ==========================================================================================================
            # --- DISPLAY DASHBOARD ---
            # ==========================================================================================================

            st.title("Portfolio Dashboard")

            # Top Metrics
            m1, m2, m3, m4, m5 = st.columns(5)

            m1.metric("Total Cost Basis (EUR)", f"€{total_cost_basis:,.0f}")

            m2.metric("Total Value (EUR)", f"€{total_portfolio_value:,.0f}")

            total_pnl_eur = summary["PnL (EUR)"].sum()
            m3.metric("Total PnL (EUR)", f"€{total_pnl_eur:,.1f}", f"{(total_pnl_eur / total_cost_basis) * 100:.2f}%")

            total_pnl_pc = total_pnl_eur / total_cost_basis * 100
            m4.metric("Total PnL (%)", f"{total_pnl_pc:.1f}%")

            m5.metric("Risk-Free Rate (" + rf_ticker + ")", f"{current_rf_annual * 100:.2f}%")

            # REFINED TABLE
            st.subheader("Asset Breakdown")
            display_cols = [
                "category", "name", "ticker", "isin", "Ccy", "Avg Buy (Nom)",
                "Curr Price (Nom)", "Price Change (%)", "Cost Basis (EUR)", "Market Value (EUR)", "PnL (%)",
                "PnL (EUR)", "Weight (%)", "Beta", "Alpha"
            ]

            pnl_pc_limit = max(abs(summary['PnL (%)'].min()), abs(summary['PnL (%)'].max()), 0.1)
            alpha_limit = max(abs(summary['Alpha'].min()), abs(summary['Alpha'].max()), 0.1)

            st.dataframe(
                summary[display_cols].style.format({
                    "Avg Buy (Nom)": "{:.2f}",
                    "Curr Price (Nom)": "{:.2f}",
                    "Price Change (%)": "{:.1f} %",
                    "Cost Basis (EUR)": "€ {:.2f}",
                    "Market Value (EUR)": "€ {:,.2f}",
                    "PnL (%)": "{:.1f} %",
                    "PnL (EUR)": "€ {:,.2f}",
                    "Weight (%)": "{:.1f} %",
                    "Beta": "{:.2f}",
                    "Alpha": "{:.4f}"
                }).background_gradient(subset=['PnL (%)'], cmap='RdYlGn', vmin=-pnl_pc_limit, vmax=pnl_pc_limit)
                .background_gradient(subset=['Alpha'], cmap='RdYlGn', vmin=-alpha_limit, vmax=alpha_limit),
                width='stretch', hide_index=True
            )

            # ==========================================================================================================
            # --- 2. ALLOCATION PIE CHART ---
            # ==========================================================================================================

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

            # ==========================================================================================================
            # --- 3. ALL ASSET PERFORMANCE HISTORIES ---
            # ==========================================================================================================

            st.markdown("---")
            st.subheader("Asset Performance & Transaction History")

            # Time range selector
            time_options = {
                "6 Months": "6mo",
                "1 Year": "1y",
                "3 Years": "3y",
                "5 Years": "5y",
                "10 Years": "10y",
                "All Time": "max"
            }

            # Add a selectbox for the user to choose the timeframe
            selected_label = st.selectbox("Select Time Range", options=list(time_options.keys()), index=2)  # Default to 3 Years
            selected_period = time_options[selected_label]

            # st.write(asset_list)

            # We fetch all historical data in one go if possible, or iterate
            # for asset in asset_list:
            #     asset_name = metadata.get(asset, {}).get("Company Name", asset)

            for index, row in summary.iterrows():
                asset_ticker = row["Asset"]
                # Pull the name you wrote when logging the transaction
                custom_name = row["name"] if row["name"] != "Unknown" else asset_ticker
                official_name = row["Official Name"] if row["Official Name"] else asset_ticker
                currency = row["Ccy"]

                with st.expander(f"📈 {custom_name}", expanded=True):
                    # Fetch the selected period
                    # hist_data = yf.download(asset, period=selected_period, progress=False)
                    hist_data = fetch_historical_data(asset_ticker, selected_period)

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
                            title=f"{official_name} ({currency}, {asset_ticker}) - {selected_label}",
                            labels={"Close": "Price", "Date": "Timeline"},
                            template="plotly_white"
                        )

                        # 2. Add "Buy" and "Sell" markers from your Firestore 'df'
                        # Filter transactions for THIS specific asset
                        asset_txs = df[df['id'] == asset_ticker].copy()
                        # Ensure date is in datetime format for alignment with x-axis
                        asset_txs['date'] = pd.to_datetime(asset_txs['date'])

                        # Add Buy Markers (Green Up-Arrows)
                        buys = asset_txs[asset_txs['action'] == 'Buy']
                        if not buys.empty:
                            fig.add_trace(px.scatter(
                                buys, x='date', y='price_nominal',
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
                                sells, x='date', y='price_nominal',
                                color_discrete_sequence=['#E74C3C']
                            ).data[0].update(
                                name="Sell",
                                marker=dict(size=12, symbol='triangle-down', line=dict(width=2, color='DarkRed')),
                                hovertemplate="<b>SELL</b><br>Date: %{x}<br>Price: $%{y:.2f}"
                            ))

                        # 3. Add the Average Cost Basis Line (Break-even)
                        asset_summary = summary[summary['Asset'] == asset_ticker].iloc[0]
                        avg_price = asset_summary['Avg Buy (Nom)']

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
                        st.error(f"Could not load historical data for {asset_ticker}")

        except Exception as e:
            st.error(f"Analysis Error: {e}")