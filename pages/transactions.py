from datetime import date

import pandas as pd
import streamlit as st

import market_data as md
import portfolio_math as pm
from database import (
    clear_cash_cache,
    delete_transaction,
    get_all_transactions,
    get_cash_accounts,
    get_timestamp,
    record_transaction,
    record_transaction_with_balance,
    save_cash_accounts,
    update_transaction,
)
from invest.data import search

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

#: The asset category of what a search found. A kind that is not here leaves the choice as it is.
KIND_CATEGORIES = {
    "Stock": "Stock",
    "ETF": "ETF",
    "Fund": "Fund",
    "Crypto": "Crypto",
    "Money market": "Cash/Money Market",
    "Future": "Commodity",
}

#: The fields that describe one trade, emptied once it is saved. The date, category, action
#: and currency stay for the next one, and so does the Dry Powder sync.
TRADE_FIELDS = {
    "tx_ticker": "", "tx_isin": "", "tx_name": "",
    "tx_quantity": None, "tx_price": None, "tx_fees": 0.0, "tx_fx": 0.0,
}

if st.session_state.pop("tx_reset", False):
    # Set, not removed: the browser would go on showing the value of a widget whose state is merely gone.
    st.session_state.update(TRADE_FIELDS)
    for held in ("tx_picked", "tx_listing", "tx_fetched", "tx_total_for"):
        st.session_state.pop(held, None)


def _detect_currency(identifier: str) -> str | None:
    """Listing currency for a ticker or ISIN; cached per symbol for a day."""
    return md.fetch_listing_currency((identifier,)).get(identifier)


def _fx_blocked(currency: str, day: date) -> str:
    return (
        f"Save blocked: no EUR{currency} rate available for {day:%Y-%m-%d}. Markets may "
        f"have been closed, or today's rate may not have published yet. Use the previous "
        f"business day, or enter the rate in the FX override field."
    )


def _trade_fx(currency: str, manual_fx: float, day: date) -> tuple[float | None, str]:
    """``(rate, source)``. An FX rate that cannot be established is ``None``, never a default:
    a silent 1.0 permanently understates a USD cost basis by ~15%, and the wrong rate gets
    frozen into the database.
    """
    if currency == pm.BASE_CCY:
        return 1.0, "base"
    if manual_fx > 0:
        return manual_fx, "manual"
    return md.fetch_historical_fx(day.strftime("%Y-%m-%d"), currency), "yahoo"


def _clean(field: str) -> str:
    return str(st.session_state.get(field) or "").upper().strip()


def _identifiers() -> tuple[str, str]:
    """``(ticker, ISIN)`` as the boxes say. An ISIN searched in the Ticker box and left there is the ISIN."""
    ticker, isin = _clean("tx_ticker"), _clean("tx_isin")
    return ("", isin or ticker) if search.is_isin(ticker) else (ticker, isin)


def _forget_fetched_price() -> None:
    """A fetched close belongs to one asset on one day. Changing either takes it out of the form; a price typed over it stays."""
    fetched = st.session_state.pop("tx_fetched", None)
    if fetched and st.session_state.get("tx_price") == fetched["price"]:
        st.session_state["tx_price"] = None


def _isin_for(hit: search.Hit, query: str) -> tuple[str, str]:
    """The ISIN to log a search result under and, when there is none, what to tell the reader.

    A position is keyed by its ISIN first and its ticker second, so a holding already
    logged keeps the identifiers it was logged with: an ISIN it did not have would open a
    second position.
    """
    for doc in get_all_transactions():
        if hit.ref in (doc.get("ticker"), doc.get("resolved_ticker")):
            isin = str(doc.get("isin") or "")
            return isin, "" if isin else (
                f"Left empty: {hit.ref} is already logged without an ISIN, and one here would open a second position."
            )
    if search.is_isin(query):
        return query.upper(), ""
    if hit.kind not in search.ISIN_KINDS:
        return "", ""
    try:
        return md.fetch_isin(hit.ref, hit.name), ""
    except Exception:
        return "", f"No ISIN found for {hit.ref}. Enter it here if you have it."


