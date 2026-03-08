import streamlit as st
from datetime import datetime
from database import record_transaction, get_timestamp, get_historical_fx

st.title("Log New Transaction")

# Define your standard categories
ASSET_CLASSES = [
    "Fund",
    "Stock",
    "ETF",
    "Cash/Money Market",
    "Fixed Income",
    "Crypto",
    "Bonds",
    "Commodity",
    "Other"
]

# Create the form
with st.form("trade_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    with col1:
        # User can now select a past date
        date_input = st.date_input("Transaction Date", value=datetime.now())
        category = st.selectbox("Asset Category", options=ASSET_CLASSES)
        ticker = st.text_input("Ticker").upper().strip()
        isin = st.text_input("ISIN").upper().strip()
        name = st.text_input("Asset Name").strip()

    with col2:
        action = st.selectbox("Action", ["Buy", "Sell"])
        currency = st.selectbox("Nominal Currency", ["EUR", "USD"], index=0)
        quantity = st.number_input("Quantity", min_value=0.0, step=0.01)
        price = st.number_input("Price (Nominal)", min_value=0.0, step=0.01)
        fees = st.number_input("Fees (Optional)", min_value=0.0, step=0.01)

    submitted = st.form_submit_button("Save Transaction")

    if submitted:

        ticker_clean = ticker.strip().upper()
        isin_clean = isin.strip().upper()
        if not ticker_clean and not isin_clean:
            st.error("ERROR: You must provide at least a Ticker or an ISIN.")
        elif quantity <= 0:
            st.error("ERROR: Quantity must be greater than 0.")

        else:
            # FETCH FX RATE FOR THAT DATE
            # We want to know: 1 EUR = X USD on that day
            fx_rate = get_historical_fx(date_input.strftime('%Y-%m-%d'), "EUR", "USD")

            # Calculate cost in EUR at time of purchase
            # If price is in USD, we divide by fx_rate (EURUSD) to get EUR
            cost_eur = (price * quantity) / fx_rate if currency == "USD" else (price * quantity)

            # BUILD THE DATA OBJECT
            # We only add fields if they aren't empty strings
            trade_data = {
                "date": date_input.strftime("%Y-%m-%d"),
                "category": category,
                "action": action,
                "currency": currency,
                "quantity": quantity,
                "price_nominal": price,
                "fx_rate_at_buy": fx_rate,
                "cost_eur": cost_eur,  # Fixed historical cost
                "timestamp": get_timestamp()  # The "Handshake" with Google
            }

            # Only add the identifier fields if they were filled out
            if ticker_clean:
                trade_data["ticker"] = ticker_clean
            if isin_clean:
                trade_data["isin"] = isin_clean
            if name:
                trade_data["name"] = name.strip()
            if fees:
                trade_data["fees"] = fees

            # SAVE TO FIRESTORE
            try:
                record_transaction(trade_data)
                st.success(f"Successfully recorded {ticker_clean or isin_clean}")
            except Exception as e:
                st.error(f"Error saving to database: {e}")