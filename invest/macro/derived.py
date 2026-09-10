"""Derived series: pure functions from stored series to a new series.

Each formula named in ``config/series.toml`` (``source = "derived"``) maps to a
function here that takes the input series in catalog order and returns one
series. Nothing is fetched and nothing is filled: where an input is missing the
output is missing on that date too.

Conventions:

- Inputs and outputs are ``pd.Series`` on a ``DatetimeIndex``.
- Rates are in percent, like FRED (``4.80`` is 4.80%). Ratios are plain numbers.
- Growth rates are returned in percent.
"""

from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

Formula = Callable[..., pd.Series]

_MONTH_PERIODS = {"D": 21, "W": 4.345, "M": 1, "Q": 1 / 3, "A": 1 / 12}


def _clean(series: pd.Series) -> pd.Series:
    out = pd.to_numeric(series, errors="coerce").astype(float).dropna()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def infer_frequency(series: pd.Series) -> str:
    """``D``, ``W``, ``M``, ``Q`` or ``A`` from the median spacing of the index."""
    if len(series) < 3:
        return "M"
    days = float(np.median(np.diff(series.index.values).astype("timedelta64[D]").astype(float)))
    if days <= 3:
        return "D"
    if days <= 10:
        return "W"
    if days <= 45:
        return "M"
    if days <= 120:
        return "Q"
    return "A"


def align(*series: pd.Series, how: str = "inner") -> pd.DataFrame:
    """Column-wise alignment of several series on one calendar.

    ``how="ffill"`` puts every input on the union of dates and forward-fills the
    slower ones, so a monthly level can be combined with a daily one. It is the
    only place a value is carried forward, and only across the reporting gap of
    a slower series -- never to invent an observation that does not exist yet.
    """
    frame = pd.concat([_clean(s).rename(i) for i, s in enumerate(series)], axis=1, sort=True)
    if how == "ffill":
        return frame.sort_index().ffill().dropna()
    return frame.dropna()


# ---------------------------------------------------------------------------
# transforms (also used by the scorecard)
# ---------------------------------------------------------------------------

def shift_by_months(series: pd.Series, months: int) -> pd.Series:
    """The value ``months`` earlier, by calendar, for irregular or daily series."""
    s = _clean(series)
    if s.empty:
        return s
    target = s.index - pd.DateOffset(months=months)
    # asof: latest observation at or before the shifted date
    positions = s.index.searchsorted(target, side="right") - 1
    values = np.where(positions >= 0, s.to_numpy()[np.clip(positions, 0, None)], np.nan)
    # an observation more than half a period stale is not the value "a year ago"
    out = pd.Series(values, index=s.index)
    return out


def yoy(series: pd.Series) -> pd.Series:
    """Percent change against the value twelve months earlier."""
    s = _clean(series)
    freq = infer_frequency(s)
    if freq in ("M", "Q", "A"):
        periods = {"M": 12, "Q": 4, "A": 1}[freq]
        return (s / s.shift(periods) - 1.0) * 100.0
    earlier = shift_by_months(s, 12)
    return (s / earlier - 1.0) * 100.0


def mom(series: pd.Series) -> pd.Series:
    s = _clean(series)
    return (s / s.shift(1) - 1.0) * 100.0


def ann3m(series: pd.Series) -> pd.Series:
    """Three-month change, annualised, in percent. Monthly series only."""
    s = _clean(series)
    return ((s / s.shift(3)) ** 4 - 1.0) * 100.0