def _pick(hit: search.Hit, query: str) -> None:
    """Fill the form from a search result. Yahoo's symbol is the ticker: it is the one the dashboard can price."""
    isin, note = _isin_for(hit, query)
    # One typed by hand stays when nothing better was found; one from the previous pick does not.
    typed = _clean("tx_isin")
    if not isin and typed != st.session_state.get("tx_picked", {}).get("isin"):
        isin = typed
    st.session_state["tx_ticker"] = hit.ref
    st.session_state["tx_isin"] = isin
    st.session_state["tx_name"] = "" if hit.name == hit.ref else hit.name  # Yahoo gave it no name
    if hit.kind in KIND_CATEGORIES:
        st.session_state["tx_category"] = KIND_CATEGORIES[hit.kind]
    st.session_state["tx_listing"] = (hit.ref, _detect_currency(hit.ref))
    st.session_state["tx_picked"] = {"ticker": hit.ref, "isin": isin, "note": note}
    if st.session_state.get("tx_fetched", {}).get("symbol") != hit.ref:  # otherwise it is still this asset's price
        _forget_fetched_price()


def _results(query: str) -> None:
    """What the Ticker box finds, each a button that fills the form with it."""
    try:
        hits = search.rank(query, [search.yahoo_hits(md.search_yahoo(query))])
    except Exception as exc:
        st.caption(f"Yahoo Finance did not answer: {md.reason(exc)}")
        return
    if not hits:
        st.caption(f"Nothing found for “{query}”.")
    found = st.container(gap=None)  # a list, not a column of separate buttons
    for n, hit in enumerate(hits):
        found.button(search.hit_label(hit), key=f"tx_hit_{n}", on_click=_pick, args=(hit, query),
                     type="tertiary", icon=":material/add:")


def _listed_as(ticker: str) -> str | None:
    """Yahoo's symbol for a ticker: itself when Yahoo has it, or its listing when typed without one: P911 is P911.DE.

    Only that very ticker. Yahoo's search also answers with whatever looks alike, and
    the price of another company would be worse than no price. Asked before Yahoo is asked
    for the ticker's prices or currency, as those log an error for a symbol it does not know.
    """
    try:
        symbols = [hit.ref for hit in search.yahoo_hits(md.search_yahoo(ticker))]
    except Exception:
        return None
    return ticker if ticker in symbols else next((s for s in symbols if s.split(".")[0] == ticker), None)


def _price_symbols(ticker: str, isin: str):
    """What to ask Yahoo for a price, in order. Lazy: an ISIN that may not be needed costs no request."""
    if ticker:
        listed = _listed_as(ticker)
        if listed:
            yield listed
        elif " " not in ticker:  # the search is not exhaustive, but a name left in the box is no symbol
            yield ticker
    if isin:
        yield isin


def _fetch_price() -> None:
    ticker, isin = _identifiers()
    day = st.session_state["tx_date"]
    if not ticker and not isin:
        st.session_state["tx_price_error"] = "Enter a Ticker or ISIN first."
        return
    for symbol in _price_symbols(ticker, isin):
        try:
            close, on = md.fetch_close_on(symbol, day)
        except Exception:
            continue
        break
    else:
        st.session_state["tx_price_error"] = (
            f"No price found for {ticker or isin} up to {day:%Y-%m-%d}. "
            f"Yahoo Finance may list it under another symbol: press Enter in the Ticker box and pick one from the list."
        )
        return
    # A ticker Yahoo does not know would be unpriced on the dashboard too.
    renamed = ""
    if ticker and symbol != ticker:
        renamed = ticker
        st.session_state["tx_ticker"] = "" if symbol == isin else symbol
    listing_ccy = _detect_currency(symbol)
    st.session_state["tx_listing"] = (_identifiers()[0] or isin, listing_ccy)
    # Yahoo's closes are single precision: 46.72 arrives as 46.720001220703125.
    fetched_price = round(close, 4)
    st.session_state["tx_price"] = fetched_price
    st.session_state["tx_fetched"] = {
        "price": fetched_price, "on": on, "symbol": symbol, "currency": listing_ccy, "renamed": renamed,
    }


stored_accounts, stored_problems = pm.clean_cash_accounts(get_cash_accounts())
dry_powder = [a for a in stored_accounts if a.category != pm.RESERVE_CATEGORY]

