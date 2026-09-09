"""Firestore document loading, including the schema quirks found in the live data."""

from datetime import date, datetime

import pytest

from portfolio_math import (
    audit_transaction,
    load_transactions,
    trade_document,
    transaction_from_doc,
)


def doc(**over):
    base = {
        "date": "2025-09-22",
        "action": "Buy",
        "quantity": 100.0,
        "price_nominal": 10.0,
        "currency": "EUR",
        "fx_rate_at_buy": 1.1708,
        "cost_eur": 1000.0,
        "isin": "ES0000000000",
        "name": "Test Fund",
        "category": "Fund",
    }
    base.update(over)
    return base


def test_eur_doc_ignores_stored_eurusd_rate():
    """The writer stores an EURUSD rate on EUR trades and never applies it.

    Honouring fx_rate_at_buy=1.1708 here would understate this basis by ~15%.
    """
    tx, problems = transaction_from_doc(doc())
    assert tx.fx_rate == 1.0
    assert tx.gross_eur == pytest.approx(1000.0)
    assert problems == []


def test_usd_doc_uses_stored_rate():
    tx, _ = transaction_from_doc(doc(currency="USD", price_nominal=11.70, fx_rate_at_buy=1.17))
    assert tx.gross_eur == pytest.approx(1000.0)


def test_usd_doc_with_fx_exactly_one_is_flagged():
    tx, problems = transaction_from_doc(doc(currency="USD", fx_rate_at_buy=1.0))
    assert tx is not None  # still usable, but the user is told
    assert any("silent fallback" in p for p in problems)


def test_usd_doc_with_no_fx_is_rejected_not_defaulted():
    tx, problems = transaction_from_doc(
        {**doc(currency="USD"), "fx_rate_at_buy": None}
    )
    assert tx is None
    assert any("no usable FX rate" in p for p in problems)


def test_legacy_price_field_is_accepted_and_noted():
    legacy = doc()
    del legacy["price_nominal"]
    legacy["price"] = 10.0
    tx, problems = transaction_from_doc(legacy)
    assert tx.price_nominal == 10.0
    assert any("legacy" in p for p in problems)


def test_missing_currency_assumed_base():
    bare = doc()
    del bare["currency"]
    tx, problems = transaction_from_doc(bare)
    assert tx.currency == "EUR"
    assert any("assumed EUR" in p for p in problems)


def test_stored_cost_eur_is_recomputed_not_trusted():
    """Live data has cost_eur=2830.84 against quantity x price = 2830.98."""
    tx, _ = transaction_from_doc(
        doc(quantity=352.7257, price_nominal=8.0260, cost_eur=2830.84)
    )
    assert tx.gross_eur == pytest.approx(2830.9765, abs=1e-3)


def test_audit_reports_denormalised_drift():
    notes = audit_transaction(doc(quantity=352.7257, price_nominal=8.0260, cost_eur=2830.84))
    assert any("disagrees" in n for n in notes)


def test_audit_is_quiet_on_a_consistent_doc():
    notes = audit_transaction(doc(quantity=100.0, price_nominal=10.0, cost_eur=1000.0, fees=0.0))
    assert not any("disagrees" in n for n in notes)


def test_timestamp_becomes_ordering_seq():
    tx, _ = transaction_from_doc(doc(timestamp=datetime(2025, 9, 22, 14, 30)))
    assert tx.seq > 0


@pytest.mark.parametrize(
    "bad",
    [
        {"isin": "", "ticker": ""},
        {"date": "not-a-date"},
        {"quantity": 0},
        {"quantity": -5},
        {"price_nominal": None, "price": None},
    ],
)
def test_unusable_docs_are_skipped_with_a_reason(bad):
    tx, problems = transaction_from_doc(doc(**bad))
    assert tx is None
    assert problems


def test_load_transactions_reports_and_continues():
    txs, problems = load_transactions([doc(), doc(quantity=0), doc(isin="X2")])
    assert len(txs) == 2  # the bad one is dropped, the good ones survive
    assert any("skipped" in p for p in problems)


def test_asset_identity_prefers_isin_matching_existing_grouping():
    tx, _ = transaction_from_doc(doc(isin="ES0001", ticker="ABC"))
    assert tx.asset_id == "ES0001"
    assert tx.ticker == "ABC"  # still carried for display and price resolution


def test_listing_currency_is_read_from_the_document():
    """The writer has stored it since schema v2; the reader must carry it."""
    tx, _ = transaction_from_doc(doc(listing_ccy="usd"))
    assert tx.listing_ccy == "USD"


def test_listing_currency_absent_is_none_not_eur():
    tx, _ = transaction_from_doc(doc())
    assert tx.listing_ccy is None


