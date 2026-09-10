"""EPV and DCF with their inputs, hand-computed."""

import pytest

from invest.fundamentals import valuation as val


def test_effective_tax_rate_bounds():
    assert val.effective_tax_rate(21, 100) == pytest.approx(0.21)
    assert val.effective_tax_rate(2, 100) == val.DEFAULT_TAX_RATE   # 2% is not a real rate
    assert val.effective_tax_rate(50, 100) == val.DEFAULT_TAX_RATE  # nor is 50%
    assert val.effective_tax_rate(None, 100) == val.DEFAULT_TAX_RATE
    assert val.effective_tax_rate(10, -100) == val.DEFAULT_TAX_RATE


def test_normalised_ebit_averages_the_last_five_years():
    assert val.normalised_ebit({2019: 50, 2020: 60, 2021: 70, 2022: 80, 2023: 90, 2024: 100}) == 80
    assert val.normalised_ebit({2024: 100, 2025: 110}) is None


def test_epv_by_hand():
    # 150 EBIT, 21% tax, 9% cost of capital: EV = 150 * 0.79 / 0.09 = 1316.67; equity = EV - 300 net debt
    result = val.epv(ebit_normalised=150, tax_rate=0.21, cost_of_capital=0.09, net_debt=300, shares=100)
    assert result.equity_value == pytest.approx(150 * 0.79 / 0.09 - 300)
    assert result.value_per_share == pytest.approx((150 * 0.79 / 0.09 - 300) / 100)
    assert result.inputs["enterprise_value"] == pytest.approx(150 * 0.79 / 0.09)
    assert result.notes == ()
    missing = val.epv(ebit_normalised=None, tax_rate=0.21, cost_of_capital=0.09, net_debt=0, shares=100)
    assert missing.value_per_share is None and "no normalised EBIT" in missing.notes


def test_dcf_by_hand():
    r, g, tg = 0.09, 0.03, 0.02
    result = val.dcf(fcf_base=100, growth=g, discount_rate=r, terminal_growth=tg, net_debt=0, shares=10)
    pv_explicit = sum(100 * (1 + g) ** t / (1 + r) ** t for t in range(1, 6))
    fcf5 = 100 * (1 + g) ** 5
    pv_terminal = fcf5 * (1 + tg) / (r - tg) / (1 + r) ** 5
    assert result.inputs["pv_explicit"] == pytest.approx(pv_explicit)
    assert result.inputs["pv_terminal"] == pytest.approx(pv_terminal)
    assert result.value_per_share == pytest.approx((pv_explicit + pv_terminal) / 10)
    assert 0.5 < result.inputs["terminal_share"] < 0.9


def test_dcf_refuses_bad_inputs():
    assert val.dcf(fcf_base=-5, growth=0.0, discount_rate=0.09, terminal_growth=0.02, net_debt=0, shares=10).value_per_share is None
    assert "must exceed" in val.dcf(fcf_base=5, growth=0.0, discount_rate=0.01, terminal_growth=0.02, net_debt=0, shares=10).notes[0]


def test_conservative_growth_is_floored_and_capped():
    assert val.conservative_growth(0.12) == 0.05
    assert val.conservative_growth(-0.10) == 0.0
    assert val.conservative_growth(None) == 0.0
    assert val.conservative_growth(0.03) == pytest.approx(0.03)


def test_margin_of_safety():
    assert val.margin_of_safety(80, 100) == pytest.approx(0.2)
    assert val.margin_of_safety(120, 100) == pytest.approx(-0.2)
    assert val.margin_of_safety(80, None) is None
    assert val.margin_of_safety(80, -1) is None


def test_value_summary_takes_the_more_conservative_estimate():
    summary = val.value_summary(price=50, ebit_by_year={2021: 100, 2022: 110, 2023: 120, 2024: 130, 2025: 140},
                                income_tax=25, pretax_income=120, net_debt=100, shares=20, fcf=90, revenue_cagr=0.04)
    assert summary["epv"]["value_per_share"] is not None and summary["dcf"]["value_per_share"] is not None
    assert summary["value_conservative"] == min(summary["epv"]["value_per_share"], summary["dcf"]["value_per_share"])
    assert summary["mos_conservative"] == pytest.approx(1 - 50 / summary["value_conservative"])
    assert summary["epv"]["inputs"]["tax_rate"] == pytest.approx(25 / 120)