# No st.form: nothing here is saved until the Save button is clicked, and Enter in a field
# only confirms that field. The search, Fetch Price and the total all work on what the
# fields say at that moment, which a form would hold back until it is submitted.
col1, col2 = st.columns(2)
with col1:
    date_input = st.date_input("Transaction Date", value=date.today(), key="tx_date", on_change=_forget_fetched_price)
    category = st.selectbox("Asset Category", options=ASSET_CLASSES, key="tx_category")
    typed_ticker = st.text_input(
        "Ticker", key="tx_ticker", placeholder="Ticker, name or ISIN, then Enter", icon=":material/search:",
        on_change=_forget_fetched_price,
    ).strip()
    # Until a result is picked, what the box says is a search, as on the Custom charts page.
    picked = st.session_state.get("tx_picked", {})
    if typed_ticker and typed_ticker != picked.get("ticker"):
        _results(typed_ticker)
    st.text_input("ISIN", key="tx_isin", on_change=_forget_fetched_price)
    if picked.get("note") and typed_ticker == picked.get("ticker") and not _clean("tx_isin"):
        st.caption(picked["note"])
    name = st.text_input("Asset Name", key="tx_name")

    ticker, isin = _identifiers()
    identifier = ticker or isin
    # Keyed to the identifier, so changing the ticker invalidates a previously
    # detected currency instead of leaving a stale one behind.
    listing = st.session_state.get("tx_listing")
    detected = listing[1] if listing and listing[0] == identifier else None

with col2:
    action = st.selectbox("Action", ["Buy", "Sell"], key="tx_action")
    currency = st.selectbox("Transaction Currency", SUPPORTED_CCY, key="tx_currency")
    st.caption("The currency the cash left your account in.")

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

    # Blank until entered, as a 0 would have to be deleted first. Each reads None while it is.
    quantity = st.number_input("Quantity", value=None, min_value=0.0, step=0.01, format="%.4f", key="tx_quantity")

    # --- Fetch Historical Price ---
    st.button("Fetch Price", key="fetch_price", on_click=_fetch_price)
    price_error = st.session_state.pop("tx_price_error", None)
    if price_error:
        st.error(price_error)

    price = st.number_input("Price (Nominal)", value=None, min_value=0.0, step=0.01, format="%.4f", key="tx_price")
    fetched = st.session_state.get("tx_fetched")
    if fetched and price == fetched["price"]:
        st.caption(
            (f"{fetched['renamed']} is {fetched['symbol']} on Yahoo Finance, so the ticker now says that. "
             if fetched["renamed"] and ticker == fetched["symbol"] else "")
            + f"Close of {fetched['on']:%Y-%m-%d} on {fetched['symbol']}, in {fetched['currency'] or 'an unknown currency'}."
        )
        if fetched["currency"] and fetched["currency"] != currency:
            st.warning(
                f"That price is in {fetched['currency']} and this transaction is in {currency}. "
                f"Enter what you paid in {currency}, or change the transaction currency."
            )
    fees = st.number_input("Fees", min_value=0.0, step=0.01, key="tx_fees")
    st.caption("Included in the cost basis.")

    manual_fx = st.number_input(
        f"FX override ({currency} per 1 EUR, 0 = look up automatically)",
        min_value=0.0, step=0.0001, format="%.4f", key="tx_fx",
    )

# --- Total ---
# Kept with what it was calculated from, so a total never outlives a change to any of it.
total_inputs = (action, currency, quantity, price, fees, manual_fx, date_input)
if st.button("Calculate total", key="calculate_total"):
    if quantity and price:
        st.session_state["tx_total_for"] = total_inputs
    else:
        st.error("Set a quantity and a price first.")

total_eur = None
if st.session_state.get("tx_total_for") == total_inputs:
    total = pm.trade_total(action, quantity, price, fees)
    total_fx, _ = _trade_fx(currency, manual_fx, date_input)
    total_label = "Total cost" if action == "Buy" else "Total proceeds"
    st.metric(total_label, f"€{total:,.2f}" if currency == pm.BASE_CCY else f"{total:,.2f} {currency}")
    if total_fx is None:
        st.warning(_fx_blocked(currency, date_input))
    else:
        total_eur = total / total_fx
        if currency != pm.BASE_CCY:
            st.caption(f"€{total_eur:,.2f} at FX {total_fx:.4f}.")

# --- Dry Powder ---
sync = st.checkbox(
    "Sync with Dry Powder reserve", value=True, key="tx_sync",
    help="Saving a Buy takes its cost, fees included, from the Dry Powder account. Saving a Sell adds its proceeds.",
)
sync_account = None
if sync and not dry_powder:
    st.warning("There is no Dry Powder account to sync with. Add one under Cash accounts below, or untick this box.")
