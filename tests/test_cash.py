"""Cash accounts and allocation bands: validation, band mapping, and the Firestore replace."""

import math

import pytest

import database
from portfolio_math import (
    ALLOCATION_BANDS,
    CASH_CATEGORIES,
    RESERVE_CATEGORY,
    CashAccount,
    allocation_band,
    capital_by_band,
    clean_cash_accounts,
)

TRANSACTION_CATEGORIES = [
    "Fund", "Stock", "ETF", "Cash/Money Market", "Fixed Income", "Crypto", "Bonds", "Commodity", "Other",
]


def row(name="Main", category="Cash", balance=100.0, **extra):
    return {"name": name, "category": category, "balance_eur": balance, **extra}


# --- validation ------------------------------------------------------------------------


def test_valid_rows_become_accounts():
    accounts, errors = clean_cash_accounts([row(" Main ", "Cash", 5000), row("Broker", "Dry Powder", "2500.5")])
    assert errors == []
    assert accounts == [CashAccount("Main", "Cash", 5000.0), CashAccount("Broker", "Dry Powder", 2500.5)]


def test_the_empty_row_the_editor_adds_is_dropped():
    accounts, errors = clean_cash_accounts([row(), {"name": None, "category": None, "balance_eur": math.nan}])
    assert errors == [] and len(accounts) == 1


def test_a_balance_without_a_name_is_an_error_not_dropped():
    _, errors = clean_cash_accounts([row(name="  ", balance=300)])
    assert errors and "no account name" in errors[0]


@pytest.mark.parametrize("category", ["cash", "Savings", None])
def test_unknown_category_is_an_error(category):
    _, errors = clean_cash_accounts([row(category=category)])
    assert errors and "category" in errors[0]


@pytest.mark.parametrize("balance", [-1.0, None, math.nan, "lots"])
def test_bad_balance_is_an_error(balance):
    _, errors = clean_cash_accounts([row(balance=balance)])
    assert errors and "balance" in errors[0]


def test_zero_balance_is_allowed():
    accounts, errors = clean_cash_accounts([row(balance=0)])
    assert errors == [] and accounts[0].balance_eur == 0.0


def test_one_row_per_account():
    _, errors = clean_cash_accounts([row("Main"), row("main ", "Dry Powder")])
    assert errors and "listed twice" in errors[0]


def test_the_saved_date_is_carried():
    accounts, _ = clean_cash_accounts([row(updated="2026-09-27")])
    assert accounts[0].updated == "2026-09-27"


# --- bands ------------------------------------------------------------------------------


def test_every_category_in_the_portfolio_has_a_band():
    for category in TRANSACTION_CATEGORIES + ["Dry Powder"]:
        assert allocation_band(category) in ALLOCATION_BANDS


def test_the_cash_reserve_has_no_band():
    """Current accounts are outside the portfolio, so they are in no band at all."""
    assert RESERVE_CATEGORY in CASH_CATEGORIES
    assert allocation_band(RESERVE_CATEGORY) is None


@pytest.mark.parametrize(
    "category, band",
    [
        ("Dry Powder", "Dry Powder"),
        ("Cash/Money Market", "Dry Powder"),  # money parked in the portfolio to be invested
        ("Bonds", "Bonds"),
        ("Fixed Income", "Bonds"),
        ("Fund", "Equity funds"),
        ("ETF", "Equity funds"),
        ("Stock", "Stocks"),
        ("Crypto", "Other"),
        ("Commodity", "Other"),
        (None, "Other"),
    ],
)
def test_band_mapping(category, band):
    assert allocation_band(category) == band


def test_capital_by_band_sums_in_drawing_order_and_drops_empty_bands():
    bands = capital_by_band(
        [("Stock", 300.0), ("Dry Powder", 100.0), ("Fund", 200.0), ("ETF", 50.0), ("Stock", None), ("Bonds", 0.0)]
    )
    assert bands == {"Dry Powder": 100.0, "Equity funds": 250.0, "Stocks": 300.0}
    assert list(bands) == ["Dry Powder", "Equity funds", "Stocks"]


def test_capital_by_band_leaves_the_cash_reserve_out():
    """Every account can be passed in unfiltered; the reserve simply does not count."""
    bands = capital_by_band([("Cash", 5_000.0), ("Dry Powder", 2_500.0), ("Fund", 10_000.0)])
    assert bands == {"Dry Powder": 2_500.0, "Equity funds": 10_000.0}


