"""Portfolio design: rounding, rebalancing with locks, validating a design, the gap to it, storage."""

import random

import pytest

import database
from portfolio_math import (
    DESIGN_BANDS,
    allocation_drift,
    capital_by_band,
    clean_design,
    rebalance,
    round_to_100,
    round_to_total,
)

DESIGN = {"Dry Powder": 10, "Bonds": 25, "Equity funds": 45, "Stocks": 20}


def test_the_design_has_no_cash_reserve_category():
    """Current accounts are outside the portfolio, so a design never targets them."""
    assert DESIGN_BANDS == ("Dry Powder", "Bonds", "Equity funds", "Stocks")


# --- rounding -----------------------------------------------------------------------------


def test_round_to_100_always_adds_up():
    # Plain rounding gives 33 + 33 + 33 = 99; largest remainder fixes it.
    assert sum(round_to_100({"a": 1, "b": 1, "c": 1}).values()) == 100
    shares = round_to_100({"Dry Powder": 10_000, "Bonds": 5_000, "Equity funds": 192_708})
    assert shares == {"Dry Powder": 5, "Bonds": 2, "Equity funds": 93}


def test_round_to_100_gives_the_extra_point_to_the_largest_remainder():
    assert round_to_100({"a": 16.6, "b": 16.6, "c": 66.8}) == {"a": 17, "b": 16, "c": 67}


def test_round_to_total_any_total():
    assert round_to_total({"a": 1, "b": 2}, 10) == {"a": 3, "b": 7}
    assert round_to_total({"a": 1, "b": 1}, 0) == {"a": 0, "b": 0}


def test_round_to_100_with_nothing_is_all_zeros():
    assert round_to_100({"a": 0, "b": 0}) == {"a": 0, "b": 0}


def test_round_to_100_keeps_the_order_and_every_key():
    shares = round_to_100({band: 0 for band in DESIGN_BANDS} | {"Bonds": 1})
    assert list(shares) == list(DESIGN_BANDS)
    assert shares["Bonds"] == 100


# --- rebalance: the sliders -----------------------------------------------------------------
# `values` holds the moved slider's new value already, as the widget callback sees it.

START = {"Dry Powder": 10, "Bonds": 20, "Equity funds": 50, "Stocks": 20}


def test_the_others_absorb_a_move_in_proportion():
    out = rebalance(START | {"Stocks": 40}, "Stocks")
    assert sum(out.values()) == 100 and out["Stocks"] == 40
    # The other three were 10 : 20 : 50 and share the remaining 60 the same way.
    assert out == {"Dry Powder": 8, "Bonds": 15, "Equity funds": 37, "Stocks": 40}


def test_moving_down_gives_the_points_back_in_proportion():
    out = rebalance(START | {"Equity funds": 30}, "Equity funds")
    assert out == {"Dry Powder": 14, "Bonds": 28, "Equity funds": 30, "Stocks": 28}


def test_a_locked_category_stays_put():
    out = rebalance(START | {"Stocks": 40}, "Stocks", locked={"Equity funds"})
    assert out["Equity funds"] == 50
    assert out["Stocks"] == 40
    assert out["Dry Powder"] + out["Bonds"] == 10 and sum(out.values()) == 100


def test_a_move_beyond_what_the_unlocked_can_give_is_clamped():
    out = rebalance(START | {"Stocks": 90}, "Stocks", locked={"Equity funds"})
    assert out == {"Dry Powder": 0, "Bonds": 0, "Equity funds": 50, "Stocks": 50}


def test_with_every_other_category_locked_the_slider_cannot_move():
    out = rebalance(START | {"Stocks": 35}, "Stocks", locked={"Dry Powder", "Bonds", "Equity funds"})
    assert out == START


def test_when_the_others_are_all_zero_the_points_are_shared_equally():
    """No proportions left to keep: the mix that 100% erased cannot come back."""
    everything = {"Dry Powder": 0, "Bonds": 0, "Equity funds": 100, "Stocks": 0}
    out = rebalance(everything | {"Equity funds": 70}, "Equity funds")
    assert out == {"Dry Powder": 10, "Bonds": 10, "Equity funds": 70, "Stocks": 10}


def test_the_total_holds_through_any_sequence_of_moves():
    rng = random.Random(7)
    values = dict(START)
    for _ in range(300):
        changed = rng.choice(DESIGN_BANDS)
        locked = {band for band in DESIGN_BANDS if band != changed and rng.random() < 0.3}
        values = rebalance(values | {changed: rng.randint(0, 100)}, changed, locked)
        assert sum(values.values()) == 100
        assert all(0 <= v <= 100 and isinstance(v, int) for v in values.values())


