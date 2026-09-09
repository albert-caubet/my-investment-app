"""Headless smoke tests for both pages, on canned data. No network, no Firestore.

``AppTest`` runs the entry point in-process. Every network and database function
the pages call is replaced before the run, so what is exercised is the page code
itself: loading, replaying, rendering, and the create / edit / delete flows.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import database
import market_data as md

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "transactions_sample.json").read_text("utf-8"))
DAYS = pd.bdate_range("2024-06-03", periods=300)

# Newest trade in the fixture, so it is row 0 of the date-descending history log.
NEWEST_ID = "eur_drifted_cost"


@pytest.fixture
def canned(monkeypatch):
    """Canned data sources, plus a recorder for every write the pages attempt."""
    writes = {"added": [], "updated": [], "deleted": []}

    monkeypatch.setattr(database, "get_all_transactions", lambda: [dict(d) for d in FIXTURE])
    monkeypatch.setattr(database, "clear_transaction_cache", lambda: None)
    monkeypatch.setattr(database, "record_transaction", lambda data: writes["added"].append(data))
    monkeypatch.setattr(
        database, "update_transaction", lambda doc_id, data: writes["updated"].append((doc_id, data))
    )
    monkeypatch.setattr(database, "delete_transaction", lambda doc_id: writes["deleted"].append(doc_id))

    def price_history(tickers, period, *, adjusted=True):
        rng = np.random.default_rng(0)
        return pd.DataFrame(
            {t: 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(DAYS))) for t in tickers},
            index=DAYS,
        )

    monkeypatch.setattr(
        md, "fetch_listing_currency", lambda tickers: {t: ("USD" if t == "USST" else "EUR") for t in tickers}
    )
    monkeypatch.setattr(md, "fetch_listing_name", lambda symbol: "Canned Name")
    monkeypatch.setattr(md, "fetch_spot_prices", lambda tickers: {t: 12.0 for t in tickers})
    monkeypatch.setattr(md, "fetch_spot_fx", lambda currencies: {"EUR": 1.0, "USD": 1.17})
    monkeypatch.setattr(md, "fetch_historical_fx", lambda date_iso, ccy: 1.0 if ccy == "EUR" else 1.17)
    monkeypatch.setattr(md, "fetch_price_history", price_history)
    monkeypatch.setattr(md, "fetch_fx_history", lambda currencies, period: pd.DataFrame({"USD": 1.1}, index=DAYS))
    monkeypatch.setattr(
        md,
        "fetch_hicp_index",
        lambda area=None: {"2025-01": 125.0, "2025-02": 125.3, "2025-06": 126.5, "2025-09": 127.17, "2025-12": 128.28},
    )
    monkeypatch.setattr(md, "fetch_hicp_annual_rate", lambda area=None: (0.03, "2025-12"))
    monkeypatch.setattr(md, "resolve_isin", lambda isin: None)
    return writes


def _app() -> AppTest:
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def _transactions_page(at: AppTest) -> AppTest:
    return at.switch_page("pages/transactions.py").run()


def _select_history_row(at: AppTest, row: int) -> AppTest:
    """Run with `row` selected in the history table.

    AppTest has no frontend to hold a dataframe selection, so a seeded selection
    is consumed by exactly one run and reads back empty on the next. Call this
    for every run that should still have the row selected -- after a click, not
    just once -- which mirrors the browser, where the widget keeps the selection.
    """
    at.session_state["tx_log_0"] = {"selection": {"rows": [row], "columns": []}}
    return at.run()


# --- portfolio ---------------------------------------------------------------


def test_portfolio_page_renders_on_canned_data(canned):
    at = _app().run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    for expected in ("Total Value (EUR)", "Unrealised PnL (EUR)", "Realised PnL (EUR)", "Total PnL (EUR)"):
        assert expected in labels
    # Nothing was left unvalued: every canned symbol had a price and a currency.
    assert not any("could not be valued" in w.value for w in at.warning)
    assert canned == {"added": [], "updated": [], "deleted": []}


def test_portfolio_refuses_to_value_an_unknown_listing_currency(canned, monkeypatch):
    monkeypatch.setattr(md, "fetch_listing_currency", lambda tickers: {t: None for t in tickers})
    at = _app().run()
    assert not at.exception
    # The fixture stores no listing currency, so every position is unvalued rather
    # than quietly treated as EUR.
    assert any("could not be valued" in w.value for w in at.warning)


# --- transactions: create ----------------------------------------------------


def test_transactions_page_renders_the_history(canned):
    at = _transactions_page(_app().run())
    assert not at.exception
    assert len(at.dataframe) == 1
    assert len(at.dataframe[0].value) == len(FIXTURE)


def test_logging_a_trade_writes_a_schema_v2_document(canned):
    at = _transactions_page(_app().run())
    at.text_input[0].set_value("ABC")  # ticker
    at.number_input[0].set_value(10.0)  # quantity
    at.number_input[1].set_value(100.0)  # price
    at.number_input[2].set_value(9.95)  # fees
    at.button(key="save_trade").click().run()

    assert not at.exception
    assert at.success
    assert len(canned["added"]) == 1
    doc = canned["added"][0]
    assert doc["ticker"] == "ABC"
    assert doc["currency"] == "EUR"
    assert doc["fx_rate"] == 1.0
    assert doc["cost_eur"] == pytest.approx(1009.95)
    assert doc["schema_version"] == 2
    assert "timestamp" in doc


def test_logging_without_an_identifier_is_refused(canned):
    at = _transactions_page(_app().run())
    at.number_input[0].set_value(10.0)
    at.number_input[1].set_value(100.0)
    at.button(key="save_trade").click().run()
    assert at.error
    assert canned["added"] == []


# --- transactions: edit and delete -------------------------------------------


def test_selecting_a_row_opens_the_edit_form_prefilled(canned):
    at = _select_history_row(_transactions_page(_app().run()), 0)
    assert not at.exception
    original = next(d for d in FIXTURE if d["_doc_id"] == NEWEST_ID)
    assert at.number_input(key=f"edit_quantity_{NEWEST_ID}").value == pytest.approx(original["quantity"])
    assert at.number_input(key=f"edit_price_{NEWEST_ID}").value == pytest.approx(original["price_nominal"])
    assert at.text_input(key=f"edit_isin_{NEWEST_ID}").value == original["isin"]


def test_saving_an_edit_rewrites_the_document_and_keeps_its_timestamp(canned):
    at = _select_history_row(_transactions_page(_app().run()), 0)
    at.number_input(key=f"edit_quantity_{NEWEST_ID}").set_value(300.0)
    at.button(key=f"edit_save_{NEWEST_ID}").click()
    at = _select_history_row(at, 0)

    assert not at.exception
    assert len(canned["updated"]) == 1
    doc_id, doc = canned["updated"][0]
    assert doc_id == NEWEST_ID
    assert doc["quantity"] == 300.0
    assert doc["price_nominal"] == pytest.approx(8.026)
    assert doc["currency"] == "EUR" and doc["fx_rate"] == 1.0
    # The fixture row stored a drifted cost_eur; the rewrite recomputes it.
    assert doc["cost_eur"] == pytest.approx(300.0 * 8.026)
    assert doc["schema_version"] == 2
    assert "updated_at" in doc
    # The rerun after saving shows the outcome and, by re-keying the table,
    # clears the selection so the form is gone.
    assert at.success
    assert not any(b.label == "Save changes" for b in at.button)


def test_editing_a_usd_trade_keeps_its_stored_rate_when_date_is_unchanged(canned):
    at = _transactions_page(_app().run())
    log = at.dataframe[0].value
    row = int(log.index[log["_doc_id"] == "usd_buy"][0])
    at = _select_history_row(at, row)
    at.number_input(key="edit_fees_usd_buy").set_value(5.0)
    at.button(key="edit_save_usd_buy").click()
    at = _select_history_row(at, row)

    assert not at.exception
    (doc_id, doc), = canned["updated"]
    assert doc_id == "usd_buy"
    assert doc["fx_rate"] == pytest.approx(1.17)  # stored rate, not re-fetched
    assert doc["cost_eur"] == pytest.approx((11.7 * 100.0 + 5.0) / 1.17)


def test_delete_requires_the_confirmation_box(canned):
    at = _select_history_row(_transactions_page(_app().run()), 0)
    at.button(key=f"edit_delete_{NEWEST_ID}").click()
    at = _select_history_row(at, 0)
    assert at.error
    assert canned["deleted"] == []

    at.checkbox(key=f"edit_confirm_{NEWEST_ID}").check()
    at.button(key=f"edit_delete_{NEWEST_ID}").click()
    at = _select_history_row(at, 0)
    assert not at.exception
    assert canned["deleted"] == [NEWEST_ID]
    assert at.success


def test_a_legacy_document_can_be_opened_and_upgraded(canned):
    """The legacy row has `price` and no currency; saving it writes schema v2."""
    at = _transactions_page(_app().run())
    log = at.dataframe[0].value
    row = int(log.index[log["_doc_id"] == "legacy_schema"][0])
    at = _select_history_row(at, row)
    assert at.number_input(key="edit_price_legacy_schema").value == pytest.approx(50.0)

    at.button(key="edit_save_legacy_schema").click()
    at = _select_history_row(at, row)
    assert not at.exception
    (doc_id, doc), = canned["updated"]
    assert doc_id == "legacy_schema"
    assert doc["price_nominal"] == 50.0 and "price" not in doc
    assert doc["currency"] == "EUR" and doc["schema_version"] == 2
