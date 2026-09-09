from datetime import date, datetime

import pandas as pd
import streamlit as st
import yfinance as yf

import market_data as md
import portfolio_math as pm
from database import (
    delete_transaction,
    get_all_transactions,
    get_timestamp,
    record_transaction,
    update_transaction,
)

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


def _detect_currency(identifier: str) -> str | None:
    """Listing currency for a ticker or ISIN; cached per symbol for a day."""
    return md.fetch_listing_currency((identifier,)).get(identifier)


def _fx_blocked(currency: str, day: date) -> str:
    return (
        f"Save blocked: no EUR{currency} rate available for {day:%Y-%m-%d}. Markets may "
        f"have been closed, or today's rate may not have published yet. Use the previous "
        f"business day, or enter the rate in the FX override field."
    )


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
        if st.form_submit_button("Fetch Name", key="fetch_name"):
            if identifier:
                st.session_state["detected_for"] = identifier
                st.session_state["detected_ccy"] = _detect_currency(identifier)
                found_name = md.fetch_listing_name(identifier)
                if found_name:
                    st.session_state["fetched_name"] = found_name
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
        if st.form_submit_button("Fetch Price", key="fetch_price"):
            if identifier:
                try:
                    if not st.session_state.get("detected_ccy"):
                        st.session_state["detected_for"] = identifier
                        st.session_state["detected_ccy"] = _detect_currency(identifier)

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

    submitted = st.form_submit_button("🚀 Save Transaction", key="save_trade", width="stretch")

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
                st.error(_fx_blocked(currency, date_input))
            else:
                try:
                    resolved = None
                    if isin_clean and not ticker_clean:
                        # Stored so the pricing path never has to run a live ISIN
                        # search, which can resolve to another exchange listing.
                        resolved = md.resolve_isin(isin_clean)

                    trade_data = pm.trade_document(
                        trade_date=date_input,
                        category=category,
                        action=action,
                        currency=currency,
                        quantity=quantity,
                        price_nominal=price,
                        fees=fees,
                        fx_rate=fx_rate,
                        fx_source=fx_source,
                        ticker=ticker_clean,
                        isin=isin_clean,
                        name=name,
                        listing_ccy=detected,
                        resolved_ticker=resolved,
                    )
                    trade_data["timestamp"] = get_timestamp()
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