# --- clean_design -------------------------------------------------------------------------


def test_a_valid_design():
    design, problems = clean_design(DESIGN)
    assert problems == []
    assert design == {band: float(DESIGN[band]) for band in DESIGN_BANDS}


def test_no_design_saved():
    assert clean_design(None) == (None, [])
    assert clean_design({}) == (None, [])


def test_a_missing_category_counts_as_zero():
    design, problems = clean_design({"Dry Powder": 40, "Equity funds": 60})
    assert problems == [] and design["Bonds"] == 0.0


def test_a_design_that_does_not_add_up_is_refused():
    design, problems = clean_design(DESIGN | {"Stocks": 25})
    assert design is None and "105" in problems[0]


def test_a_design_saved_with_a_cash_target_is_refused():
    """From before the cash reserve left the portfolio: its percentages no longer mean the same."""
    design, problems = clean_design({"Cash": 10, "Dry Powder": 5, "Bonds": 20, "Equity funds": 45, "Stocks": 20})
    assert design is None and "'Cash'" in problems[0]


@pytest.mark.parametrize("bad", [{"Crypto": 5}, {"Stocks": -5}, {"Stocks": 120}, {"Stocks": "lots"}])
def test_unusable_values_are_refused(bad):
    design, problems = clean_design(DESIGN | bad)
    assert design is None and problems


# --- allocation_drift ---------------------------------------------------------------------


def test_drift_against_the_design():
    current = {"Dry Powder": 15_000.0, "Equity funds": 185_000.0}  # portfolio 200,000
    rows = {d.band: d for d in allocation_drift(current, DESIGN)}

    assert list(rows) == list(DESIGN_BANDS)  # every design category, even an empty one
    equity = rows["Equity funds"]
    assert equity.current_pct == pytest.approx(92.5)
    assert equity.off_by_pp == pytest.approx(47.5)  # over the design
    assert equity.to_design_eur == pytest.approx(90_000 - 185_000)
    assert rows["Bonds"].off_by_pp == pytest.approx(-25.0)  # under: none held
    assert rows["Bonds"].to_design_eur == pytest.approx(50_000)


def test_a_stray_key_does_not_change_the_base():
    rows = allocation_drift({"Cash": 1_000_000.0, "Dry Powder": 100.0}, DESIGN)
    assert next(d for d in rows if d.band == "Dry Powder").current_pct == pytest.approx(100.0)


def test_rebalancing_moves_money_without_adding_any():
    current = capital_by_band([("Fund", 150_000), ("Stock", 20_000), ("Cash", 10_000), ("Crypto", 5_000)])
    assert sum(d.to_design_eur for d in allocation_drift(current, DESIGN)) == pytest.approx(0.0, abs=1e-6)


def test_other_appears_only_when_held_and_is_designed_at_zero():
    assert "Other" not in {d.band for d in allocation_drift({"Dry Powder": 100.0}, DESIGN)}
    other = next(d for d in allocation_drift({"Dry Powder": 90.0, "Other": 10.0}, DESIGN) if d.band == "Other")
    assert other.design_pct == 0.0 and other.off_by_pp == pytest.approx(10.0)


def test_drift_with_no_capital():
    rows = allocation_drift({}, DESIGN)
    assert all(d.current_pct == 0.0 and d.design_eur == 0.0 for d in rows)


# --- storage -------------------------------------------------------------------------------


class _Snapshot:
    def __init__(self, data):
        self.exists, self._data = data is not None, data

    def to_dict(self):
        return dict(self._data)


class _Document:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def get(self):
        return _Snapshot(self.store.get(self.key))

    def set(self, data):
        self.store[self.key] = dict(data)


class _FakeFirestore:
    def __init__(self, store):
        self.store = store

    def collection(self, name):
        assert name == database.DESIGN_COLLECTION
        return self

    def document(self, key):
        assert key == database.DESIGN_DOCUMENT
        return _Document(self.store, key)


def test_save_overwrites_the_single_design_document(monkeypatch):
    store = {}
    monkeypatch.setattr(database, "init_db", lambda: _FakeFirestore(store))
    database.get_portfolio_design.clear()

    assert database.get_portfolio_design() is None  # before the first save
    database.save_portfolio_design({"targets": DESIGN, "saved": "2026-09-27"})
    changed = DESIGN | {"Dry Powder": 5, "Stocks": 25}
    database.save_portfolio_design({"targets": changed, "saved": "2026-09-28"})

    # One document, the latest save, and the cache saw it.
    assert list(store) == [database.DESIGN_DOCUMENT]
    assert database.get_portfolio_design() == {"targets": changed, "saved": "2026-09-28"}
    database.get_portfolio_design.clear()
