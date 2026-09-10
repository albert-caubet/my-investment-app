"""Derived-series formulas against hand-computed values."""

import math

import numpy as np
import pandas as pd
import pytest

from invest.macro import derived as d


def monthly(values, start="2020-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"), dtype=float)


def weekly(values, start="2020-01-04"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="W-SAT"), dtype=float)


def daily(values, start="2020-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def test_infer_frequency():
    assert d.infer_frequency(monthly([1] * 12)) == "M"
    assert d.infer_frequency(weekly([1] * 12)) == "W"
    assert d.infer_frequency(daily([1] * 12)) == "D"
    q = pd.Series([1.0] * 8, index=pd.date_range("2020-01-01", periods=8, freq="QS"))
    assert d.infer_frequency(q) == "Q"


def test_yoy_monthly_is_twelve_periods_back():
    s = monthly(list(range(100, 125)))
    out = d.yoy(s).dropna()
    # 112 vs 100 a year earlier: +12%
    assert out.iloc[0] == pytest.approx(12.0)
    assert out.index[0] == pd.Timestamp("2021-01-01")


def test_yoy_daily_uses_the_calendar_not_a_fixed_count():
    s = daily([100.0] * 300 + [110.0] * 100, start="2020-01-01")
    out = d.yoy(s).dropna()
    # once a full year has passed the comparison is against the 100 level
    assert out.iloc[-1] == pytest.approx(10.0)


def test_ann3m():
    s = monthly([100, 101, 102, 103])
    # (103/100)^4 - 1
    assert d.ann3m(s).dropna().iloc[-1] == pytest.approx(((1.03) ** 4 - 1) * 100)


def test_diff3m_and_diff12m_on_monthly():
    s = monthly(list(range(0, 15)))
    assert d.diff3m(s).dropna().iloc[-1] == 3.0
    assert d.diff12m(s).dropna().iloc[-1] == 12.0


def test_ma4w_is_a_plain_four_week_mean():
    s = weekly([200, 210, 220, 230, 240])
    out = d.ma4w(s).dropna()
    assert out.iloc[0] == pytest.approx(215.0)
    assert out.iloc[-1] == pytest.approx(225.0)


def test_sahm_rule_matches_definition():
    # unemployment flat at 4.0 for 15 months, then 4.3, 4.6, 4.9
    u = monthly([4.0] * 15 + [4.3, 4.6, 4.9])
    out = d.sahm(u).dropna()
    # 3m avg at the end = (4.3+4.6+4.9)/3 = 4.6; low of prior 12 months of 3m avg = 4.0 -> 0.6
    assert out.iloc[-1] == pytest.approx(0.6)
    # before the rise it is exactly zero
    assert out.iloc[0] == pytest.approx(0.0)


def test_claims_off_low_percent_above_trailing_low():
    c = weekly([200.0] * 40 + [230.0])
    out = d.claims_off_low(c).dropna()
    assert out.iloc[-1] == pytest.approx(15.0)


def test_payrolls_3m_change_is_mean_of_monthly_changes():
    p = monthly([100, 110, 130, 160])  # changes 10, 20, 30
    assert d.payrolls_3m_change(p).dropna().iloc[-1] == pytest.approx(20.0)


def test_net_liquidity_units_and_alignment():
    wed = pd.date_range("2024-01-03", periods=3, freq="W-WED")
    walcl = pd.Series([7_000_000.0, 7_000_000.0, 6_900_000.0], index=wed)   # $ millions
    tga = pd.Series([700_000.0, 800_000.0, 750_000.0], index=wed)            # $ millions
    rrp_days = pd.bdate_range("2024-01-01", periods=20)
    rrp = pd.Series(500.0, index=rrp_days)                                   # $ billions
    out = d.net_liquidity(walcl, tga, rrp)
    assert list(out.index) == list(wed)
    assert out.iloc[0] == pytest.approx(7000 - 700 - 500)
    assert out.iloc[-1] == pytest.approx(6900 - 750 - 500)


def test_change_13w():
    s = weekly(list(range(20)))
    assert d.change_over_weeks(s, 13).dropna().iloc[-1] == 13.0


def test_real_rate_is_fisher_not_subtraction():
    nominal = monthly([6.73] * 3)
    inflation = monthly([3.0] * 3)
    out = d.real_rate(nominal, inflation)
    assert out.iloc[-1] == pytest.approx(3.6214, abs=1e-3)
    assert out.iloc[-1] != pytest.approx(3.73, abs=1e-3)


def test_policy_stance_uses_core_pce_yoy_and_forward_fills_quarterly_rstar():
    idx = pd.date_range("2023-10-01", periods=27, freq="MS")  # through 2025-12
    fedfunds = pd.Series(5.33, index=idx)
    # core PCE index growing 2.5% a year: 100 * 1.025^(m/12)
    pce = pd.Series([100 * 1.025 ** (m / 12) for m in range(27)], index=idx)
    rstar = pd.Series([0.8, 0.9], index=pd.to_datetime(["2024-10-01", "2025-01-01"]))
    out = d.policy_stance(fedfunds, pce, rstar).dropna()
    real = ((1.0533 / 1.025) - 1) * 100
    assert out.index[0] == pd.Timestamp("2024-10-01")  # needs a year of PCE and an r*
    assert out.loc["2024-12-01"] == pytest.approx(real - 0.8, abs=1e-6)  # r* carried within the quarter
    assert out.iloc[-1] == pytest.approx(real - 0.9, abs=1e-6)  # 2025-12 uses the 2025-01 r*


def test_buffett_ratio_converts_millions_over_billions():
    q = pd.date_range("2024-01-01", periods=2, freq="QS")
    equities = pd.Series([50_000_000.0, 60_000_000.0], index=q)  # $ millions
    gdp = pd.Series([28_000.0, 29_000.0], index=q)                # $ billions
    out = d.buffett(equities, gdp)
    assert out.iloc[0] == pytest.approx(50_000 / 28_000)


def test_trend_deviation_is_zero_on_an_exact_exponential():
    idx = pd.date_range("2000-01-01", periods=40, freq="QS")
    years = (idx - idx[0]).days / 365.25
    s = pd.Series(np.exp(0.05 * years), index=idx)
    out = d.trend_deviation(s)
    assert np.allclose(out.to_numpy(), 0.0, atol=1e-8)


def test_excess_cape_yield_definition():
    idx = pd.date_range("2000-01-01", periods=130, freq="MS")
    cape = pd.Series(25.0, index=idx)
    gs10 = pd.Series(4.0, index=idx)
    cpi = pd.Series([100 * 1.02 ** (m / 12) for m in range(130)], index=idx)
    out = d.excess_cape_yield(cape, gs10, cpi).dropna()
    # 1/25 = 4%; real long rate = 4% - 2% = 2%; excess = 2 percentage points
    assert out.iloc[-1] == pytest.approx(2.0, abs=1e-6)


def test_bollinger_pct_b_and_bandwidth():
    prices = daily([10.0] * 19 + [12.0])
    pct_b = d.bollinger_pct_b(prices, window=20, k=2.0).dropna().iloc[-1]
    mean = (19 * 10 + 12) / 20
    sd = math.sqrt((19 * (10 - mean) ** 2 + (12 - mean) ** 2) / 20)
    assert pct_b == pytest.approx((12 - (mean - 2 * sd)) / (4 * sd))
    assert d.bollinger_bandwidth(prices).dropna().iloc[-1] == pytest.approx(4 * sd / mean)


def test_pct_vs_ma_and_drawdown():
    prices = daily([100.0] * 199 + [110.0])
    ma = (199 * 100 + 110) / 200
    assert d.pct_vs_ma(prices, 200).dropna().iloc[-1] == pytest.approx((110 / ma - 1) * 100)
    dd = d.drawdown(daily([100, 120, 90, 96]))
    assert dd.tolist() == pytest.approx([0.0, 0.0, -25.0, -20.0])


def test_realised_vol_annualises_log_returns():
    rng = np.random.default_rng(0)
    rets = rng.normal(0, 0.01, 300)
    prices = pd.Series(100 * np.exp(np.cumsum(rets)), index=pd.bdate_range("2020-01-01", periods=300))
    out = d.realised_vol(prices, window=20).dropna()
    expected = np.std(np.diff(np.log(prices.to_numpy()))[-20:], ddof=1) * math.sqrt(252) * 100
    assert out.iloc[-1] == pytest.approx(expected)


def test_relative_strength_3m():
    a = daily([1.0] * 63 + [1.1])
    b = daily([1.0] * 64)
    assert d.relative_strength(a, b, 63).dropna().iloc[-1] == pytest.approx(10.0)


def test_mean_available_needs_a_minimum_count():
    idx = pd.date_range("2024-01-01", periods=3, freq="MS")
    a = pd.Series([10.0, 20.0, 30.0], index=idx)
    b = pd.Series([20.0, np.nan, 40.0], index=idx)
    c = pd.Series([np.nan, np.nan, 50.0], index=idx)
    out = d.mean_available(a, b, c, min_count=2)
    assert out.loc["2024-01-01"] == 15.0
    assert "2024-02-01" not in out.index.strftime("%Y-%m-%d")
    assert out.loc["2024-03-01"] == 40.0


def test_spread_forward_fills_the_slower_series_only():
    daily_idx = pd.bdate_range("2024-01-01", periods=10)
    fast = pd.Series(np.arange(10, dtype=float), index=daily_idx)
    slow = pd.Series([1.0], index=pd.to_datetime(["2024-01-03"]))
    out = d.spread(fast, slow)
    assert out.index[0] == pd.Timestamp("2024-01-03")  # nothing before the slow series exists
    assert out.iloc[-1] == 9.0 - 1.0


def test_deflated_yoy():
    nominal = monthly([100 * 1.05 ** (m / 12) for m in range(25)])
    cpi = monthly([100 * 1.03 ** (m / 12) for m in range(25)])
    out = d.deflated_yoy(nominal, cpi).dropna()
    assert out.iloc[-1] == pytest.approx((1.05 / 1.03 - 1) * 100, abs=1e-6)


def test_compute_rejects_unknown_formula_and_cleans_inf():
    with pytest.raises(KeyError):
        d.compute("nope", [monthly([1.0])])
    out = d.compute("ratio", [monthly([1.0, 2.0]), monthly([0.0, 4.0])])
    assert out.tolist() == [0.5]


def test_apply_transform_names():
    s = monthly(list(range(100, 114)))
    assert d.apply_transform(s, "level").equals(s)
    assert d.apply_transform(s, "yoy").iloc[-1] == pytest.approx((113 / 101 - 1) * 100)
    with pytest.raises(KeyError):
        d.apply_transform(s, "cube")
