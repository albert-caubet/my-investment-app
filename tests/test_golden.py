"""End-to-end check over a fixture that mimics the real Firestore schema mix."""

import json
from pathlib import Path

import pytest

from portfolio_math import (
    Quote,
    build_positions,
    load_transactions,
    portfolio_totals,
    value_position,
)

RATES = {"USD": 1.17}
PRICES = {
    "ES0000000001": Quote("ES0000000001", 12.0, "EUR"),
    "ES0000000002": Quote("ES0000000002", 9.0, "EUR"),
    "USST": Quote("USST", 14.04, "USD"),
    "IE0000000003": Quote("IE0000000003", 30.0, "EUR"),
    "EXIT": Quote("EXIT", 31.0, "EUR"),
    "LU0000000004": Quote("LU0000000004", 55.0, "EUR"),
    "FEES": Quote("FEES", 110.0, "EUR"),
}


@pytest.fixture(scope="module")
def positions():
    raw = json.loads(
        (Path(__file__).parent / "fixtures" / "transactions_sample.json").read_text("utf-8")
    )
    txs, problems = load_transactions(raw)
    return build_positions(txs), problems


def test_unusable_document_is_dropped_with_a_reason(positions):
    _, problems = positions
    assert any("unusable quantity" in p for p in problems)
    assert any("legacy `price` field" in p for p in problems)


def test_eur_doc_ignores_its_stored_eurusd_rate(positions):
    pos, _ = positions
    assert pos["ES0000000001"].cost_basis_eur == pytest.approx(1000.0)


def test_drifted_cost_eur_is_recomputed(positions):
    """Stored 2830.84; quantity x price is 2830.9765. The latter wins."""
    pos, _ = positions
    assert pos["ES0000000002"].cost_basis_eur == pytest.approx(2830.9765, abs=1e-3)


def test_usd_position_basis_at_historical_rate(positions):
    pos, _ = positions
    assert pos["USST"].cost_basis_eur == pytest.approx(1000.0)


def test_partial_sell_position(positions):
    pos, _ = positions
    p = pos["IE0000000003"]
    assert p.quantity == pytest.approx(150.0)
    assert p.cost_basis_eur == pytest.approx(2250.0)  # not the old 3000.0
    assert p.realised_pnl_eur == pytest.approx(500.0)


def test_fully_exited_position_is_closed_but_keeps_realised(positions):
    pos, _ = positions
    p = pos["EXIT"]
    assert not p.is_open
    assert p.quantity == 0.0
    assert p.cost_basis_eur == 0.0
    assert p.realised_pnl_eur == pytest.approx(200.0)


def test_fees_are_capitalised(positions):
    pos, _ = positions
    assert pos["FEES"].cost_basis_eur == pytest.approx(1009.95)


def test_legacy_price_document_still_values(positions):
    pos, _ = positions
    assert pos["LU0000000004"].cost_basis_eur == pytest.approx(500.0)


def test_portfolio_totals_are_stable(positions):
    """The number that would regress silently if any of the above broke."""
    pos, _ = positions
    vals = [value_position(p, PRICES.get(aid), RATES) for aid, p in pos.items()]
    tot = portfolio_totals(vals)

    # Open basis: 1000 + 2830.9765 + 1000 + 2250 + 500 + 1009.95
    assert tot.cost_basis_eur == pytest.approx(8590.9265, abs=1e-3)
    # Market value: 1200 + 3174.5313 + 1200 + 4500 + 550 + 1100 (EXIT holds nothing)
    assert tot.market_value_eur == pytest.approx(11724.5313, abs=1e-3)
    assert tot.realised_pnl_eur == pytest.approx(700.0)  # 500 partial + 200 exit
    assert tot.total_pnl_eur == pytest.approx(
        tot.market_value_eur - tot.cost_basis_eur + 700.0, abs=1e-6
    )
    assert tot.n_unvalued == 0

    # Released basis: 750 from the partial sell (25% of 3000) + 1000 from the exit.
    assert tot.cost_released_eur == pytest.approx(1750.0)
    assert tot.invested_eur == pytest.approx(8590.9265 + 1750.0, abs=1e-3)
    assert tot.realised_pnl_pct == pytest.approx(700.0 / 1750.0 * 100.0)
    assert tot.unrealised_pnl_pct == pytest.approx(
        tot.unrealised_pnl_eur / tot.cost_basis_eur * 100.0
    )
    # On every euro deployed, not on the open basis alone (which would be ~44.6%).
    assert tot.pnl_pct == pytest.approx(tot.total_pnl_eur / tot.invested_eur * 100.0)
    assert tot.pnl_pct < tot.total_pnl_eur / tot.cost_basis_eur * 100.0


def test_closed_position_contributes_no_market_value(positions):
    pos, _ = positions
    val = value_position(pos["EXIT"], PRICES["EXIT"], RATES)
    assert val.market_value_eur == 0.0
    assert val.total_pnl_eur == pytest.approx(200.0)
