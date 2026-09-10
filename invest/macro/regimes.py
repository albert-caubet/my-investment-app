"""Regime rules v1: transparent rules, a count of groups firing, and a label.

Each rule is a named, documented threshold over catalog series. ``evaluate``
returns one result per rule, fired or not or ``None`` when an input is missing;
``aggregate`` turns the results into a label the way PLAN.md section 4.3
prescribes: "stress" needs rules firing in at least three different groups,
"late cycle" two, a lone signal is listed but does not move the label, and
"recovery" is a label that follows a stress reading within the last six months.

Valuation and contrarian sentiment rules are reported but do not count towards
the label: CAPE says expected ten-year returns are low, not when; a sentiment
extreme argues the other way. The label summarises the rules that fired; it
predicts nothing, and the report says so.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Callable, Mapping

import pandas as pd

from invest.macro import derived
from invest.macro.indicators import percentile, truncate, zscore

STRESS_GROUPS = 3
LATE_CYCLE_GROUPS = 2
RECOVERY_LOOKBACK_MONTHS = 6


@dataclass(frozen=True)
class Rule:
    name: str
    group: str
    description: str
    formula: str
    inputs: tuple[str, ...]
    counts: bool = True
    #: horizontal lines to draw on the chart of each input: {series_id: level}
    thresholds: Mapping[str, float] | None = None


@dataclass(frozen=True)
class RuleResult:
    rule: Rule
    fired: bool | None
    value: float | None
    as_of: date | None
    detail: str

    def as_dict(self) -> dict:
        out = {
            "name": self.rule.name,
            "group": self.rule.group,
            "description": self.rule.description,
            "formula": self.rule.formula,
            "counts": self.rule.counts,
            "fired": self.fired,
            "value": self.value,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "detail": self.detail,
        }
        return out


@dataclass(frozen=True)
class Regime:
    label: str
    groups_firing: tuple[str, ...]
    firing: tuple[str, ...]
    informational: tuple[str, ...]
    missing: tuple[str, ...]
    explanation: str
    as_of: date | None

    def as_dict(self) -> dict:
        out = asdict(self)
        out["as_of"] = self.as_of.isoformat() if self.as_of else None
        return out


Series = Mapping[str, pd.Series]


def _latest(series: Series, sid: str, as_of: date | None) -> tuple[float | None, date | None]:
    s = series.get(sid)
    if s is None or s.empty:
        return None, None
    s = truncate(s.dropna(), as_of)
    if s.empty:
        return None, None
    return float(s.iloc[-1]), s.index[-1].date()


def _monthly_mean(s: pd.Series) -> pd.Series:
    return s.dropna().resample("MS").mean().dropna()


# --- rule functions: (series, as_of) -> (fired, value, as_of, detail) --------

def curve_inverted(series: Series, as_of: date | None):
    s = series.get("US_CURVE_10Y3M")
    if s is None or s.empty:
        return None, None, None, "missing: US_CURVE_10Y3M"
    s = truncate(s.dropna(), as_of)
    if s.empty:
        return None, None, None, "missing: US_CURVE_10Y3M"
    end = s.index[-1]
    window = s[s.index > end - pd.DateOffset(months=3)]
    if len(window) < 40:
        return None, float(s.iloc[-1]), end.date(), f"only {len(window)} daily observations in the last three months"
    peak = float(window.max())
    fired = peak < 0
    return fired, float(s.iloc[-1]), end.date(), f"highest 10y-3m spread over the last three months {peak:+.2f} pp"


def curve_uninversion(series: Series, as_of: date | None):
    s = series.get("US_CURVE_10Y3M")
    if s is None or s.empty:
        return None, None, None, "missing: US_CURVE_10Y3M"
    s = truncate(s.dropna(), as_of)
    if s.empty:
        return None, None, None, "missing: US_CURVE_10Y3M"
    monthly = _monthly_mean(s)
    if len(monthly) < 15:
        return None, float(s.iloc[-1]), s.index[-1].date(), "fewer than 15 months of history"
    latest = float(monthly.iloc[-1])
    last_12 = monthly.iloc[-13:-1]
    run, longest = 0, 0
    for value in last_12:
        run = run + 1 if value < 0 else 0
        longest = max(longest, run)
    fired = latest > 0 and longest >= 3
    detail = f"latest monthly mean {latest:+.2f} pp; longest inverted run in the prior year {longest} months"
    return fired, latest, monthly.index[-1].date(), detail


def sahm(series: Series, as_of: date | None):
    value, when = _latest(series, "US_SAHM", as_of)
    if value is None:
        return None, None, None, "missing: US_SAHM"
    return value >= 0.5, value, when, f"Sahm indicator {value:+.2f} pp (trigger 0.50)"


def claims_yoy(series: Series, as_of: date | None):
    value, when = _latest(series, "US_CLAIMS_4WK_YOY", as_of)
    if value is None:
        return None, None, None, "missing: US_CLAIMS_4WK_YOY"
    return value > 20, value, when, f"4-week average of initial claims {value:+.1f}% YoY (trigger +20%)"


def claims_off_low(series: Series, as_of: date | None):
    value, when = _latest(series, "US_CLAIMS_OFF_LOW", as_of)
    if value is None:
        return None, None, None, "missing: US_CLAIMS_OFF_LOW"
    return value > 15, value, when, f"claims {value:.1f}% above their 52-week low (trigger 15%)"


def hy_stress(series: Series, as_of: date | None):
    s = series.get("US_HY_OAS")
    if s is None or s.empty:
        return None, None, None, "missing: US_HY_OAS"
    s = truncate(s.dropna(), as_of)
    if s.empty:
        return None, None, None, "missing: US_HY_OAS"
    z, n = zscore(s, window_years=10)
    change, _ = _latest(series, "US_HY_OAS_3M_CHG", as_of)
    if change is None:
        change = float(derived.diff3m(s).dropna().iloc[-1]) if len(derived.diff3m(s).dropna()) else None
    parts = []
    fired = False
    if z is not None:
        parts.append(f"z {z:+.2f} (trigger +1)")
        fired = fired or z > 1
    if change is not None:
        parts.append(f"3-month change {change:+.2f} pp (trigger +1.50)")
        fired = fired or change > 1.5
    if z is None and change is None:
        return None, float(s.iloc[-1]), s.index[-1].date(), "not enough history for z-score or change"
    return fired, float(s.iloc[-1]), s.index[-1].date(), f"HY OAS {s.iloc[-1]:.2f} pp; " + "; ".join(parts)


def cape_percentile(series: Series, as_of: date | None):
    s = series.get("SP500_CAPE")
    if s is None or s.empty:
        return None, None, None, "missing: SP500_CAPE"
    s = truncate(s.dropna(), as_of)
    if s.empty:
        return None, None, None, "missing: SP500_CAPE"
    pct, n = percentile(s)
    if pct is None:
        return None, float(s.iloc[-1]), s.index[-1].date(), "not enough history"
    return pct > 90, float(s.iloc[-1]), s.index[-1].date(), f"CAPE {s.iloc[-1]:.1f}, {pct:.0f}th percentile since {s.index[0].year} (trigger 90th). Not a timing signal."


def aaii_contrarian(series: Series, as_of: date | None):
    s = series.get("AAII_BULL_BEAR")
    if s is None or s.empty:
        return None, None, None, "missing: AAII_BULL_BEAR (hand-entered)"
    s = truncate(s.dropna(), as_of)
    z, n = zscore(s, window_years=10)
    if z is None:
        return None, float(s.iloc[-1]) if len(s) else None, s.index[-1].date() if len(s) else None, f"only {n} observations"
    return z < -1.5, float(s.iloc[-1]), s.index[-1].date(), f"bull-bear spread z {z:+.2f} (contrarian bullish below -1.5)"


def naaim_extreme(series: Series, as_of: date | None):
    value, when = _latest(series, "NAAIM_EXPOSURE", as_of)
    if value is None:
        return None, None, None, "missing: NAAIM_EXPOSURE (hand-entered)"
    return value > 90 or value < 20, value, when, f"NAAIM exposure {value:.0f} (extremes above 90 or below 20)"


def trend_broken(series: Series, as_of: date | None):
    dist, when = _latest(series, "SPX_VS_200D", as_of)
    slope, _ = _latest(series, "SPX_200D_SLOPE", as_of)
    if dist is None or slope is None:
        return None, None, None, "missing: SPX_VS_200D or SPX_200D_SLOPE"
    fired = dist < 0 and slope < 0
    return fired, dist, when, f"S&P 500 {dist:+.1f}% vs its 200-day average, average slope {slope:+.2f}% over 20 days"


def ism_manufacturing(series: Series, as_of: date | None):
    pmi, when = _latest(series, "US_ISM_MFG_PMI", as_of)
    spread, _ = _latest(series, "US_ISM_NEW_ORDERS_MINUS_INVENTORIES", as_of)
    if pmi is None or spread is None:
        return None, pmi, when, "missing: ISM manufacturing PMI or new orders minus inventories (hand-entered)"
    return pmi < 48 and spread < 0, pmi, when, f"ISM manufacturing {pmi:.1f} (trigger below 48) with new orders minus inventories {spread:+.1f} (trigger below 0)"


def ism_services(series: Series, as_of: date | None):
    value, when = _latest(series, "US_ISM_SERVICES_PMI", as_of)
    if value is None:
        return None, None, None, "missing: US_ISM_SERVICES_PMI (hand-entered)"
    return value < 50, value, when, f"ISM services {value:.1f} (trigger below 50)"


def lei_signal(series: Series, as_of: date | None):
    change, when = _latest(series, "US_LEI_6M_ANN", as_of)
    diffusion, _ = _latest(series, "US_LEI_DIFFUSION", as_of)
    if change is None or diffusion is None:
        return None, change, when, "missing: LEI 6-month change or diffusion (hand-entered)"
    return change < -4 and diffusion < 50, change, when, f"LEI 6-month annualised {change:+.1f}% (trigger below -4) with diffusion {diffusion:.0f} (trigger below 50)"


def cfnai_recession(series: Series, as_of: date | None):
    value, when = _latest(series, "US_CFNAI_MA3", as_of)
    if value is None:
        return None, None, None, "missing: US_CFNAI_MA3"
    return value < -0.7, value, when, f"CFNAI 3-month average {value:+.2f} (trigger below -0.70); confirms a recession already begun"


def bank_tightening(series: Series, as_of: date | None):
    value, when = _latest(series, "US_SLOOS_TIGHTENING", as_of)
    if value is None:
        return None, None, None, "missing: US_SLOOS_TIGHTENING"
    return value > 20, value, when, f"net {value:+.1f}% of banks tightening C&I standards (trigger +20%)"


def ebp_top_decile(series: Series, as_of: date | None):
    s = series.get("US_EBP")
    if s is None or s.empty:
        return None, None, None, "missing: US_EBP"
    s = truncate(s.dropna(), as_of)
    if s.empty:
        return None, None, None, "missing: US_EBP"
    pct, n = percentile(s)
    if pct is None:
        return None, float(s.iloc[-1]), s.index[-1].date(), "not enough history"
    return pct > 90, float(s.iloc[-1]), s.index[-1].date(), f"excess bond premium {s.iloc[-1]:+.2f} pp, {pct:.0f}th percentile since {s.index[0].year} (trigger 90th)"


def permits_falling(series: Series, as_of: date | None):
    s = series.get("US_PERMITS")
    if s is None or s.empty:
        return None, None, None, "missing: US_PERMITS"
    s = truncate(s.dropna(), as_of)
    yoy = derived.yoy(s).dropna()
    if yoy.empty:
        return None, None, None, "not enough history for YoY"
    value = float(yoy.iloc[-1])
    return value < -15, value, yoy.index[-1].date(), f"building permits {value:+.1f}% YoY (trigger below -15%)"


def policy_restrictive(series: Series, as_of: date | None):
    value, when = _latest(series, "US_POLICY_STANCE", as_of)
    if value is None:
        return None, None, None, "missing: US_POLICY_STANCE"
    return value > 1, value, when, f"real Fed funds minus r* {value:+.2f} pp (trigger above +1)"


RULES: tuple[tuple[Rule, Callable], ...] = (
    (Rule("curve_inverted", "curve", "10y minus 3m below zero for three months or more",
          "max(T10Y3M over 3 months) < 0", ("US_CURVE_10Y3M",), thresholds={"US_CURVE_10Y3M": 0.0}), curve_inverted),
    (Rule("curve_uninversion", "curve", "Un-inversion after an inversion of three months or more within the past year",
          "monthly mean T10Y3M > 0 after a run of >= 3 negative months in the prior 12", ("US_CURVE_10Y3M",),
          thresholds={"US_CURVE_10Y3M": 0.0}), curve_uninversion),
    (Rule("sahm", "labor", "Sahm rule triggered", "SAHMREALTIME >= 0.5", ("US_SAHM",),
          thresholds={"US_SAHM": 0.5}), sahm),
    (Rule("claims_yoy", "labor", "Initial claims 4-week average up more than 20% YoY", "yoy(ma4w(ICSA)) > 20",
          ("US_CLAIMS_4WK_YOY",), thresholds={"US_CLAIMS_4WK_YOY": 20.0}), claims_yoy),
    (Rule("claims_off_low", "labor", "Initial claims 4-week average more than 15% above its 52-week low",
          "ma4w(ICSA) / min52w - 1 > 15%", ("US_CLAIMS_OFF_LOW",), thresholds={"US_CLAIMS_OFF_LOW": 15.0}), claims_off_low),
    (Rule("hy_stress", "credit", "HY OAS z-score above 1, or up 150bp in three months",
          "z10y(HY OAS) > 1 or diff3m(HY OAS) > 1.5", ("US_HY_OAS", "US_HY_OAS_3M_CHG"),
          thresholds={"US_HY_OAS_3M_CHG": 1.5}), hy_stress),
    (Rule("cape_percentile", "valuation", "CAPE above its 90th percentile (low expected ten-year returns; not a timing signal)",
          "percentile(CAPE) > 90", ("SP500_CAPE",), counts=False), cape_percentile),
    (Rule("aaii_contrarian", "sentiment", "AAII bull-bear spread z-score below -1.5 (contrarian bullish)",
          "z10y(AAII spread) < -1.5", ("AAII_BULL_BEAR",), counts=False), aaii_contrarian),
    (Rule("naaim_extreme", "sentiment", "NAAIM exposure above 90 or below 20", "NAAIM > 90 or NAAIM < 20",
          ("NAAIM_EXPOSURE",), counts=False, thresholds={"NAAIM_EXPOSURE": 90.0}), naaim_extreme),
    (Rule("trend_broken", "trend", "S&P 500 below a falling 200-day average", "SPX < MA200 and MA200 falling",
          ("SPX_VS_200D", "SPX_200D_SLOPE"), thresholds={"SPX_VS_200D": 0.0, "SPX_200D_SLOPE": 0.0}), trend_broken),
    (Rule("ism_manufacturing", "surveys", "ISM Manufacturing below 48 with New Orders below Inventories",
          "PMI < 48 and NO - INV < 0", ("US_ISM_MFG_PMI", "US_ISM_NEW_ORDERS_MINUS_INVENTORIES"),
          thresholds={"US_ISM_MFG_PMI": 48.0, "US_ISM_NEW_ORDERS_MINUS_INVENTORIES": 0.0}), ism_manufacturing),
    (Rule("ism_services", "surveys", "ISM Services below 50", "Services PMI < 50", ("US_ISM_SERVICES_PMI",),
          thresholds={"US_ISM_SERVICES_PMI": 50.0}), ism_services),
    (Rule("lei_signal", "composites", "LEI 6-month annualised change below -4% with diffusion below 50",
          "LEI6m < -4 and diffusion < 50", ("US_LEI_6M_ANN", "US_LEI_DIFFUSION"),
          thresholds={"US_LEI_6M_ANN": -4.0, "US_LEI_DIFFUSION": 50.0}), lei_signal),
    (Rule("cfnai_recession", "composites", "CFNAI 3-month average below -0.7 (confirms a recession has begun)",
          "CFNAIMA3 < -0.7", ("US_CFNAI_MA3",), thresholds={"US_CFNAI_MA3": -0.7}), cfnai_recession),
    (Rule("bank_tightening", "bank_lending", "Loan officer survey net tightening above 20%", "DRTSCILM > 20",
          ("US_SLOOS_TIGHTENING",), thresholds={"US_SLOOS_TIGHTENING": 20.0}), bank_tightening),
    (Rule("ebp_top_decile", "credit", "Excess bond premium in its top decile", "percentile(EBP) > 90", ("US_EBP",)),
     ebp_top_decile),
    (Rule("permits_falling", "housing", "Building permits down more than 15% YoY", "yoy(PERMIT) < -15",
          ("US_PERMITS",)), permits_falling),
    (Rule("policy_restrictive", "policy", "Real Fed funds more than one point above r*", "real FF - r* > 1",
          ("US_POLICY_STANCE",), thresholds={"US_POLICY_STANCE": 1.0}), policy_restrictive),
)

RULES_BY_NAME = {rule.name: rule for rule, _ in RULES}


def thresholds_for(series_id: str) -> dict[str, float]:
    """``{rule name: level}`` of every rule that draws a line on this series."""
    out = {}
    for rule, _ in RULES:
        if rule.thresholds and series_id in rule.thresholds:
            out[rule.name] = rule.thresholds[series_id]
    return out


def evaluate(series: Series, *, as_of: date | None = None) -> list[RuleResult]:
    results = []
    for rule, fn in RULES:
        try:
            fired, value, when, detail = fn(series, as_of)
        except Exception as exc:  # a broken input must not take the scorecard down
            fired, value, when, detail = None, None, None, f"error: {exc}"
        results.append(RuleResult(rule, fired, value, when, detail))
    return results


def aggregate(results: list[RuleResult], *, previous: "Regime | None" = None, as_of: date | None = None) -> Regime:
    firing = tuple(r.rule.name for r in results if r.fired and r.rule.counts)
    informational = tuple(r.rule.name for r in results if r.fired and not r.rule.counts)
    missing = tuple(r.rule.name for r in results if r.fired is None)
    groups = tuple(sorted({r.rule.group for r in results if r.fired and r.rule.counts}))

    if len(groups) >= STRESS_GROUPS:
        label = "stress"
    elif len(groups) >= LATE_CYCLE_GROUPS:
        label = "late cycle"
    elif previous is not None and previous.label == "stress":
        label = "recovery"
    else:
        label = "expansion"

    if groups:
        core = (f"Rules firing in {len(groups)} group(s) ({', '.join(groups)}): {', '.join(firing)}. ")
    else:
        core = "No counted rule is firing. "
    if label == "stress":
        core += f"Three or more groups firing reads as stress."
    elif label == "late cycle":
        core += "Two groups firing reads as late cycle; a lone signal would not move the label."
    elif label == "recovery":
        core += f"Fewer than two groups fire now, after a stress reading within the last {RECOVERY_LOOKBACK_MONTHS} months: recovery."
    else:
        core += "Fewer than two groups fire: expansion."
    if informational:
        core += f" Reported but not counted: {', '.join(informational)}."
    if missing:
        core += f" Not evaluated for lack of data: {', '.join(missing)}."
    core += " The label summarises the rules; it does not predict anything."
    return Regime(label, groups, firing, informational, missing, core, as_of)


def regime(series: Series, *, as_of: date | None = None) -> tuple[Regime, list[RuleResult]]:
    """Label now, with the six-months-earlier label consulted for 'recovery'."""
    now = as_of or max((s.index[-1].date() for s in series.values() if s is not None and not s.empty), default=None)
    results = evaluate(series, as_of=as_of)
    previous = None
    if now is not None:
        earlier = (pd.Timestamp(now) - pd.DateOffset(months=RECOVERY_LOOKBACK_MONTHS)).date()
        previous = aggregate(evaluate(series, as_of=earlier), as_of=earlier)
    return aggregate(results, previous=previous, as_of=now), results
