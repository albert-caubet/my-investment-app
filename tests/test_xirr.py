"""Money-weighted return: the solver, and the guards around it."""

from datetime import date, timedelta

import pytest

from portfolio_math import MIN_XIRR_DAYS, build_cashflows, xirr

D0 = date(2025, 1, 1)


def d(days):
    return D0 + timedelta(days=days)


def test_simple_one_year_gain():
    res = xirr([(D0, -1000.0), (d(365), 1100.0)])
    assert res.credible
    assert res.rate == pytest.approx(0.10, abs=1e-6)


def test_one_year_loss():
    assert xirr([(D0, -1000.0), (d(365), 900.0)]).rate == pytest.approx(-0.10, abs=1e-6)


def test_doubling_in_one_year():
    assert xirr([(D0, -1000.0), (d(365), 2000.0)]).rate == pytest.approx(1.0, abs=1e-6)


def test_matches_closed_form_for_a_single_round_trip():
    """For one outflow and one inflow, XIRR is exactly (V/C)^(365/n) - 1."""
    cost, value, days = 184_266.79, 191_707.53, 310
    res = xirr([(D0, -cost), (d(days), value)])
    expected = (value / cost) ** (365.0 / days) - 1
    assert res.rate == pytest.approx(expected, rel=1e-9)


def test_half_year_gain_annualises_upward():
    """4% over six months is more than 4% a year -- that is the whole point."""
    res = xirr([(D0, -1000.0), (d(182), 1040.0)])
    assert res.rate > 0.08


def test_later_contributions_are_weighted_less():
    """A big early deposit and a big late one cannot count equally."""
    early = xirr([(D0, -10_000.0), (d(300), -100.0), (d(365), 11_100.0)]).rate
    late = xirr([(D0, -100.0), (d(300), -10_000.0), (d(365), 11_100.0)]).rate
    assert late > early


def test_multiple_contributions():
    res = xirr(
        [(D0, -1000.0), (d(120), -1000.0), (d(240), -1000.0), (d(365), 3200.0)]
    )
    assert res.credible
    assert 0.05 < res.rate < 0.35
    assert res.n_flows == 4
    assert res.days == 365


def test_short_window_is_not_credible():
    res = xirr([(D0, -1000.0), (d(20), 1010.0)])
    assert not res.credible
    assert res.rate is None
    assert "minimum" in res.note


def test_boundary_at_min_days():
    assert xirr([(D0, -1000.0), (d(MIN_XIRR_DAYS), 1050.0)]).credible
    assert not xirr([(D0, -1000.0), (d(MIN_XIRR_DAYS - 1), 1050.0)]).credible


def test_all_outflows_has_no_solution():
    res = xirr([(D0, -1000.0), (d(365), -500.0)])
    assert not res.credible
    assert "outflow and an inflow" in res.note


def test_single_flow_is_rejected():
    assert not xirr([(D0, -1000.0)]).credible


def test_zero_flows_are_ignored():
    res = xirr([(D0, -1000.0), (d(100), 0.0), (d(365), 1100.0)])
    assert res.n_flows == 2
    assert res.rate == pytest.approx(0.10, abs=1e-6)


def test_severe_loss():
    """95% down over a year."""
    res = xirr([(D0, -1000.0), (d(365), 50.0)])
    assert res.credible
    assert res.rate == pytest.approx(-0.95, abs=1e-6)


def test_near_total_loss_still_brackets():
    """A wipeout must not be misreported as having no solution."""
    res = xirr([(D0, -1000.0), (d(365), 0.01)])
    assert res.credible
    assert -1.0 < res.rate < -0.999


def test_long_span_at_the_extreme_does_not_overflow():
    res = xirr([(D0, -1000.0), (d(365 * 20), 0.01)])
    assert res.rate is None or -1.0 < res.rate < 0.0


def test_build_cashflows_signs_buys_negative_and_sells_positive(tx):
    flows = build_cashflows(
        [tx(qty=100, price=10, day=1), tx(action="Sell", qty=50, price=20, day=200)],
        terminal_value_eur=900.0,
        as_of=date(2025, 12, 31),
    )
    assert flows[0][1] == pytest.approx(-1000.0)
    assert flows[1][1] == pytest.approx(1000.0)
    assert flows[-1] == (date(2025, 12, 31), 900.0)


def test_build_cashflows_includes_fees(tx):
    flows = build_cashflows(
        [tx(qty=100, price=10, fees=9.95, day=1)], 0.0, date(2025, 12, 31)
    )
    assert flows[0][1] == pytest.approx(-1009.95)


def test_build_cashflows_converts_usd_at_its_own_historical_rate(tx):
    flows = build_cashflows(
        [tx(qty=100, price=11.70, ccy="USD", fx=1.17, day=1)], 0.0, date(2025, 12, 31)
    )
    assert flows[0][1] == pytest.approx(-1000.0)


def test_end_to_end_on_a_realistic_flow_shape(tx):
    """One small early buy, one dominant later buy -- the real portfolio's shape."""
    flows = build_cashflows(
        [tx(qty=100, price=10, day=1), tx(qty=1000, price=100, day=91)],
        terminal_value_eur=105_000.0,
        as_of=date(2025, 1, 1) + timedelta(days=310),
    )
    res = xirr(flows)
    assert res.credible
    # Simple ratio is 105000/101000 - 1 = 3.96%; annualised over a weighted
    # holding period well under a year, it must come out higher.
    assert res.rate > (105_000.0 / 101_000.0 - 1)