def _prefill(doc: dict) -> dict:
    """Widget defaults for the edit form.

    Taken from the normalised transaction when the document is usable, and from
    the raw fields when it is not -- a broken document is exactly the one that
    most needs editing, so it must still open in the form.
    """

    def num(value, default=0.0):
        try:
            out = float(value)
        except (TypeError, ValueError):
            return default
        # NaN guard, and number_input refuses a value below its min_value.
        return out if out == out and out >= 0.0 else default

    tx, _ = pm.transaction_from_doc(doc)
    if tx is not None:
        return {
            "date": tx.trade_date,
            "category": tx.category,
            "action": tx.action,
            "currency": tx.currency,
            "quantity": tx.quantity,
            "price": tx.price_nominal,
            "fees": tx.fees,
            "fx_rate": tx.fx_rate,
            "ticker": tx.ticker or "",
            "isin": tx.isin or "",
            "name": tx.name or "",
        }

    try:
        day = datetime.strptime(str(doc.get("date") or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        day = date.today()
    return {
        "date": day,
        "category": doc.get("category"),
        "action": doc.get("action"),
        "currency": str(doc.get("currency") or pm.BASE_CCY).upper(),
        "quantity": num(doc.get("quantity")),
        "price": num(doc.get("price_nominal", doc.get("price"))),
        "fees": num(doc.get("fees")),
        "fx_rate": num(doc.get("fx_rate", doc.get("fx_rate_at_buy")), 1.0),
        "ticker": str(doc.get("ticker") or ""),
        "isin": str(doc.get("isin") or ""),
        "name": str(doc.get("name") or ""),
    }


def _edit_form(original: dict) -> None:
    """Edit or delete one stored transaction.

    Saving rebuilds the whole document through the same builder the create form
    uses, so an edited legacy row comes out in the current schema. The original
    timestamp is kept because it orders same-day trades; the edit itself is
    recorded in ``updated_at``.
    """
    doc_id = original["_doc_id"]
    d = _prefill(original)

    def k(field: str) -> str:
        # Keyed per document: selecting another row re-seeds every field from that
        # row instead of carrying over what was typed for the previous one.
        return f"edit_{field}_{doc_id}"

    st.subheader("Edit selected transaction")
    with st.form(f"edit_form_{doc_id}"):
        col1, col2 = st.columns(2)
        with col1:
            e_date = st.date_input("Transaction Date", value=d["date"], key=k("date"))
            e_category = st.selectbox(
                "Asset Category",
                ASSET_CLASSES,
                index=(
                    ASSET_CLASSES.index(d["category"])
                    if d["category"] in ASSET_CLASSES
                    else ASSET_CLASSES.index("Other")
                ),
                key=k("category"),
            )
            e_ticker = st.text_input("Ticker", value=d["ticker"], key=k("ticker")).upper().strip()
            e_isin = st.text_input("ISIN", value=d["isin"], key=k("isin")).upper().strip()
            e_name = st.text_input("Asset Name", value=d["name"], key=k("name")).strip()
        with col2:
            e_action = st.selectbox(
                "Action", ["Buy", "Sell"], index=1 if d["action"] == "Sell" else 0, key=k("action")
            )
            e_currency = st.selectbox(
                "Transaction Currency",
                SUPPORTED_CCY,
                index=SUPPORTED_CCY.index(d["currency"]) if d["currency"] in SUPPORTED_CCY else 0,
                key=k("currency"),
            )
            e_quantity = st.number_input(
                "Quantity", min_value=0.0, value=float(d["quantity"]), step=0.01,
                format="%.4f", key=k("quantity"),
            )
            e_price = st.number_input(
                "Price (Nominal)", min_value=0.0, value=float(d["price"]), step=0.01,
                format="%.4f", key=k("price"),
            )
            e_fees = st.number_input(
                "Fees", min_value=0.0, value=float(d["fees"]), step=0.01, key=k("fees")
            )
            e_fx = st.number_input(
                "FX override (units per 1 EUR, 0 = keep the stored rate)",
                min_value=0.0, value=0.0, step=0.0001, format="%.4f", key=k("fx"),
            )
            if d["currency"] != pm.BASE_CCY:
                st.caption(
                    f"Stored rate: {d['fx_rate']:.4f} ({original.get('fx_source') or 'unknown source'}). "
                    f"Kept unless the date or currency changes, in which case it is looked up again."
                )

        confirm = st.checkbox("Yes, delete this transaction permanently", key=k("confirm"))
        b_save, b_delete = st.columns(2)
        save = b_save.form_submit_button(
            "Save changes", type="primary", key=k("save"), width="stretch"
        )
        delete = b_delete.form_submit_button("Delete", key=k("delete"), width="stretch")

    if delete:
        if not confirm:
            st.error("Tick the confirmation box to delete this transaction.")
            return
        try:
            delete_transaction(doc_id)
        except Exception as exc:
            st.error(f"Error deleting transaction: {exc}")
            return
        st.session_state["tx_flash"] = (
            f"Deleted {d['action']} of {d['quantity']:g} "
            f"{d['ticker'] or d['isin'] or d['name']} on {d['date']:%Y-%m-%d}."
        )
        st.session_state["tx_log_rev"] = st.session_state.get("tx_log_rev", 0) + 1
        st.rerun()

    if not save:
        return

    if not e_ticker and not e_isin:
        st.error("ERROR: You must provide at least a Ticker or an ISIN.")
        return
    if e_quantity <= 0:
        st.error("ERROR: Quantity must be greater than 0.")
        return
    if e_price <= 0:
        st.error("ERROR: Price must be greater than 0.")
        return

    # Same rule as the create form: never default an FX rate. The stored rate is
    # kept only while the date and currency it was fetched for are unchanged, and
    # a stored 1.0 on a non-EUR trade is the old silent fallback, not a rate.
    fx_context_unchanged = e_currency == d["currency"] and e_date == d["date"]
    if e_currency == pm.BASE_CCY:
        fx_rate, fx_source = 1.0, "base"
    elif e_fx > 0:
        fx_rate, fx_source = e_fx, "manual"
    elif fx_context_unchanged and d["fx_rate"] > 0 and d["fx_rate"] != 1.0:
        fx_rate, fx_source = d["fx_rate"], original.get("fx_source") or "stored"
    else:
        fx_rate = md.fetch_historical_fx(e_date.strftime("%Y-%m-%d"), e_currency)
        fx_source = "yahoo"
    if fx_rate is None:
        st.error(_fx_blocked(e_currency, e_date))
        return

    # Identity metadata is re-detected only when the identity itself changed.
    identifier = e_ticker or e_isin
    listing_ccy = original.get("listing_ccy")
    resolved = original.get("resolved_ticker")
    if e_ticker != d["ticker"] or e_isin != d["isin"]:
        listing_ccy = _detect_currency(identifier)
        resolved = md.resolve_isin(e_isin) if (e_isin and not e_ticker) else None

    try:
        doc = pm.trade_document(
            trade_date=e_date,
            category=e_category,
            action=e_action,
            currency=e_currency,
            quantity=e_quantity,
            price_nominal=e_price,
            fees=e_fees,
            fx_rate=fx_rate,
            fx_source=fx_source,
            ticker=e_ticker,
            isin=e_isin,
            name=e_name,
            listing_ccy=listing_ccy,
            resolved_ticker=resolved,
        )
        doc["timestamp"] = original.get("timestamp") or get_timestamp()
        doc["updated_at"] = get_timestamp()
        update_transaction(doc_id, doc)
    except Exception as exc:
        st.error(f"Error saving changes: {exc}")
        return

    moved = "cost" if e_action == "Buy" else "proceeds"
    st.session_state["tx_flash"] = (
        f"Updated {e_action} of {e_quantity:g} {identifier} on {e_date:%Y-%m-%d} "
        f"— {moved} €{doc['cost_eur']:,.2f} (FX {fx_rate:.4f}, {fx_source})."
    )
    st.session_state["tx_log_rev"] = st.session_state.get("tx_log_rev", 0) + 1
    st.rerun()


st.markdown("---")
st.subheader("Transaction History Log")

# The outcome of the previous run's save or delete, shown once. That run ends in
# a rerun so the table refreshes, and the rerun would otherwise wipe the message.
flash = st.session_state.pop("tx_flash", None)
if flash:
    st.success(flash)

raw_logs = get_all_transactions()

if not raw_logs:
    st.info("No transactions recorded yet.")
else:
    rows = []
    for doc in raw_logs:
        tx, problems = pm.transaction_from_doc(doc)
        notes = [n for n in pm.audit_transaction(doc) if "no fees" not in n]
        if tx is None:
            rows.append(
                {
                    "_doc_id": doc.get("_doc_id"),
                    "date": str(doc.get("date") or ""),
                    "name": doc.get("name"),
                    "note": "; ".join(problems) or "unusable",
                }
            )
            continue
        rows.append(
            {
                "_doc_id": tx.doc_id,
                # ISO text rather than a date object, so usable and unusable rows
                # sort together instead of raising on mixed types.
                "date": tx.trade_date.isoformat(),
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

    log_df = (
        pd.DataFrame(rows).sort_values(by="date", ascending=False).reset_index(drop=True)
    )

    # The key changes after every write, so the refreshed table opens with nothing
    # selected: the selected row may have moved or may no longer exist.
    table_key = f"tx_log_{st.session_state.get('tx_log_rev', 0)}"
    event = st.dataframe(
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
        column_config={"_doc_id": None},
        on_select="rerun",
        selection_mode="single-row",
        key=table_key,
        width="stretch",
        hide_index=True,
    )

    flagged = [r for r in rows if r.get("note")]
    if flagged:
        st.caption(f"{len(flagged)} row(s) carry a data-quality note — see the `note` column.")

    selected_rows = event.selection.rows
    if not selected_rows:
        st.caption("Select a row to edit or delete that transaction.")
    else:
        selected_id = log_df.iloc[selected_rows[0]]["_doc_id"]
        original = next((d for d in raw_logs if d.get("_doc_id") == selected_id), None)
        if original is not None:
            _edit_form(original)