def diff_months(series: pd.Series, months: int) -> pd.Series:
    """Difference against the value ``months`` earlier, in the series' own units."""
    s = _clean(series)
    freq = infer_frequency(s)
    if freq == "M":
        return s - s.shift(months)
    if freq == "Q" and months % 3 == 0:
        return s - s.shift(months // 3)
    return s - shift_by_months(s, months)


def diff3m(series: pd.Series) -> pd.Series:
    return diff_months(series, 3)


def diff12m(series: pd.Series) -> pd.Series:
    return diff_months(series, 12)


def ma4w(series: pd.Series) -> pd.Series:
    """Four-observation mean of a weekly series (the claims convention)."""
    return _clean(series).rolling(4).mean()


def ma3m(series: pd.Series) -> pd.Series:
    return _clean(series).rolling(3).mean()


def pct_of_max_12m(series: pd.Series) -> pd.Series:
    s = _clean(series)
    return s / s.rolling(12, min_periods=6).max() * 100.0


TRANSFORMS: dict[str, Formula] = {
    "level": lambda s: _clean(s),
    "yoy": yoy,
    "mom": mom,
    "ann3m": ann3m,
    "diff3m": diff3m,
    "diff12m": diff12m,
    "ma4w": ma4w,
    "ma3m": ma3m,
}


def apply_transform(series: pd.Series, name: str) -> pd.Series:
    try:
        fn = TRANSFORMS[name]
    except KeyError:
        raise KeyError(f"unknown transform {name!r}; known: {sorted(TRANSFORMS)}")
    return fn(series).dropna()


# ---------------------------------------------------------------------------
# formulas
# ---------------------------------------------------------------------------

def spread(a: pd.Series, b: pd.Series) -> pd.Series:
    """``a - b`` on common dates (slower series forward-filled onto the faster)."""
    f = align(a, b, how="ffill")
    return f[0] - f[1]


def ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    f = align(a, b, how="ffill")
    return f[0] / f[1]


def ratio_pct(a: pd.Series, b: pd.Series) -> pd.Series:
    return ratio(a, b) * 100.0


def sahm(unrate: pd.Series) -> pd.Series:
    """Sahm rule: 3-month average unemployment minus its low over the prior 12 months.

    The low is taken over the twelve *preceding* months of the 3-month average,
    matching the published real-time definition.
    """
    u = _clean(unrate)
    avg3 = u.rolling(3).mean()
    low12 = avg3.shift(1).rolling(12, min_periods=12).min()
    return avg3 - low12


def claims_off_low(claims_4wk: pd.Series, weeks: int = 52) -> pd.Series:
    """Percent the 4-week average sits above its low over the trailing year."""
    c = _clean(claims_4wk)
    low = c.rolling(weeks, min_periods=26).min()
    return (c / low - 1.0) * 100.0


def payrolls_3m_change(payems: pd.Series) -> pd.Series:
    """Average monthly change over the last three months, in thousands."""
    p = _clean(payems)
    return p.diff().rolling(3).mean()


def net_liquidity(walcl: pd.Series, tga: pd.Series, rrp: pd.Series) -> pd.Series:
    """Fed balance sheet minus the Treasury account minus reverse repo, in $bn, weekly.

    WALCL and WTREGEN are published in millions, RRPONTSYD in billions. Aligned
    on the Wednesday balance-sheet date; RRP is the last daily print at or
    before it.
    """
    w = _clean(walcl) / 1000.0
    t = _clean(tga) / 1000.0
    r = _clean(rrp)
    frame = pd.concat([w.rename("w"), t.rename("t"), r.rename("r")], axis=1, sort=True).ffill()
    frame = frame.loc[w.index].dropna()
    return frame["w"] - frame["t"] - frame["r"]


def change_over_weeks(series: pd.Series, weeks: int = 13) -> pd.Series:
    s = _clean(series)
    return s - s.shift(int(weeks))


def real_rate(nominal: pd.Series, inflation_yoy: pd.Series) -> pd.Series:
    """Fisher real rate in percent: ``(1 + n) / (1 + i) - 1``, both in percent."""
    f = align(nominal, inflation_yoy, how="ffill")
    return ((1.0 + f[0] / 100.0) / (1.0 + f[1] / 100.0) - 1.0) * 100.0


def real_rate_from_index(nominal: pd.Series, price_index: pd.Series) -> pd.Series:
    """Real rate against the year-on-year change of a price index (Fisher)."""
    return real_rate(nominal, yoy(price_index))


def policy_stance(fedfunds: pd.Series, price_index: pd.Series, rstar: pd.Series) -> pd.Series:
    """Real policy rate minus r*, in percentage points. Positive is restrictive.

    Real policy rate is the Fisher real Fed funds rate against core PCE inflation
    over the past year; r* is the Holston-Laubach-Williams estimate, quarterly,
    carried forward within the quarter.
    """
    real = real_rate_from_index(fedfunds, price_index)
    f = align(real, rstar, how="ffill")
    return f[0] - f[1]


def real_growth(nominal_yoy: pd.Series, inflation_yoy: pd.Series) -> pd.Series:
    """Real growth from nominal growth and inflation, Fisher form, in percent."""
    return real_rate(nominal_yoy, inflation_yoy)


def deflated_yoy(nominal_level: pd.Series, price_index: pd.Series) -> pd.Series:
    """Year-on-year growth of a nominal level after deflating by a price index."""
    return real_growth(yoy(nominal_level), yoy(price_index))


def real_earnings_growth(earnings: pd.Series, cpi: pd.Series) -> pd.Series:
    """Real growth of trailing earnings, Fisher form, in percent."""
    return real_growth(yoy(earnings), yoy(cpi))


def buffett(corp_equities_millions: pd.Series, gdp_billions: pd.Series) -> pd.Series:
    """Corporate equities (Z.1, $ millions) over nominal GDP ($ billions), as a ratio."""
    f = align(corp_equities_millions / 1000.0, gdp_billions, how="inner")
    return f[0] / f[1]


def trend_deviation(series: pd.Series) -> pd.Series:
    """Percent deviation from a log-linear trend fitted over the whole history.

    Used for the Buffett indicator, which drifts upward structurally (foreign
    revenue, lower rates), so its level says less than its distance from trend.
    Fitted once on all data, so early values look at a trend they helped shape;
    that is acceptable for a picture and unacceptable for a backtest, which is
    why the research code refits walk-forward.
    """
    s = _clean(series)
    s = s[s > 0]
    if len(s) < 8:
        return pd.Series(dtype=float)
    x = (s.index - s.index[0]).days / 365.25
    coef = np.polyfit(x, np.log(s.to_numpy()), 1)
    trend = np.exp(np.polyval(coef, x))
    return (s / trend - 1.0) * 100.0


def excess_cape_yield(cape: pd.Series, gs10: pd.Series, cpi: pd.Series) -> pd.Series:
    """1 / CAPE minus the real long rate (nominal 10y minus trailing 10-year inflation), in percent."""
    f = align(cape, gs10, cpi, how="inner")
    infl10 = ((f[2] / f[2].shift(120)) ** (1 / 10) - 1.0) * 100.0
    return (100.0 / f[0]) - (f[1] - infl10)


def pct_vs_ma(price: pd.Series, window: int = 200) -> pd.Series:
    """Percent distance of price from its ``window``-day simple moving average."""
    p = _clean(price)
    ma = p.rolling(window).mean()
    return (p / ma - 1.0) * 100.0


def ma_slope(price: pd.Series, window: int = 200, lookback: int = 20) -> pd.Series:
    """Percent change of the moving average over ``lookback`` observations. Negative is falling."""
    p = _clean(price)
    ma = p.rolling(window).mean()
    return (ma / ma.shift(lookback) - 1.0) * 100.0


def bollinger_pct_b(price: pd.Series, window: int = 20, k: float = 2.0) -> pd.Series:
    """%B = (price - lower) / (upper - lower), bands at the mean +/- k sigma (ddof=0)."""
    p = _clean(price)
    mid = p.rolling(window).mean()
    sd = p.rolling(window).std(ddof=0)
    lower, upper = mid - k * sd, mid + k * sd
    return (p - lower) / (upper - lower)


def bollinger_bandwidth(price: pd.Series, window: int = 20, k: float = 2.0) -> pd.Series:
    p = _clean(price)
    mid = p.rolling(window).mean()
    sd = p.rolling(window).std(ddof=0)
    return (2.0 * k * sd) / mid


def realised_vol(price: pd.Series, window: int = 20, periods_per_year: int = 252) -> pd.Series:
    """Annualised standard deviation of log returns over ``window`` days, in percent."""
    p = _clean(price)
    returns = np.log(p / p.shift(1))
    return returns.rolling(window).std(ddof=1) * math.sqrt(periods_per_year) * 100.0


def drawdown(price: pd.Series) -> pd.Series:
    """Percent below the running high (zero at a new high, negative otherwise)."""
    p = _clean(price)
    return (p / p.cummax() - 1.0) * 100.0


def relative_strength(a: pd.Series, b: pd.Series, lookback: int = 63) -> pd.Series:
    """Percent change of the ``a / b`` ratio over ``lookback`` observations."""
    r = ratio(a, b)
    return (r / r.shift(lookback) - 1.0) * 100.0


def mean_available(*series: pd.Series, min_count: int = 2) -> pd.Series:
    """Average of whatever inputs exist on each date, if at least ``min_count`` do.

    Used for the regional Fed surveys, where the count of contributors is part
    of the reading and is printed alongside it.
    """
    frame = pd.concat([_clean(s).rename(i) for i, s in enumerate(series)], axis=1, sort=True)
    count = frame.notna().sum(axis=1)
    return frame.mean(axis=1)[count >= min_count]


def interest_to_receipts(interest: pd.Series, receipts: pd.Series) -> pd.Series:
    return ratio_pct(interest, receipts)


def multiply(a: pd.Series, factor: float) -> pd.Series:
    return _clean(a) * float(factor)


FORMULAS: dict[str, Formula] = {
    "spread": spread,
    "ratio": ratio,
    "ratio_pct": ratio_pct,
    "yoy": yoy,
    "mom": mom,
    "ann3m": ann3m,
    "diff3m": diff3m,
    "diff12m": diff12m,
    "ma4w": ma4w,
    "ma3m": ma3m,
    "sahm": sahm,
    "claims_off_low": claims_off_low,
    "payrolls_3m_change": payrolls_3m_change,
    "net_liquidity": net_liquidity,
    "change_13w": lambda s: change_over_weeks(s, 13),
    "real_rate": real_rate,
    "real_rate_from_index": real_rate_from_index,
    "policy_stance": policy_stance,
    "real_growth": real_growth,
    "deflated_yoy": deflated_yoy,
    "real_earnings_growth": real_earnings_growth,
    "buffett": buffett,
    "trend_deviation": trend_deviation,
    "excess_cape_yield": excess_cape_yield,
    "pct_vs_ma200": lambda p: pct_vs_ma(p, 200),
    "ma200_slope": lambda p: ma_slope(p, 200, 20),
    "bollinger_pct_b": bollinger_pct_b,
    "bollinger_bandwidth": bollinger_bandwidth,
    "realised_vol_20d": lambda p: realised_vol(p, 20),
    "drawdown": drawdown,
    "relative_strength_3m": lambda a, b: relative_strength(a, b, 63),
    "mean_available": mean_available,
    "interest_to_receipts": interest_to_receipts,
}


#: Formulas that work on whatever inputs exist. Every other formula needs all of them.
OPTIONAL_INPUT_FORMULAS = frozenset({"mean_available"})
MIN_OPTIONAL_INPUTS = 2


def compute(formula: str, inputs: list[pd.Series]) -> pd.Series:
    """Apply a named formula to its inputs; the result is cleaned of NaN and inf."""
    try:
        fn = FORMULAS[formula]
    except KeyError:
        raise KeyError(f"unknown formula {formula!r}; known: {sorted(FORMULAS)}")
    out = fn(*inputs)
    out = pd.to_numeric(out, errors="coerce").astype(float)
    return out.replace([np.inf, -np.inf], np.nan).dropna()