elif sync:
    balances = {a.name: a.balance_eur for a in dry_powder}
    sync_name = dry_powder[0].name
    if len(dry_powder) > 1:
        sync_name = st.selectbox(
            "Dry Powder account", list(balances), key="tx_sync_account",
            format_func=lambda account: f"{account} (€{balances[account]:,.2f})",
        )
    sync_account = next(a for a in dry_powder if a.name == sync_name)
    effect = "takes its cost from" if action == "Buy" else "adds its proceeds to"
    line = f"This {action.lower()} {effect} {search.plain(sync_name)}, which holds €{sync_account.balance_eur:,.2f}."
    if total_eur is not None:
        try:
            line += f" That leaves €{pm.balance_after_trade(sync_account.balance_eur, action, total_eur):,.2f}."
        except ValueError as exc:
            st.warning(f"Saving will be refused: {exc}.")
    st.caption(line)


def _save_trade() -> None:
    if not ticker and not isin:
        st.error("ERROR: You must provide at least a Ticker or an ISIN.")
        return
    if " " in ticker:
        st.error("ERROR: The Ticker box holds a search, not a ticker. Pick the asset from the list under it.")
        return
    if not quantity:
        st.error("ERROR: Quantity must be greater than 0.")
        return
    if not price:
        st.error("ERROR: Price must be greater than 0.")
        return
    if sync and sync_account is None:
        st.error("Not saved: there is no Dry Powder account to sync with. Add one under Cash accounts below, or untick the box.")
        return

    fx_rate, fx_source = _trade_fx(currency, manual_fx, date_input)
    if fx_rate is None:
        st.error(_fx_blocked(currency, date_input))
        return

    try:
        # A ticker Yahoo does not know is saved as its listing, as Fetch Price does:
        # otherwise the position sits on the dashboard with no price.
        saved_ticker, listing_ccy = ticker, detected
        if not listing_ccy:
            saved_ticker = (_listed_as(ticker) or ticker) if ticker else ""
            listing_ccy = _detect_currency(saved_ticker or isin)

        resolved = None
        if isin and not saved_ticker:
            # Stored so the pricing path never has to run a live ISIN
            # search, which can resolve to another exchange listing.
            resolved = md.resolve_isin(isin)

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
            ticker=saved_ticker,
            isin=isin,
            name=name,
            listing_ccy=listing_ccy,
            resolved_ticker=resolved,
        )
        trade_data["timestamp"] = get_timestamp()

        synced = ""
        if sync:
            # The balance as it is now, not the minute-old copy this page was drawn from.
            clear_cash_cache()
            current, _ = pm.clean_cash_accounts(get_cash_accounts())
            account = next((a for a in current if a.name == sync_account.name), None)
            if account is None:
                st.error(f"Not saved: there is no account called {sync_account.name} any more.")
                return
            try:
                after = pm.balance_after_trade(account.balance_eur, action, trade_data["cost_eur"])
            except ValueError as exc:
                st.error(f"Not saved: {exc}. Top it up under Cash accounts below, or untick the sync box.")
                return
            record_transaction_with_balance(trade_data, account.name, after, date.today().isoformat())
            synced = f" {search.plain(account.name)}: €{account.balance_eur:,.2f} → €{after:,.2f}."
            # The cash table keeps its own copy of what it was given; a new key redraws it.
            st.session_state["cash_rev"] = st.session_state.get("cash_rev", 0) + 1
        else:
            record_transaction(trade_data)
    except Exception as exc:
        st.error(f"Error processing transaction: {exc}")
        return

    moved = "cost" if action == "Buy" else "proceeds"
    st.session_state["tx_flash"] = (
        f"Recorded {action} of {quantity:g} {saved_ticker or isin} "
        f"— {moved} €{trade_data['cost_eur']:,.2f} "
        f"(FX {fx_rate:.4f}, {fx_source}).{synced}"
    )
    if not listing_ccy:
        st.session_state["tx_flash_warning"] = (
            f"Yahoo Finance could not confirm {saved_ticker or isin}, so the dashboard may not be able to price it. "
            f"Select the row in the history log to change its ticker to the listing Yahoo Finance uses."
        )
    st.session_state["tx_reset"] = True
    st.session_state["tx_log_rev"] = st.session_state.get("tx_log_rev", 0) + 1
    st.rerun()


