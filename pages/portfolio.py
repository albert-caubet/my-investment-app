import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.express as px
from database import get_all_transactions

st.set_page_config(layout="wide", page_title="My Portfolio")

# --- DATA FETCHING ---
raw_data = get_all_transactions()

if not raw_data:
    st.title("Current Portfolio")
    st.warning("No transactions found. Go to the Transactions page to add some!")
else:
    df = pd.DataFrame(raw_data)

    # Defensive columns
    for col in ["ticker", "isin", "quantity", "action", "category"]:
        if col not in df.columns:
            df[col] = "Unknown" if col in ["ticker", "isin", "category"] else 0

    df['id'] = df['ticker'].replace("", None).fillna(df['isin']).fillna("Unknown")

    # Buy/Sell Math
    df['adj_qty'] = df.apply(
        lambda x: x['quantity'] if x['action'] == 'Buy' else -x['quantity'],
        axis=1
    )

    # Aggregation
    summary = df.groupby("id").agg({
        "adj_qty": "sum",
        "category": "first"
    }).reset_index()

    summary.rename(columns={"adj_qty": "Total Shares", "id": "Asset"}, inplace=True)
    summary = summary[summary["Total Shares"] > 0]

    if summary.empty:
        st.title("💰 Current Portfolio")
        st.info("You currently have no open positions.")
    else:
        # --- MARKET DATA FETCHING ---
        asset_list = summary["Asset"].tolist()

        try:
            # 1. Fetch data
            live_data_full = yf.download(asset_list, period="5d")['Close']  # Fetch 5 days to ensure we get data

            # Fill any gaps (like weekends/holidays) by carrying the last known price forward
            live_data_full = live_data_full.ffill()

            current_prices = live_data_full.iloc[-1]
            prev_prices = live_data_full.iloc[-2]

            # 2. Map to summary
            if len(asset_list) == 1:
                # If only 1 ticker, live_data_full is a Series.
                # We convert to a dict to make mapping easy.
                current_dict = {asset_list[0]: live_data_full.iloc[-1]}
                prev_dict = {asset_list[0]: live_data_full.iloc[-2]}
            else:
                # If multiple tickers, it's a DataFrame
                current_dict = live_data_full.iloc[-1].to_dict()
                prev_dict = live_data_full.iloc[-2].to_dict()

            summary["Current Price"] = summary["Asset"].map(current_dict).astype(float)
            summary["Prev Price"] = summary["Asset"].map(prev_dict).astype(float)

            # 3. Handle potential NaNs before math
            summary = summary.dropna(subset=["Current Price"])

            summary["Market Value"] = summary["Total Shares"] * summary["Current Price"]
            summary["Daily Change $"] = summary["Total Shares"] * (summary["Current Price"] - summary["Prev Price"])

            # --- HEADER METRICS (REINFORCED) ---
            total_value = summary["Market Value"].sum()
            total_daily_change = summary["Daily Change $"].sum()

            # Prevent division by zero or NaN
            denominator = total_value - total_daily_change
            change_percent = (total_daily_change / denominator * 100) if denominator != 0 else 0

            st.title("💰 Current Portfolio")

            m1, m2, m3 = st.columns(3)
            m1.metric("Total Net Worth", f"${total_value:,.2f}")

            # Use 'delta' only if we have a valid change
            m2.metric("Today's Change", f"${total_daily_change:,.2f}", f"{change_percent:.2f}%")

            # Only calculate 'Top Asset' if summary isn't empty after dropping NaNs
            if not summary.empty:
                # Calculate daily return % for each asset
                summary["Return %"] = (summary["Current Price"] / summary["Prev Price"] - 1) * 100
                best_asset_row = summary.loc[summary["Return %"].idxmax()]
                m3.metric("Top Asset Today", best_asset_row["Asset"], f"{best_asset_row['Return %']:.2f}%")

            st.markdown("---")

            # 1. DISPLAY TABLE
            st.subheader("Holdings Overview")
            st.dataframe(summary[["Asset", "category", "Total Shares", "Current Price", "Market Value"]].style.format({
                "Total Shares": "{:.2f}",
                "Current Price": "${:.2f}",
                "Market Value": "${:,.2f}"
            }), width='stretch', hide_index=True)

            # 2. ALLOCATION PIE CHART
            st.markdown("---")
            col_chart, col_empty = st.columns([2, 1])

            with col_chart:
                st.subheader("Diversification by Category")
                cat_data = summary.groupby("category")["Market Value"].sum().reset_index()
                fig = px.pie(cat_data, values='Market Value', names='category', hole=0.4,
                             color_discrete_sequence=px.colors.qualitative.Prism)
                st.plotly_chart(fig, width='stretch')

                # 3. HISTORICAL PRICE PLOT
                st.markdown("---")
                st.subheader("Asset Performance History")
                selected_asset = st.selectbox("Select an asset to view price history:", asset_list)

                # Fetch historical data
                # ... (Inside your historical plot logic) ...
                hist_data = yf.download(selected_asset, period="1y", progress=False)

                if not hist_data.empty:
                    # Modern column flattening
                    if isinstance(hist_data.columns, pd.MultiIndex):
                        hist_data.columns = hist_data.columns.get_level_values(0)

                    hist_plot_df = hist_data.reset_index()

                    # --- NEW: Calculate Average Buy Price for this asset ---
                    # Filter original df for ONLY 'Buy' actions of this asset
                    buys = df[(df['id'] == selected_asset) & (df['action'] == 'Buy')]
                    if not buys.empty:
                        avg_buy_price = (buys['quantity'] * buys['price']).sum() / buys['quantity'].sum()
                    else:
                        avg_buy_price = None

                    # Create the Line Chart
                    fig_line = px.line(
                        hist_plot_df,
                        x="Date",
                        y="Close",
                        title=f"{selected_asset} - Performance vs. Your Cost Basis",
                        labels={"Close": "Price ($)", "Date": "Timeline"},
                        template="plotly_white"  # Clean desktop look
                    )

                    # --- NEW: Add the Horizontal 'Cost Basis' Line ---
                    if avg_buy_price:
                        fig_line.add_hline(
                            y=avg_buy_price,
                            line_dash="dash",
                            line_color="green",
                            annotation_text=f"Avg Buy: ${avg_buy_price:.2f}",
                            annotation_position="bottom right"
                        )

                    # Add a nice gradient/color
                    fig_line.update_traces(line_color='#007BFF')
                    st.plotly_chart(fig_line, width="stretch")

                else:
                    st.error("No historical data found for this ticker.")

        except Exception as e:
            st.error(f"Error updating market data: {e}")
            st.dataframe(summary)