"""The money-critical tests: cost basis, sells, realised P&L, float residue."""

import random

import pytest

from portfolio_math import build_position, build_positions


def test_buy_only(tx):
    pos = build_position([tx(qty=100, price=10)])
    assert pos.quantity == 100
    assert pos.cost_basis_eur == pytest.approx(1000.0)
    assert pos.realised_pnl_eur == 0.0
    assert pos.is_open


def test_buy_capitalises_fees(tx):
    pos = build_position([tx(qty=100, price=10, fees=7.5)])
    assert pos.cost_basis_eur == pytest.approx(1007.5)
    assert pos.avg_cost_eur == pytest.approx(10.075)
    assert pos.fees_eur_total == pytest.approx(7.5)


def test_weighted_average_across_buys(tx):
    pos = build_position([tx(qty=100, price=10, day=1), tx(qty=100, price=20, day=2)])
    assert pos.quantity == 200
    assert pos.avg_cost_eur == pytest.approx(15.0)


def test_partial_sell_releases_basis_proportionally(tx):
    """The regression that matters: the old groupby kept 100% of buy cost."""
    pos = build_position(
        [
            tx(qty=100, price=10, day=1),
            tx(qty=100, price=20, day=2),
            tx(action="Sell", qty=50, price=25, day=3),
        ]
    )
    assert pos.quantity == 150
    # 50 of 200 units sold -> 25% of the 3000 basis released.
    assert pos.cost_basis_eur == pytest.approx(2250.0)
    assert pos.avg_cost_eur == pytest.approx(15.0)
    assert pos.realised_pnl_eur == pytest.approx(50 * (25 - 15))


def test_sell_fees_reduce_proceeds(tx):
    pos = build_position(
        [tx(qty=100, price=10, day=1), tx(action="Sell", qty=50, price=20, day=2, fees=5)]
    )
    assert pos.realised_pnl_eur == pytest.approx(50 * (20 - 10) - 5)


def test_full_exit_zeroes_basis(tx):
    pos = build_position(
        [tx(qty=100, price=10, day=1), tx(action="Sell", qty=100, price=12, day=2)]
    )
    assert pos.quantity == 0.0
    assert pos.cost_basis_eur == 0.0
    assert not pos.is_open
    assert pos.realised_pnl_eur == pytest.approx(200.0)


def test_exit_then_reentry_starts_fresh_average(tx):
    pos = build_position(
        [
            tx(qty=100, price=10, day=1),
            tx(action="Sell", qty=100, price=12, day=2),
            tx(qty=50, price=30, day=3),
        ]
    )
    assert pos.quantity == 50
    assert pos.avg_cost_eur == pytest.approx(30.0)  # not blended with the closed lot
    assert pos.realised_pnl_eur == pytest.approx(200.0)  # persists across the round trip


def test_sell_with_no_position_warns(tx):
    pos = build_position([tx(action="Sell", qty=10, price=5, day=1)])
    assert pos.quantity == 0.0
    assert pos.realised_pnl_eur == pytest.approx(50.0)
    assert any("no open position" in w for w in pos.warnings)


def test_oversell_warns_and_clamps(tx):
    pos = build_position(
        [tx(qty=10, price=10, day=1), tx(action="Sell", qty=25, price=12, day=2)]
    )
    assert pos.quantity == 0.0  # never goes short
    assert pos.cost_basis_eur == 0.0
    assert any("oversell" in w for w in pos.warnings)


def test_float_residue_is_swept(tx):
    """Three thirds in, one whole out -- must close cleanly, not leave 1e-16 shares."""
    third = 1.0 / 3.0
    pos = build_position(
        [
            tx(qty=third, price=30, day=1),
            tx(qty=third, price=30, day=2),
            tx(qty=third, price=30, day=3),
            tx(action="Sell", qty=1.0, price=30, day=4),
        ]
    )
    assert pos.quantity == 0.0
    assert pos.cost_basis_eur == 0.0
    assert not pos.is_open


def test_same_day_buy_then_sell_ordered_by_seq(tx):
    pos = build_position(
        [
            tx(action="Sell", qty=100, price=20, day=5, seq=2),
            tx(qty=100, price=10, day=5, seq=1),
        ]
    )
    assert pos.quantity == 0.0
    assert pos.realised_pnl_eur == pytest.approx(1000.0)
    assert not any("no open position" in w for w in pos.warnings)


def test_usd_cost_basis_uses_historical_rate(tx):
    """1170 USD at 1.17 USD per EUR is a 1000 EUR basis."""
    pos = build_position([tx(qty=100, price=11.70, ccy="USD", fx=1.17)])
    assert pos.cost_basis_eur == pytest.approx(1000.0)
    assert pos.cost_basis_nominal == pytest.approx(1170.0)


def test_mixed_currency_drops_nominal_basis_only(tx):
    pos = build_position(
        [
            tx(qty=100, price=10, ccy="EUR", fx=1.0, day=1),
            tx(qty=100, price=11.70, ccy="USD", fx=1.17, day=2),
        ]
    )
    assert pos.cost_basis_eur == pytest.approx(2000.0)  # still correct
    assert pos.cost_basis_nominal is None  # but a nominal average is meaningless
    assert pos.avg_cost_nominal is None


def test_build_positions_groups_by_asset(tx):
    positions = build_positions(
        [tx(asset="A", qty=10), tx(asset="B", qty=20), tx(asset="A", qty=5, day=2)]
    )
    assert set(positions) == {"A", "B"}
    assert positions["A"].quantity == 15


def test_total_equals_unrealised_plus_realised_invariant(tx):
    """Property test: whatever the trade sequence, the identity must hold."""
    rng = random.Random(42)
    txs, day = [], 1
    for _ in range(50):
        qty = round(rng.uniform(1, 100), 4)
        price = round(rng.uniform(5, 50), 4)
        action = "Buy" if rng.random() < 0.6 else "Sell"
        txs.append(tx(action=action, qty=qty, price=price, day=day, seq=day))
        day += 1

    pos = build_position(txs)
    mark = 30.0
    unrealised = pos.quantity * mark - pos.cost_basis_eur
    total = unrealised + pos.realised_pnl_eur

    cash = sum(
        (-t.gross_eur - t.fees_eur) if t.is_buy else (t.gross_eur - t.fees_eur) for t in txs
    )
    # Only holds when no sell ever exceeded the position; otherwise phantom
    # shares were sold and the cash trail legitimately diverges.
    if not any("oversell" in w or "no open position" in w for w in pos.warnings):
        assert total == pytest.approx(cash + pos.quantity * mark, abs=1e-6)


def test_position_carries_listing_currency_latest_non_empty_wins(tx):
    """The stored currency is the valuation fallback, so an empty later doc must
    not erase it."""
    pos = build_position([tx(day=1, listing_ccy="USD"), tx(day=2)])
    assert pos.listing_ccy == "USD"
    pos = build_position([tx(day=1, listing_ccy="USD"), tx(day=2, listing_ccy="EUR")])
    assert pos.listing_ccy == "EUR"
    assert build_position([tx()]).listing_ccy is None
