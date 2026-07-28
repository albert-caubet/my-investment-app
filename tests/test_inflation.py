"""Inflation adjustment: the Fisher relation and index-based restatement."""

from datetime import date

import pytest

from portfolio_math import (
    build_position,
    inflation_between,
    month_of,
    real_rate,
    to_real_terms,
)

# Real Spanish HICP levels, so the arithmetic is checkable against published data.
INDEX = {
    "2025-07": 126.84,
    "2025-08": 126.88,
    "2025-09": 127.17,
    "2025-10": 127.85,
    "2025-11": 127.87,
    "2025-12": 128.28,
}


def test_real_rate_uses_fisher_not_subtraction():
    """6.73% nominal at 3.0% inflation is 3.62% real, not 3.73%."""
    assert real_rate(0.0673, 0.030) == pytest.approx(0.036214, abs=1e-6)
    assert real_rate(0.0673, 0.030) != pytest.approx(0.0673 - 0.030, abs=1e-5)


def test_real_rate_zero_inflation_is_identity():
    assert real_rate(0.05, 0.0) == pytest.approx(0.05)


def test_real_rate_can_go_negative():
    """Beating nothing: 2% nominal against 3% inflation loses purchasing power."""
    assert real_rate(0.02, 0.03) < 0


def test_real_rate_with_deflation_raises_real_return():
    assert real_rate(0.02, -0.01) > 0.02


def test_month_of():
    assert month_of(date(2025, 9, 22)) == "2025-09"
    assert month_of(date(2026, 3, 2)) == "2026-03"


def test_inflation_between():
    assert inflation_between("2025-09", "2025-12", INDEX) == pytest.approx(
        128.28 / 127.17 - 1, abs=1e-9
    )


def test_inflation_between_uncovered_returns_none():
    assert inflation_between("2025-09", "2026-07", INDEX) is None


def test_to_real_terms_scales_price_and_fees(tx):
    """A Sept 2025 purchase restated into Dec 2025 euros."""
    original = tx(qty=100, price=10.0, fees=5.0, day=265)  # 2025-09-22
    assert month_of(original.trade_date) == "2025-09"

    adjusted, uncovered = to_real_terms([original], "2025-12", INDEX)
    factor = 128.28 / 127.17
    assert uncovered == []
    assert adjusted[0].price_nominal == pytest.approx(10.0 * factor)
    assert adjusted[0].fees == pytest.approx(5.0 * factor)
    assert adjusted[0].quantity == original.quantity  # shares are not money


def test_to_real_terms_reports_uncovered_months(tx):
    """A March 2026 purchase cannot be adjusted with an index ending Dec 2025."""
    outside = tx(qty=10, price=100.0, day=426)  # 2026-03-02
    assert month_of(outside.trade_date) == "2026-03"

    adjusted, uncovered = to_real_terms([outside], "2025-12", INDEX)
    assert uncovered == ["2026-03"]
    assert adjusted[0].price_nominal == 100.0  # passed through, not silently scaled


def test_to_real_terms_partial_coverage_names_only_the_gap(tx):
    covered = tx(qty=100, price=10.0, day=265)  # 2025-09
    outside = tx(qty=10, price=100.0, day=426)  # 2026-03
    _, uncovered = to_real_terms([covered, outside], "2025-12", INDEX)
    assert uncovered == ["2026-03"]


def test_missing_target_month_adjusts_nothing(tx):
    adjusted, uncovered = to_real_terms([tx(qty=100, price=10.0)], "2026-07", INDEX)
    assert adjusted[0].price_nominal == 10.0
    assert "no index value" in uncovered[0]


def test_real_cost_basis_via_build_position(tx):
    """Replaying adjusted transactions gives a constant-euro cost basis."""
    txs = [tx(qty=100, price=10.0, day=265)]  # 2025-09, EUR 1000
    real_txs, _ = to_real_terms(txs, "2025-12", INDEX)

    nominal = build_position(txs)
    real = build_position(real_txs)

    assert nominal.cost_basis_eur == pytest.approx(1000.0)
    assert real.cost_basis_eur == pytest.approx(1000.0 * 128.28 / 127.17)
    assert real.cost_basis_eur > nominal.cost_basis_eur
    assert real.quantity == nominal.quantity  # only the money moved


def test_real_adjustment_survives_a_partial_sell(tx):
    """Sells must still relieve basis proportionally after adjustment."""
    txs = [
        tx(qty=100, price=10.0, day=265),
        tx(action="Sell", qty=50, price=12.0, day=340),
    ]
    real_txs, uncovered = to_real_terms(txs, "2025-12", INDEX)
    real = build_position(real_txs)
    assert uncovered == []
    assert real.quantity == pytest.approx(50.0)
    # Half the (inflated) basis remains.
    assert real.cost_basis_eur == pytest.approx(1000.0 * 128.28 / 127.17 / 2)


def test_adjusting_to_its_own_month_is_a_no_op(tx):
    txs = [tx(qty=100, price=10.0, day=356)]  # 2025-12
    real_txs, _ = to_real_terms(txs, "2025-12", INDEX)
    assert real_txs[0].price_nominal == pytest.approx(10.0)


def test_empty_index_leaves_everything_untouched(tx):
    adjusted, uncovered = to_real_terms([tx(qty=1, price=1.0)], "2025-12", {})
    assert adjusted[0].price_nominal == 1.0
    assert uncovered