def test_nothing_is_left_out_of_the_total():
    """A category without a band of its own still counts, under Other."""
    bands = capital_by_band([("Crypto", 40.0), ("Stock", 60.0)])
    assert sum(bands.values()) == 100.0 and bands["Other"] == 40.0


# --- Firestore: save replaces the stored list ----------------------------------------------


class _Ref:
    def __init__(self, store, doc_id):
        self.store, self.id = store, doc_id


class _Doc:
    def __init__(self, ref, data):
        self.reference, self._data = ref, data

    def to_dict(self):
        return dict(self._data)


class _Collection:
    def __init__(self, store):
        self.store, self.n = store, 0

    def stream(self):
        return [_Doc(_Ref(self.store, k), v) for k, v in list(self.store.items())]

    def document(self):
        self.n += 1
        return _Ref(self.store, f"new{self.n}")


class _Batch:
    def __init__(self):
        self.ops = []

    def delete(self, ref):
        self.ops.append((ref, None))

    def set(self, ref, data):
        self.ops.append((ref, data))

    def commit(self):  # applied only here, as a real batch is
        for ref, data in self.ops:
            if data is None:
                ref.store.pop(ref.id, None)
            else:
                ref.store[ref.id] = dict(data)


class _FakeFirestore:
    def __init__(self, store):
        self.cash = _Collection(store)

    def collection(self, name):
        assert name == database.CASH_COLLECTION
        return self.cash

    def batch(self):
        return _Batch()


def test_save_replaces_every_stored_account(monkeypatch):
    store = {"a": row("Old bank", balance=10.0), "b": row("Closed account", balance=20.0)}
    monkeypatch.setattr(database, "init_db", lambda: _FakeFirestore(store))
    database.get_cash_accounts.clear()

    assert len(database.get_cash_accounts()) == 2
    database.save_cash_accounts([row("Main", balance=5000.0, updated="2026-09-27")])

    # Deleted rows are gone, the new list is all there is, and the cache saw the save.
    assert list(store.values()) == [row("Main", balance=5000.0, updated="2026-09-27")]
    assert database.get_cash_accounts() == [row("Main", balance=5000.0, updated="2026-09-27")]
    database.get_cash_accounts.clear()


def test_saving_an_empty_list_clears_the_accounts(monkeypatch):
    store = {"a": row()}
    monkeypatch.setattr(database, "init_db", lambda: _FakeFirestore(store))
    database.save_cash_accounts([])
    assert store == {}
    database.get_cash_accounts.clear()


# --- dating saved accounts ------------------------------------------------------------------

from portfolio_math import account_documents  # noqa: E402

PREVIOUS = [
    CashAccount("Main", "Cash", 5000.0, "2026-09-01"),
    CashAccount("Broker", "Dry Powder", 2500.0, "2026-09-01"),
]
TODAY = "2026-09-27"


def _dates(documents):
    return {d["name"]: d["updated"] for d in documents}


def test_an_unchanged_account_keeps_its_date():
    """Deleting one row and saving must not claim the others were checked today."""
    documents = account_documents([PREVIOUS[0]], PREVIOUS, TODAY)
    assert documents == [{"name": "Main", "category": "Cash", "balance_eur": 5000.0, "updated": "2026-09-01"}]


def test_a_changed_balance_or_category_is_dated_today():
    changed = [
        CashAccount("Main", "Cash", 5100.0),  # new balance
        CashAccount("Broker", "Cash", 2500.0),  # new category
    ]
    assert _dates(account_documents(changed, PREVIOUS, TODAY)) == {"Main": TODAY, "Broker": TODAY}


def test_a_new_account_is_dated_today_and_matching_ignores_case():
    documents = account_documents(
        [CashAccount("main", "Cash", 5000.0), CashAccount("Savings", "Dry Powder", 100.0)], PREVIOUS, TODAY
    )
    assert _dates(documents) == {"main": "2026-09-01", "Savings": TODAY}


def test_an_undated_account_gets_a_date_when_saved():
    undated = [CashAccount("Main", "Cash", 5000.0, None)]
    assert _dates(account_documents(undated, undated, TODAY)) == {"Main": TODAY}
