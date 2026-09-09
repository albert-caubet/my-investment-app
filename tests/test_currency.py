"""Currency conversion, and the valuation-keys-off-listing-currency fix."""

import pytest

from portfolio_math import (
    MissingRate,
    PositionState,
    Quote,
    from_base,
    portfolio_totals,
    to_base,
    value_position,
    weights,
)

RATES = {"USD": 1.17}


def test_eur_is_identity():
    assert to_base(100.0, "EUR", {}) == 100.0
    assert from_base(100.0, "EUR", {}) == 100.0


def test_usd_converts_by_dividing():
    assert to_base(117.0, "USD", RATES) == pytest.approx(100.0)
    assert from_base(100.0, "USD", RATES) == pytest.approx(117.0)


def test_case_insensitive():
    assert to_base(117.0, "usd", RATES) == pytest.approx(100.0)


@pytest.mark.parametrize("rates", [{}, {"USD": 0.0}, {"USD": -1.0}, {"USD": float("nan")}])
def test_missing_or_bogus_rate_raises_never_defaults_to_one(rates):
    """A silent 1.0 is how a USD basis quietly ends up ~15% too low."""
    with pytest.raises(MissingRate):
        to_base(117.0, "USD", rates)


def _pos(qty=100.0, basis=1000.0, realised=0.0):
    return PositionState(
        asset_id="X", quantity=qty, cost_basis_eur=basis, realised_pnl_eur=realised
    )


def test_value_position_uses_listing_currency():
    """A USD-listed asset bought with euros must still be converted."""
    val = value_position(_pos(), Quote("X", price=11.70, listing_ccy="USD"), RATES)
    assert val.market_value_eur == pytest.approx(1000.0)
    assert val.error is None


def test_value_position_eur_listing_not_converted():
    val = value_position(_pos(), Quote("X", price=12.0, listing_ccy="EUR"), RATES)
    assert val.market_value_eur == pytest.approx(1200.0)
    assert val.unrealised_pnl_eur == pytest.approx(200.0)
    assert val.unrealised_pnl_pct == pytest.approx(20.0)


def test_unsupported_listing_currency_errors_rather_than_valuing_one_to_one():
    """A GBp price valued as EUR would overstate by 100x. Refuse instead."""
    val = value_position(_pos(), Quote("X", price=250.0, listing_ccy="GBp"), RATES)
    assert val.market_value_eur is None
    assert "unsupported listing currency" in val.error


def test_missing_price_errors_without_raising():
    val = value_position(_pos(), None, RATES)
    assert val.market_value_eur is None
    assert val.error == "no price available"


def test_nan_price_errors():
    val = value_position(_pos(), Quote("X", float("nan"), "EUR"), RATES)
    assert val.market_value_eur is None


def test_total_pnl_includes_realised_even_when_unvaluable():
    val = value_position(_pos(realised=250.0), None, RATES)
    assert val.total_pnl_eur == pytest.approx(250.0)


def test_portfolio_totals_counts_unvalued_rather_than_zeroing():
    good = value_position(_pos(), Quote("A", 12.0, "EUR"), RATES)
    bad = value_position(_pos(), None, RATES)
    good.asset_id, bad.asset_id = "A", "B"

    tot = portfolio_totals([good, bad])
    assert tot.n_valued == 1
    assert tot.n_unvalued == 1
    assert tot.unvalued_ids == ["B"]
    # The broken position must not inflate cost basis with no value against it.
    assert tot.cost_basis_eur == pytest.approx(1000.0)
    assert tot.market_value_eur == pytest.approx(1200.0)


def test_portfolio_pnl_pct_is_none_on_zero_basis():
    v = value_position(PositionState(asset_id="X"), Quote("X", 1.0, "EUR"), RATES)
    assert portfolio_totals([v]).pnl_pct is None


def test_weights_sum_to_100():
    vals = [
        value_position(_pos(qty=100), Quote("A", 12.0, "EUR"), RATES),
        value_position(_pos(qty=50), Quote("B", 8.0, "EUR"), RATES),
    ]
    vals[0].asset_id, vals[1].asset_id = "A", "B"
    assert sum(weights(vals).values()) == pytest.approx(100.0)


def test_unknown_listing_currency_is_not_assumed_to_be_eur():
    """Regression: the dashboard defaulted an unknown listing currency to EUR.

    A failed metadata lookup then valued a USD price one-for-one as euros, ~15%
    too high. Refuse instead, the same way a missing FX rate is refused.
    """
    val = value_position(_pos(), Quote("X", price=11.70, listing_ccy=None), RATES)
    assert val.market_value_eur is None
    assert "unknown" in val.error
    assert val.total_pnl_eur == pytest.approx(0.0)  # realised still flows through


def test_empty_string_listing_currency_is_also_unknown():
    val = value_position(_pos(), Quote("X", price=11.70, listing_ccy=""), RATES)
    assert val.market_value_eur is None
