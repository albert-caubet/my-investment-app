import streamlit as st
from datetime import datetime
from database import record_transaction, get_timestamp

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
    # Layout the inputs in columns for a cleaner "Dashboard" look
    col1, col2 = st.columns(2)

    with col1:
        date_input = st.date_input("Transaction Date", value=datetime.now())
        category = st.selectbox("Asset Category", options=ASSET_CLASSES)
        name = st.text_input("Asset Name")
        ticker = st.text_input("Ticker").upper().strip()
        isin = st.text_input("ISIN").upper().strip()

    with col2:
        action = st.selectbox("Action", ["Buy", "Sell"])
        currency = st.selectbox("Currency", ["EUR", "USD"])
        quantity = st.number_input("Quantity", min_value=0.0, step=0.01)
        price = st.number_input("Price per Unit", min_value=0.0, step=0.01)
        fees = st.number_input("Fees", min_value=0.0, step=0.01)

    submitted = st.form_submit_button("Save Transaction")

    if submitted:
        # 1. CLEAN THE INPUTS (remove whitespace)
        ticker_clean = ticker.strip().upper()
        isin_clean = isin.strip().upper()

        # 2. VALIDATION: Check if BOTH are empty
        if not ticker_clean and not isin_clean:
            st.error("❌ ERROR: You must provide at least a Ticker or an ISIN.")

        # 3. VALIDATION: Ensure quantity is valid
        elif quantity <= 0:
            st.error("❌ ERROR: Quantity must be greater than 0.")

        else:
            # BUILD THE DATA OBJECT
            # We only add fields if they aren't empty strings
            trade_data = {
                "date": date_input.strftime("%Y-%m-%d"),
                "category": category,
                "action": action,
                "currency": currency,
                "quantity": quantity,
                "price": price,
                "fees": fees,
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