if st.button("🚀 Save Transaction", key="save_trade", width="stretch"):
    _save_trade()


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
    with st.form(f"edit_form_{doc_id}", enter_to_submit=False):
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

        st.caption("Editing or deleting a transaction does not move Dry Powder: correct the balance under Cash accounts.")
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
flash_warning = st.session_state.pop("tx_flash_warning", None)
if flash_warning:
    st.warning(flash_warning)

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


# ======================================================================================================================
# --- CASH ACCOUNTS ---
# ======================================================================================================================

st.markdown("---")
st.subheader("Cash accounts")
st.caption(
    "One row per bank account, balance in EUR. **Cash** is money in current accounts: your "
    "day-to-day cash and emergency fund, shown on the dashboard as the cash reserve and kept "
    "outside the portfolio. **Dry Powder** is money set aside to invest: part of the portfolio, "
    "its weights, charts and design. Add or change rows, then save. To delete an account, either "
    "tick the box that appears at the left of its row when you point at it, click the bin above "
    "the table and save, or use Delete an account below."
)

cash_flash = st.session_state.pop("cash_flash", None)
if cash_flash:
    st.success(cash_flash)

for problem in stored_problems:
    st.warning(f"Stored account skipped: {problem}")

with st.form("cash_form"):
    edited = st.data_editor(
        pd.DataFrame(
            [{"name": a.name, "category": a.category, "balance_eur": a.balance_eur} for a in stored_accounts],
            columns=["name", "category", "balance_eur"],
        ),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        # A fresh key after each save: the editor keeps its edits as changes on top
        # of the data it was given, and replaying them onto the saved list would
        # add every new row a second time.
        key=f"cash_editor_{st.session_state.get('cash_rev', 0)}",
        column_config={
            "name": st.column_config.TextColumn("Account", required=True),
            "category": st.column_config.SelectboxColumn(
                "Category", options=list(pm.CASH_CATEGORIES), required=True, default="Cash"
            ),
            "balance_eur": st.column_config.NumberColumn(
                "Balance (EUR)", min_value=0.0, format="€ %.2f", required=True
            ),
        },
    )
    save_cash = st.form_submit_button("Save balances", type="primary", key="save_cash")

if save_cash:
    accounts, errors = pm.clean_cash_accounts(edited.to_dict("records"))
    if errors:
        for error in errors:
            st.error(f"Not saved: {error}")
    else:
        try:
            # Unchanged accounts keep their dates; only a new or changed balance is dated today.
            save_cash_accounts(pm.account_documents(accounts, stored_accounts, date.today().isoformat()))
        except Exception as exc:
            st.error(f"Error saving balances: {exc}")
        else:
            st.session_state["cash_flash"] = (
                f"Saved {len(accounts)} account(s), €{sum(a.balance_eur for a in accounts):,.2f} in total."
            )
            st.session_state["cash_rev"] = st.session_state.get("cash_rev", 0) + 1
            st.rerun()

if stored_accounts:
    saved = max((a.updated for a in stored_accounts if a.updated), default=None)
    st.caption(
        " · ".join(
            f"{c} €{sum(a.balance_eur for a in stored_accounts if a.category == c):,.2f}"
            for c in pm.CASH_CATEGORIES
        )
        + (f" · last saved {saved}" if saved else "")
    )

    # The table can delete rows too, but only through a hidden row checkbox and a bin
    # icon; this is the obvious way. Like a transaction, it asks for a confirmation.
    # Keyed by the save counter, so the controls start fresh after every change.
    rev = st.session_state.get("cash_rev", 0)
    pick, confirm, delete = st.columns([3, 3, 1], vertical_alignment="bottom")
    doomed = pick.selectbox("Delete an account", [a.name for a in stored_accounts], key=f"cash_delete_pick_{rev}")
    sure = confirm.checkbox("Yes, delete it permanently", key=f"cash_delete_confirm_{rev}")
    if delete.button("Delete", key=f"cash_delete_{rev}", width="stretch"):
        if not sure:
            st.error("Tick the confirmation box to delete the account.")
        else:
            try:
                # The others are unchanged, so they keep their dates.
                remaining = [a for a in stored_accounts if a.name != doomed]
                save_cash_accounts(pm.account_documents(remaining, stored_accounts, date.today().isoformat()))
            except Exception as exc:
                st.error(f"Error deleting the account: {exc}")
            else:
                st.session_state["cash_flash"] = f"Deleted the account {doomed}."
                st.session_state["cash_rev"] = rev + 1
                st.rerun()
