"""A trade's cash and the Dry Powder balance it moves: the sums, the batched write, and the close it is priced at.

Firestore and Yahoo are replaced here, so none of this touches the network.
"""

from datetime import date

import pandas as pd
import pytest

import database
import market_data as md
from portfolio_math import balance_after_trade, trade_document, trade_total


def test_a_buy_costs_its_fees_and_a_sell_loses_them():
    assert trade_total("Buy", 10, 100.0, 5.0) == 1005.0
    assert trade_total("Sell", 10, 100.0, 5.0) == 995.0


def test_the_document_stores_the_same_total():
    doc = trade_document(
        trade_date=date(2026, 10, 30), category="Stock", action="Sell", currency="USD", quantity=10,
        price_nominal=100.0, fees=5.0, fx_rate=1.25, fx_source="manual", ticker="ABC",
    )
    assert doc["cost_eur"] == trade_total("Sell", 10, 100.0, 5.0) / 1.25


def test_a_buy_takes_from_the_balance_and_a_sell_adds_to_it():
    assert balance_after_trade(1000.0, "Buy", 250.5) == 749.5
    assert balance_after_trade(1000.0, "Sell", 250.5) == 1250.5


def test_a_balance_stays_in_whole_cents():
    assert balance_after_trade(2500.0, "Buy", 854.7008547) == 1645.3
    assert balance_after_trade(0.3, "Buy", 0.1) == 0.2  # not 0.19999999999999998


def test_a_buy_can_spend_the_whole_balance():
    assert str(balance_after_trade(100.0, "Buy", 100.004)) == "0.0"  # a rounding away, and not -0.0


def test_a_buy_the_account_cannot_cover_is_refused():
    with pytest.raises(ValueError, match="€100.01, more than the €100.00"):
        balance_after_trade(100.0, "Buy", 100.01)


# --- the write ---------------------------------------------------------------


class _Doc:
    def __init__(self, collection, doc_id, data=None):
        self.collection, self.id, self.data = collection, doc_id, data
        self.reference = self

    def to_dict(self):
        return dict(self.data)


class _Collection:
    def __init__(self, name, docs=()):
        self.name, self.docs = name, list(docs)

    def stream(self):
        return iter(self.docs)

    def document(self):
        return _Doc(self.name, "new")


class _Batch:
    def __init__(self, client):
        self.client, self.ops = client, []

    def set(self, ref, data):
        self.ops.append(("set", ref.collection, data))

    def update(self, ref, data):
        self.ops.append(("update", ref.id, data))

    def commit(self):
        if self.client.down:
            raise ConnectionError("Firestore is unavailable")
        self.client.committed.append(self.ops)


class _Client:
    def __init__(self, down=False):
        self.down, self.committed = down, []
        self.accounts = _Collection(database.CASH_COLLECTION, [
            _Doc(database.CASH_COLLECTION, "a", {"name": "Main account", "category": "Cash", "balance_eur": 5000.0}),
            _Doc(database.CASH_COLLECTION, "b", {"name": "Broker cash", "category": "Dry Powder", "balance_eur": 2500.0}),
        ])

    def collection(self, name):
        return self.accounts if name == database.CASH_COLLECTION else _Collection(name)

    def batch(self):
        return _Batch(self)


def test_the_transaction_and_the_balance_are_one_commit(monkeypatch):
    client = _Client()
    monkeypatch.setattr(database, "init_db", lambda: client)
    database.record_transaction_with_balance({"action": "Buy"}, "Broker cash", 1490.05, "2026-10-31")
    assert client.committed == [[
        ("set", "transactions", {"action": "Buy"}),
        ("update", "b", {"balance_eur": 1490.05, "updated": "2026-10-31"}),
    ]]


def test_an_account_gone_since_the_page_was_drawn_writes_nothing(monkeypatch):
    client = _Client()
    monkeypatch.setattr(database, "init_db", lambda: client)
    with pytest.raises(LookupError, match="Old broker"):
        database.record_transaction_with_balance({"action": "Buy"}, "Old broker", 1.0, "2026-10-31")
    assert client.committed == []


def test_a_failed_commit_raises_for_the_page_to_report(monkeypatch):
    client = _Client(down=True)
    monkeypatch.setattr(database, "init_db", lambda: client)
    with pytest.raises(ConnectionError):
        database.record_transaction_with_balance({"action": "Buy"}, "Broker cash", 1.0, "2026-10-31")


# --- the close ---------------------------------------------------------------


def _bars(*days_and_closes):
    days, closes = zip(*days_and_closes)
    return pd.DataFrame({("Close", "P911.DE"): closes}, index=pd.DatetimeIndex(days))


def test_a_weekend_takes_the_close_before_it(monkeypatch):
    asked = {}

    def download(symbol, **kwargs):
        asked.update(kwargs)
        return _bars(("2026-10-29", 46.0), ("2026-10-30", 46.72))

    monkeypatch.setattr(md.yf, "download", download)
    assert md.fetch_close_on("P911.DE", date(2026, 10, 31)) == (46.72, date(2026, 10, 30))
    assert asked["auto_adjust"] is False  # the price as quoted, no later dividends taken off it


def test_a_bar_after_the_day_is_not_taken(monkeypatch):
    monkeypatch.setattr(md.yf, "download", lambda symbol, **kw: _bars(("2026-10-29", 46.0), ("2026-10-30", 46.72)))
    assert md.fetch_close_on("P911.DE", date(2026, 10, 29)) == (46.0, date(2026, 10, 29))


def test_no_bars_raise_rather_than_return_nothing(monkeypatch):
    monkeypatch.setattr(md.yf, "download", lambda symbol, **kw: pd.DataFrame())
    with pytest.raises(LookupError, match="P911"):
        md.fetch_close_on("P911", date(2026, 10, 31))
