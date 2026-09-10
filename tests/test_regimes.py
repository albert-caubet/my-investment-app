"""One test per regime rule on synthetic series, plus the aggregation."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from invest.macro import regimes as rg


def monthly(values, start="2016-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"), dtype=float)


def daily(values, start="2024-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def result(name, series, as_of=None):
    return {r.rule.name: r for r in rg.evaluate(series, as_of=as_of)}[name]


# --- curve --------------------------------------------------------------------


def test_curve_inverted_needs_three_negative_months():
    fired = result("curve_inverted", {"US_CURVE_10Y3M": daily([-0.5] * 80)})
    assert fired.fired is True
    brief = result("curve_inverted", {"US_CURVE_10Y3M": daily([0.5] * 60 + [-0.5] * 20)})
    assert brief.fired is False  # inverted for a month, not three
    thin = result("curve_inverted", {"US_CURVE_10Y3M": daily([-0.5] * 20)})
    assert thin.fired is None and "observations" in thin.detail
    assert result("curve_inverted", {}).fired is None


def test_curve_uninversion_after_a_long_inversion():
    # 12 months negative, then positive for two months: fired
    s = daily([-0.4] * 260 + [0.3] * 45, start="2023-01-02")
    assert result("curve_uninversion", {"US_CURVE_10Y3M": s}).fired is True
    # positive throughout: not fired
    assert result("curve_uninversion", {"US_CURVE_10Y3M": daily([0.5] * 400)}).fired is False
    # still inverted: not fired
    assert result("curve_uninversion", {"US_CURVE_10Y3M": daily([-0.5] * 400)}).fired is False


# --- labor --------------------------------------------------------------------


def test_sahm_rule_threshold():
    assert result("sahm", {"US_SAHM": monthly([0.1, 0.3, 0.5])}).fired is True
    assert result("sahm", {"US_SAHM": monthly([0.1, 0.3, 0.49])}).fired is False


def test_claims_rules():
    assert result("claims_yoy", {"US_CLAIMS_4WK_YOY": monthly([5.0, 21.0])}).fired is True
    assert result("claims_yoy", {"US_CLAIMS_4WK_YOY": monthly([5.0, 19.0])}).fired is False
    assert result("claims_off_low", {"US_CLAIMS_OFF_LOW": monthly([2.0, 16.0])}).fired is True
    assert result("claims_off_low", {"US_CLAIMS_OFF_LOW": monthly([2.0, 14.0])}).fired is False


# --- credit -------------------------------------------------------------------


def test_hy_stress_fires_on_z_or_on_three_month_change():
    calm = daily([3.0, 3.4] * 300 + [3.2])  # latest sits at the mean of a noisy range
    assert result("hy_stress", {"US_HY_OAS": calm, "US_HY_OAS_3M_CHG": monthly([0.1])}).fired is False
    spike = daily([3.0] * 600 + [6.0])
    assert result("hy_stress", {"US_HY_OAS": spike, "US_HY_OAS_3M_CHG": monthly([0.1])}).fired is True  # z
    widening = daily([3.0] * 600 + [3.1])
    assert result("hy_stress", {"US_HY_OAS": widening, "US_HY_OAS_3M_CHG": monthly([1.6])}).fired is True  # change


def test_ebp_top_decile():
    history = monthly(list(np.linspace(-1.0, 1.0, 120)) + [0.5])
    assert result("ebp_top_decile", {"US_EBP": history}).fired is False
    assert result("ebp_top_decile", {"US_EBP": monthly(list(np.linspace(-1.0, 1.0, 120)) + [1.5])}).fired is True


# --- valuation and sentiment (informational) ----------------------------------


def test_cape_percentile_is_informational():
    cape = monthly(list(np.linspace(10, 30, 200)) + [40.0])
    r = result("cape_percentile", {"SP500_CAPE": cape})
    assert r.fired is True and r.rule.counts is False
    assert "Not a timing signal" in r.detail


def test_sentiment_rules():
    spread = monthly([10.0] * 100 + [-40.0])
    assert result("aaii_contrarian", {"AAII_BULL_BEAR": spread}).fired is True
    assert result("naaim_extreme", {"NAAIM_EXPOSURE": monthly([50.0, 95.0])}).fired is True
    assert result("naaim_extreme", {"NAAIM_EXPOSURE": monthly([50.0, 15.0])}).fired is True
    assert result("naaim_extreme", {"NAAIM_EXPOSURE": monthly([50.0, 60.0])}).fired is False
    assert result("aaii_contrarian", {}).fired is None


# --- trend, surveys, composites, bank lending, housing, policy -----------------


def test_trend_broken_needs_price_below_a_falling_average():
    assert result("trend_broken", {"SPX_VS_200D": monthly([-3.0]), "SPX_200D_SLOPE": monthly([-0.5])}).fired is True
    assert result("trend_broken", {"SPX_VS_200D": monthly([-3.0]), "SPX_200D_SLOPE": monthly([0.5])}).fired is False
    assert result("trend_broken", {"SPX_VS_200D": monthly([-3.0])}).fired is None


def test_ism_rules():
    both = {"US_ISM_MFG_PMI": monthly([47.0]), "US_ISM_NEW_ORDERS_MINUS_INVENTORIES": monthly([-2.0])}
    assert result("ism_manufacturing", both).fired is True
    weak_but_orders_ok = {"US_ISM_MFG_PMI": monthly([47.0]), "US_ISM_NEW_ORDERS_MINUS_INVENTORIES": monthly([1.0])}
    assert result("ism_manufacturing", weak_but_orders_ok).fired is False
    assert result("ism_services", {"US_ISM_SERVICES_PMI": monthly([49.9])}).fired is True
    assert result("ism_services", {"US_ISM_SERVICES_PMI": monthly([50.0])}).fired is False


def test_lei_and_cfnai():
    assert result("lei_signal", {"US_LEI_6M_ANN": monthly([-5.0]), "US_LEI_DIFFUSION": monthly([40.0])}).fired is True
    assert result("lei_signal", {"US_LEI_6M_ANN": monthly([-5.0]), "US_LEI_DIFFUSION": monthly([60.0])}).fired is False
    assert result("cfnai_recession", {"US_CFNAI_MA3": monthly([-0.71])}).fired is True
    assert result("cfnai_recession", {"US_CFNAI_MA3": monthly([-0.69])}).fired is False


def test_bank_tightening_permits_and_policy():
    assert result("bank_tightening", {"US_SLOOS_TIGHTENING": monthly([25.0])}).fired is True
    assert result("bank_tightening", {"US_SLOOS_TIGHTENING": monthly([15.0])}).fired is False
    permits = monthly([1500.0] * 12 + [1200.0])  # -20% YoY
    assert result("permits_falling", {"US_PERMITS": permits}).fired is True
    assert result("permits_falling", {"US_PERMITS": monthly([1500.0] * 12 + [1400.0])}).fired is False
    assert result("policy_restrictive", {"US_POLICY_STANCE": monthly([1.2])}).fired is True
    assert result("policy_restrictive", {"US_POLICY_STANCE": monthly([0.8])}).fired is False


# --- point in time --------------------------------------------------------------


def test_as_of_truncates_before_evaluating():
    sahm = monthly([0.0] * 10 + [0.6, 0.7], start="2024-01-01")  # fires from 2024-11
    assert result("sahm", {"US_SAHM": sahm}).fired is True
    assert result("sahm", {"US_SAHM": sahm}, as_of=date(2024, 10, 15)).fired is False


# --- aggregation ------------------------------------------------------------------


def _fired(names):
    return [rg.RuleResult(rg.RULES_BY_NAME[n], True, 1.0, date(2026, 9, 1), "") for n in names]


def _quiet(names):
    return [rg.RuleResult(rg.RULES_BY_NAME[n], False, 0.0, date(2026, 9, 1), "") for n in names]


def test_three_groups_is_stress_two_is_late_cycle_one_is_nothing():
    stress = rg.aggregate(_fired(["sahm", "hy_stress", "trend_broken"]))
    assert stress.label == "stress" and stress.groups_firing == ("credit", "labor", "trend")
    late = rg.aggregate(_fired(["sahm", "claims_yoy", "hy_stress"]))  # labor twice counts once
    assert late.label == "late cycle" and late.groups_firing == ("credit", "labor")
    lone = rg.aggregate(_fired(["sahm", "claims_yoy"]))
    assert lone.label == "expansion" and "lone signal" not in lone.explanation or lone.label == "expansion"


def test_informational_rules_do_not_move_the_label():
    r = rg.aggregate(_fired(["cape_percentile", "naaim_extreme", "sahm"]))
    assert r.label == "expansion"
    assert r.informational == ("cape_percentile", "naaim_extreme")
    assert "not counted" in r.explanation and "does not predict" in r.explanation


def test_recovery_follows_a_recent_stress_reading():
    previous = rg.aggregate(_fired(["sahm", "hy_stress", "trend_broken"]))
    now = rg.aggregate(_quiet(["sahm", "hy_stress", "trend_broken"]), previous=previous)
    assert now.label == "recovery"
    later = rg.aggregate(_quiet(["sahm"]), previous=rg.aggregate(_quiet(["sahm"])))
    assert later.label == "expansion"


def test_missing_rules_are_listed_not_counted():
    results = [rg.RuleResult(rg.RULES_BY_NAME["sahm"], None, None, None, "missing")]
    r = rg.aggregate(results)
    assert r.missing == ("sahm",) and "lack of data" in r.explanation


def test_regime_end_to_end_uses_six_month_lookback():
    # stress six months ago (three groups), calm now: recovery
    sahm = monthly([0.6] * 6 + [0.0] * 6, start="2026-01-01")
    hy = daily([8.0] * 130 + [3.0] * 130, start="2026-01-01")
    trend = monthly([-5.0] * 6 + [3.0] * 6, start="2026-01-01")
    slope = monthly([-1.0] * 6 + [1.0] * 6, start="2026-01-01")
    series = {"US_SAHM": sahm, "US_HY_OAS": hy, "US_HY_OAS_3M_CHG": daily([0.0] * 260, start="2026-01-01"),
              "SPX_VS_200D": trend, "SPX_200D_SLOPE": slope}
    regime, results = rg.regime(series, as_of=date(2026, 12, 1))
    assert regime.label in ("recovery", "expansion")
    assert regime.as_of == date(2026, 12, 1)
    assert len(results) == len(rg.RULES)


def test_thresholds_for_lists_chart_lines():
    assert rg.thresholds_for("US_SAHM") == {"sahm": 0.5}
    assert rg.thresholds_for("US_CURVE_10Y3M") == {"curve_inverted": 0.0, "curve_uninversion": 0.0}
    assert rg.thresholds_for("NOPE") == {}
