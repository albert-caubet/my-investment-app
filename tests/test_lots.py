"""FIFO lots: same total profit as average cost, different realised split, taxable gain of a sale."""

import pytest

from invest.portfolio import lots
from portfolio_math import build_position


def test_fifo_consumes_the_oldest_lot_first(tx):
    state = lots.fifo_lots([tx(qty=10, price=10, day=1), tx(qty=10, price=20, day=2),
                            tx(action="Sell", qty=10, price=25, day=3)])
    assert state.realised_eur == pytest.approx(10 * (25 - 10))  # the 10-euro lot went first
    assert state.quantity == pytest.approx(10)
    assert state.cost_basis_eur == pytest.approx(200)
    assert [lot.trade_date.day for lot in state.open_lots] == [2]


def test_total_profit_matches_average_cost_but_the_split_differs(tx):
    txs = [tx(qty=10, price=10, day=1), tx(qty=10, price=20, day=2), tx(action="Sell", qty=10, price=25, day=3)]
    fifo = lots.fifo_lots(txs)
    avg = build_position(txs)
    mark = 30.0
    fifo_total = fifo.realised_eur + (fifo.quantity * mark - fifo.cost_basis_eur)
    avg_total = avg.realised_pnl_eur + (avg.quantity * mark - avg.cost_basis_eur)
    assert fifo_total == pytest.approx(avg_total)
    assert fifo.realised_eur == pytest.approx(150) and avg.realised_pnl_eur == pytest.approx(100)


def test_fees_are_in_the_lot_cost_and_sale_proceeds(tx):
    state = lots.fifo_lots([tx(qty=10, price=10, fees=5, day=1), tx(action="Sell", qty=10, price=12, fees=3, day=2)])
    assert state.realised_eur == pytest.approx((120 - 3) - (100 + 5))


def test_partial_lot_consumption_across_several_lots(tx):
    state = lots.fifo_lots([tx(qty=4, price=10, day=1), tx(qty=4, price=12, day=2), tx(qty=4, price=14, day=3),
                            tx(action="Sell", qty=6, price=20, day=4)])
    assert state.realised_eur == pytest.approx(4 * (20 - 10) + 2 * (20 - 12))
    assert [(round(l.quantity, 6), l.cost_eur_per_unit) for l in state.open_lots] == [(2.0, 12.0), (4.0, 14.0)]


def test_oversell_is_warned_and_booked_as_gain(tx):
    state = lots.fifo_lots([tx(qty=5, price=10, day=1), tx(action="Sell", qty=8, price=10, day=2)])
    assert state.warnings and "exceeds" in state.warnings[0]
    assert state.realised_eur == pytest.approx(3 * 10)


def test_usd_lots_use_the_trade_date_fx(tx):
    state = lots.fifo_lots([tx(qty=100, price=11.70, ccy="USD", fx=1.17, day=1)])
    assert state.open_lots[0].cost_eur_per_unit == pytest.approx(10.0)


def test_taxable_gain_of_a_proposed_sale(tx):
    state = lots.fifo_lots([tx(qty=10, price=10, day=1), tx(qty=10, price=20, day=2)])
    estimate = lots.taxable_gain(state, 15, 25.0)
    assert estimate.quantity == 15 and estimate.proceeds_eur == pytest.approx(375)
    assert estimate.cost_eur == pytest.approx(10 * 10 + 5 * 20)
    assert estimate.gain_eur == pytest.approx(375 - 200)
    assert [q for _, q, _ in estimate.lots_used] == [10, 5]
    assert estimate.short_by == 0
    too_many = lots.taxable_gain(state, 30, 25.0)
    assert too_many.quantity == 20 and too_many.short_by == 10
    assert lots.units_for_amount(state, 1000.0, 25.0) == pytest.approx(20)  # capped at what is held
    assert lots.units_for_amount(state, 100.0, 25.0) == pytest.approx(4)
    assert estimate.as_dict()["lots_used"][0]["quantity"] == 10


def test_fifo_requires_transactions():
    with pytest.raises(ValueError):
        lots.fifo_lots([])
