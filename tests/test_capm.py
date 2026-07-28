"""CAPM estimator: the ddof regression and the credibility guard."""

import numpy as np
import pytest

from portfolio_math import MIN_CAPM_OBS, estimate_capm

RF = 0.046


def _series(beta=1.3, n=500, noise=0.004, seed=7):
    rng = np.random.default_rng(seed)
    market = rng.normal(0.0004, 0.01, n)
    asset = beta * market + rng.normal(0.0, noise, n)
    return asset, market


def test_recovers_known_beta():
    asset, market = _series(beta=1.3)
    res = estimate_capm(asset, market, rf_annual=RF)
    assert res.beta == pytest.approx(1.3, abs=0.05)
    assert res.credible
    assert res.n_obs == 500


def test_beta_matches_ols_slope():
    """Regression for the np.cov(ddof=1) / np.var(ddof=0) mismatch.

    The old pairing inflated beta by exactly n/(n-1).
    """
    asset, market = _series(beta=0.8)
    res = estimate_capm(asset, market, rf_annual=RF)
    ols_slope = np.polyfit(market - RF / 252, asset - RF / 252, 1)[0]
    assert res.beta == pytest.approx(ols_slope, rel=1e-9)


def test_old_mismatched_estimator_was_inflated():
    asset, market = _series(beta=1.0, n=80)
    res = estimate_capm(asset, market, rf_annual=RF, min_obs=60)
    ex_a, ex_m = asset - RF / 252, market - RF / 252
    old = np.cov(ex_a, ex_m)[0, 1] / np.var(ex_m)  # cov ddof=1, var ddof=0
    assert old == pytest.approx(res.beta * 80 / 79, rel=1e-9)


def test_short_window_is_not_credible():
    """Six observations is what the 7d fetch actually produced."""
    asset, market = _series(n=6)
    res = estimate_capm(asset, market, rf_annual=RF)
    assert not res.credible
    assert res.beta is None
    assert res.alpha_annual is None
    assert res.n_obs == 6
    assert "minimum" in res.note


def test_boundary_at_min_obs():
    asset, market = _series(n=MIN_CAPM_OBS)
    assert estimate_capm(asset, market, rf_annual=RF).credible
    asset, market = _series(n=MIN_CAPM_OBS - 1)
    assert not estimate_capm(asset, market, rf_annual=RF).credible


def test_non_finite_pairs_are_dropped():
    asset, market = _series(n=200)
    asset[5], market[9] = np.nan, np.inf
    res = estimate_capm(asset, market, rf_annual=RF)
    assert res.n_obs == 198
    assert np.isfinite(res.beta)


def test_zero_variance_benchmark_is_not_credible():
    res = estimate_capm(np.random.default_rng(1).normal(0, 0.01, 100), np.zeros(100), rf_annual=RF)
    assert not res.credible
    assert "variance" in res.note


def test_misaligned_series_rejected():
    assert not estimate_capm(np.zeros(10), np.zeros(9), rf_annual=RF).credible


def test_r_squared_is_reported_and_bounded():
    asset, market = _series(beta=1.0, noise=0.02)
    res = estimate_capm(asset, market, rf_annual=RF)
    assert 0.0 <= res.r_squared <= 1.0


def test_alpha_is_near_zero_for_a_pure_beta_asset():
    """An asset that is exactly beta x market should show no skill."""
    rng = np.random.default_rng(3)
    market = rng.normal(0.0003, 0.01, 800)
    res = estimate_capm(1.2 * market, market, rf_annual=RF)
    assert res.alpha_annual == pytest.approx(0.0, abs=0.02)
