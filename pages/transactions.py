from datetime import datetime

import pandas as pd
import streamlit as st
import yfinance as yf

import market_data as md
import portfolio_math as pm
from database import get_all_transactions, get_timestamp, record_transaction

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
    "Other",
]

SUPPORTED_CCY = ["EUR", "USD"]


def _detect(identifier: str) -> dict:
    """Listing currency and name for a ticker or ISIN, cached for the session."""
    info = md.fetch_listing_info((identifier,)).get(identifier, {})
    return {"currency": info.get("currency"), "name": info.get("name")}


# Create the form
with st.form("trade_form", clear_on_submit=False):
    col1, col2 = st.columns(2)
    with col1:
        date_input = st.date_input("Transaction Date", value=datetime.now())
        category = st.selectbox("Asset Category", options=ASSET_CLASSES)
        ticker = st.text_input("Ticker").upper().strip()
        isin = st.text_input("ISIN").upper().strip()

        identifier = ticker or isin
        # Detection is keyed to the identifier, so changing the ticker invalidates a
        # previously detected currency instead of leaving a stale one behind.
        if identifier and st.session_state.get("detected_for") != identifier:
            st.session_state.pop("detected_ccy", None)
            st.session_state.pop("fetched_name", None)

        # --- Fetch Name & Detect Currency ---
        if st.form_submit_button("Fetch Name"):
            if identifier:
                found = _detect(identifier)
                st.session_state["detected_for"] = identifier
                st.session_state["detected_ccy"] = found["currency"]
                if found["name"]:
                    st.session_state["fetched_name"] = found["name"]
                else:
                    st.warning("No name found.")
            else:
                st.error("Enter a Ticker or ISIN first.")

        name = st.text_input("Asset Name", value=st.session_state.get("fetched_name", ""))

    with col2:
        action = st.selectbox("Action", ["Buy", "Sell"])
        currency = st.selectbox("Transaction Currency", SUPPORTED_CCY, index=0)
        st.caption("The currency the cash left your account in.")

        detected = st.session_state.get("detected_ccy")
        if detected and detected != currency:
            # Not an error: paying euros for a USD-listed asset is normal. Cost basis
            # follows the transaction currency, market value the listing currency.
            st.info(
                f"Asset is listed in **{detected}**; you are paying in **{currency}**. "
                f"That is fine — both are handled separately."
            )
        if detected and detected not in SUPPORTED_CCY:
            st.warning(
                f"This asset is listed in **{detected}**, which the dashboard cannot "
                f"value yet. It will appear with a 'no rate' note until "
                f"{detected} support is added."
            )

        quantity = st.number_input("Quantity", min_value=0.0, step=0.01)

        # --- Fetch Historical Price ---
        if st.form_submit_button("Fetch Price"):
            if identifier:
                try:
                    if not st.session_state.get("detected_ccy"):
                        found = _detect(identifier)
                        st.session_state["detected_for"] = identifier
                        st.session_state["detected_ccy"] = found["currency"]

                    target = pd.to_datetime(date_input)
                    hist = yf.download(
                        identifier,
                        start=(target - pd.Timedelta(days=7)).strftime("%Y-%m-%d"),
                        end=(target + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                        progress=False,
                    )
                    if not hist.empty:
                        if isinstance(hist.columns, pd.MultiIndex):
                            hist.columns = hist.columns.get_level_values(0)
                        close = hist["Close"].dropna()
                        if isinstance(close, pd.DataFrame):
                            close = close.iloc[:, 0]
                        st.session_state["fetched_price"] = float(close.iloc[-1])
                        st.success(f"Price for {target.strftime('%Y-%m-%d')} fetched.")
                    else:
                        st.warning("No historical data found.")
                except Exception as exc:
                    st.error(f"Error: {exc}")
            else:
                st.error("Enter a Ticker or ISIN first.")

        price = st.number_input(
            "Price (Nominal)", min_value=0.0, step=0.01,
            value=st.session_state.get("fetched_price", 0.0),
        )
        fees = st.number_input("Fees", min_value=0.0, step=0.01)
        st.caption("Included in the cost basis.")

        manual_fx = st.number_input(
            f"FX override ({currency} per 1 EUR, 0 = look up automatically)",
            min_value=0.0, step=0.0001, format="%.4f", value=0.0,
        )

    submitted = st.form_submit_button("🚀 Save Transaction", use_container_width=True)

    if submitted:
        ticker_clean = ticker.strip().upper()
        isin_clean = isin.strip().upper()

        if not ticker_clean and not isin_clean:
            st.error("ERROR: You must provide at least a Ticker or an ISIN.")
        elif quantity <= 0:
            st.error("ERROR: Quantity must be greater than 0.")
        elif price <= 0:
            st.error("ERROR: Price must be greater than 0.")
        else:
            # An FX rate that cannot be established is refused rather than defaulted.
            # A silent 1.0 permanently understates a USD cost basis by ~15%, and the
            # wrong rate gets frozen into the database.
            if currency == pm.BASE_CCY:
                fx_rate, fx_source = 1.0, "base"
            elif manual_fx > 0:
                fx_rate, fx_source = manual_fx, "manual"
            else:
                fx_rate = md.fetch_historical_fx(date_input.strftime("%Y-%m-%d"), currency)
                fx_source = "yahoo"

            if fx_rate is None:
                st.error(
                    f"Save blocked: no EUR{currency} rate available for "
                    f"{date_input:%Y-%m-%d}. Markets may have been closed, or today's "
                    f"rate may not have published yet. Use the previous business day, "
                    f"or enter the rate in the FX override field above."
                )
            else:
                try:
                    resolved = None
                    if isin_clean and not ticker_clean:
                        resolved = md.resolve_isin(isin_clean)

                    trade_data = {
                        "date": date_input.strftime("%Y-%m-%d"),
                        "category": category,
                        "action": action,
                        "currency": currency,
                        "quantity": quantity,
                        "price_nominal": price,
                        "fees": fees,  # always written, so a real 0.0 is not dropped
                        "fx_rate": fx_rate,
                        "fx_source": fx_source,
                        # Cash that moved: cost including fees on a buy, proceeds net
                        # of fees on a sell. Denormalised -- the reader recomputes it
                        # and the audit checks that the two agree.
                        "cost_eur": (
                            (price * quantity + fees) / fx_rate
                            if action == "Buy"
                            else (price * quantity - fees) / fx_rate
                        ),
                        "schema_version": 2,
                        "timestamp": get_timestamp(),
                    }
                    if detected:
                        trade_data["listing_ccy"] = detected
                    if resolved:
                        # Stored so the pricing path never has to run a live ISIN
                        # search, which can resolve to another exchange listing.
                        trade_data["resolved_ticker"] = resolved
                    if ticker_clean:
                        trade_data["ticker"] = ticker_clean
                    if isin_clean:
                        trade_data["isin"] = isin_clean
                    if name:
                        trade_data["name"] = name.strip()

                    record_transaction(trade_data)
                    moved = "cost" if action == "Buy" else "proceeds"
                    st.success(
                        f"Recorded {action} of {quantity:g} {ticker_clean or isin_clean} "
                        f"— {moved} €{trade_data['cost_eur']:,.2f} "
                        f"(FX {fx_rate:.4f}, {fx_source})"
                    )

                    for key in ("fetched_name", "fetched_price", "detected_ccy", "detected_for"):
                        st.session_state.pop(key, None)

                except Exception as exc:
                    st.error(f"Error processing transaction: {exc}")


# ======================================================================================================================
# --- TRANSACTION HISTORY ---
# ======================================================================================================================

st.markdown("---")
st.subheader("Transaction History Log")

raw_logs = get_all_transactions()

if not raw_logs:
    st.info("No transactions recorded yet.")
else:
    rows = []
    for doc in raw_logs:
        tx, problems = pm.transaction_from_doc(doc)
        notes = [n for n in pm.audit_transaction(doc) if "no fees" not in n]
        if tx is None:
            rows.append({"date": doc.get("date"), "name": doc.get("name"),
                         "note": "; ".join(problems) or "unusable"})
            continue
        rows.append(
            {
                "date": tx.trade_date,
                "category": tx.category,
                "name": tx.name,
                "ticker": tx.ticker,
                "isin": tx.isin,
                "action": tx.action,
                "quantity": tx.quantity,
                "price_nominal": tx.price_nominal,
                "fees": tx.fees,
                "currency": tx.currency,
                "fx_rate": tx.fx_rate,
                # Recomputed rather than read back, so a drifted stored value shows up
                # in the note column instead of quietly flowing into the cost basis.
                # Cost including fees on a buy, proceeds net of fees on a sell.
                "net_eur": tx.net_eur,
                "note": "; ".join(notes),
            }
        )

    log_df = pd.DataFrame(rows).sort_values(by="date", ascending=False)

    st.dataframe(
        log_df.style.format(
            {
                "quantity": "{:.4f}",
                "price_nominal": "{:.2f}",
                "fees": "{:.2f}",
                "fx_rate": "{:.4f}",
                "net_eur": "€ {:.2f}",
            },
            na_rep="",
        ),
        width="stretch",
        hide_index=True,
    )

    flagged = [r for r in rows if r.get("note")]
    if flagged:
        st.caption(f"{len(flagged)} row(s) carry a data-quality note — see the `note` column.")