def test_net_eur_is_cost_on_a_buy_and_proceeds_on_a_sell(tx):
    buy = tx(qty=10, price=100.0, fees=9.95)
    sell = tx(action="Sell", qty=10, price=100.0, fees=9.95)
    assert buy.net_eur == pytest.approx(1009.95)
    assert sell.net_eur == pytest.approx(990.05)


def test_net_eur_converts_fees_at_the_trade_fx(tx):
    usd = tx(qty=100, price=11.70, fees=11.70, ccy="USD", fx=1.17)
    assert usd.net_eur == pytest.approx(1010.0)


def test_audit_is_quiet_on_a_fee_bearing_buy():
    """Regression: the writer stores cost *including* fees, and the audit compared
    it against gross alone, so every trade with a fee was flagged as drifted."""
    notes = audit_transaction(doc(quantity=10.0, price_nominal=100.0, fees=9.95, cost_eur=1009.95))
    assert not any("disagrees" in n for n in notes)


def test_audit_is_quiet_on_a_fee_bearing_sell():
    notes = audit_transaction(
        doc(action="Sell", quantity=10.0, price_nominal=100.0, fees=9.95, cost_eur=990.05)
    )
    assert not any("disagrees" in n for n in notes)


def test_audit_flags_a_sell_stored_with_fees_added():
    """Sells written before this fix carry gross + fees. That is a wrong stored
    value, and the audit should say so rather than accept either convention."""
    notes = audit_transaction(
        doc(action="Sell", quantity=10.0, price_nominal=100.0, fees=9.95, cost_eur=1009.95)
    )
    assert any("disagrees" in n and "+19.90" in n for n in notes)


def test_audit_still_catches_real_drift_on_a_fee_bearing_buy():
    notes = audit_transaction(doc(quantity=10.0, price_nominal=100.0, fees=9.95, cost_eur=1000.00))
    assert any("disagrees" in n for n in notes)


# --- writing documents -------------------------------------------------------


def _trade(**over):
    base = dict(
        trade_date=date(2025, 9, 22),
        category="Fund",
        action="Buy",
        currency="EUR",
        quantity=10.0,
        price_nominal=100.0,
        fees=9.95,
        fx_rate=1.0,
        fx_source="base",
        isin="ES0000000000",
        name="Test Fund",
    )
    base.update(over)
    return trade_document(**base)


def test_trade_document_round_trips_through_the_loader():
    tx, problems = transaction_from_doc(_trade())
    assert problems == []
    assert (tx.trade_date, tx.action, tx.currency) == (date(2025, 9, 22), "Buy", "EUR")
    assert (tx.quantity, tx.price_nominal, tx.fees, tx.fx_rate) == (10.0, 100.0, 9.95, 1.0)
    assert (tx.isin, tx.name, tx.category) == ("ES0000000000", "Test Fund", "Fund")


def test_trade_document_cost_eur_is_what_the_audit_checks():
    """Buy stores cost incl. fees, sell stores proceeds net of fees; both audit clean."""
    buy, sell = _trade(), _trade(action="Sell")
    assert buy["cost_eur"] == pytest.approx(1009.95)
    assert sell["cost_eur"] == pytest.approx(990.05)
    assert not any("disagrees" in n for n in audit_transaction(buy))
    assert not any("disagrees" in n for n in audit_transaction(sell))
    assert buy["schema_version"] == 2


def test_trade_document_usd_converts_at_the_given_rate():
    doc = _trade(
        currency="usd", fx_rate=1.17, fx_source="yahoo", price_nominal=11.70, quantity=100.0, fees=0.0
    )
    assert doc["currency"] == "USD"
    assert doc["cost_eur"] == pytest.approx(1000.0)
    tx, problems = transaction_from_doc(doc)
    assert problems == []
    assert tx.gross_eur == pytest.approx(1000.0)


def test_trade_document_omits_empty_optional_fields_and_uppercases_codes():
    doc = _trade(ticker=" abc ", isin="", name="  ", listing_ccy="usd", resolved_ticker=None)
    assert doc["ticker"] == "ABC"
    assert doc["listing_ccy"] == "USD"
    assert "isin" not in doc and "name" not in doc and "resolved_ticker" not in doc
    assert doc["fees"] == 9.95  # always present, even when it would be 0.0
    assert _trade(fees=0.0)["fees"] == 0.0


@pytest.mark.parametrize(
    "bad",
    [
        dict(action="Transfer"),
        dict(quantity=0.0),
        dict(price_nominal=-1.0),
        dict(fx_rate=0.0),
        dict(fx_rate=float("nan")),
        dict(isin="", ticker=None),
        dict(currency="EUR", fx_rate=1.17),  # an EUR trade carrying a USD rate is a bug
    ],
)
def test_trade_document_refuses_unusable_input(bad):
    with pytest.raises(ValueError):
        _trade(**bad)
