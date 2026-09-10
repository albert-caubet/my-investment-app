"""Event studies: forward return distributions after dated events. Pure.

Horizons are calendar days so daily and monthly series behave alike. Events
closer together than the horizon are collapsed to the first of the cluster,
because overlapping windows count the same market move several times. Means
carry a bootstrap confidence interval and every statistic prints its n.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd

DEFAULT_HORIZONS = (30, 91, 182, 365)
BOOTSTRAP = 2000


@dataclass(frozen=True)
class ForwardStats:
    horizon_days: int
    n: int
    mean: float | None
    median: float | None
    hit_rate: float | None      # share of positive forward returns
    ci_low: float | None        # bootstrap 95% interval of the mean
    ci_high: float | None
    p10: float | None
    p90: float | None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class EventStudy:
    n_events: int
    n_used: int
    events: tuple[pd.Timestamp, ...]
    horizons: dict[int, ForwardStats]
    baseline: dict[int, ForwardStats]

    def as_dict(self) -> dict:
        return {
            "n_events": self.n_events,
            "n_used": self.n_used,
            "events": [e.date().isoformat() for e in self.events],
            "horizons": {h: s.as_dict() for h, s in self.horizons.items()},
            "baseline": {h: s.as_dict() for h, s in self.baseline.items()},
        }


def price_at_or_after(prices: pd.Series, when: pd.Timestamp) -> tuple[float, pd.Timestamp] | None:
    pos = prices.index.searchsorted(when, side="left")
    if pos >= len(prices):
        return None
    return float(prices.iloc[pos]), prices.index[pos]


def forward_returns(prices: pd.Series, dates: Sequence[pd.Timestamp], horizon_days: int) -> pd.Series:
    """Simple return from the first price at or after each date to the first price at or after date + horizon."""
    prices = prices.dropna().sort_index()
    out = {}
    for when in dates:
        start = price_at_or_after(prices, pd.Timestamp(when))
        end = price_at_or_after(prices, pd.Timestamp(when) + pd.Timedelta(days=horizon_days))
        if start is None or end is None:
            continue
        if (end[1] - pd.Timestamp(when)).days > horizon_days + 45:
            continue  # the horizon runs past the data
        out[pd.Timestamp(when)] = end[0] / start[0] - 1.0
    return pd.Series(out, dtype=float)


def dedupe_events(dates: Sequence[pd.Timestamp], min_gap_days: int) -> list[pd.Timestamp]:
    """Keep the first event of every cluster closer than ``min_gap_days``."""
    kept: list[pd.Timestamp] = []
    for when in sorted(pd.Timestamp(d) for d in dates):
        if not kept or (when - kept[-1]).days >= min_gap_days:
            kept.append(when)
    return kept


def summarise(returns: pd.Series, horizon_days: int, *, seed: int = 0, bootstrap: int = BOOTSTRAP) -> ForwardStats:
    values = returns.dropna().to_numpy(dtype=float)
    n = int(len(values))
    if n == 0:
        return ForwardStats(horizon_days, 0, None, None, None, None, None, None, None)
    rng = np.random.default_rng(seed)
    if n >= 3:
        means = rng.choice(values, size=(bootstrap, n), replace=True).mean(axis=1)
        ci_low, ci_high = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    else:
        ci_low = ci_high = None
    return ForwardStats(
        horizon_days, n, float(values.mean()), float(np.median(values)), float((values > 0).mean()),
        ci_low, ci_high, float(np.percentile(values, 10)), float(np.percentile(values, 90)),
    )


def baseline_stats(prices: pd.Series, horizon_days: int, *, every_days: int = 30, seed: int = 0) -> ForwardStats:
    """Unconditional forward returns sampled every ``every_days``, for comparison with the events."""
    prices = prices.dropna().sort_index()
    if len(prices) < 2:
        return summarise(pd.Series(dtype=float), horizon_days)
    dates = pd.date_range(prices.index[0], prices.index[-1], freq=f"{every_days}D")
    return summarise(forward_returns(prices, dates, horizon_days), horizon_days, seed=seed)


def event_study(prices: pd.Series, dates: Sequence[pd.Timestamp], *, horizons: Sequence[int] = DEFAULT_HORIZONS,
                min_gap_days: int | None = None, seed: int = 0) -> EventStudy:
    """Forward returns after the events at each horizon, against an unconditional baseline."""
    gap = max(horizons) if min_gap_days is None else min_gap_days
    used = dedupe_events(dates, gap)
    stats = {h: summarise(forward_returns(prices, used, h), h, seed=seed) for h in horizons}
    base = {h: baseline_stats(prices, h, seed=seed) for h in horizons}
    return EventStudy(len(list(dates)), len(used), tuple(used), stats, base)


def crossing_events(series: pd.Series, *, above: float | None = None, below: float | None = None) -> list[pd.Timestamp]:
    """Dates when the series first crosses a level (from below for ``above``, from above for ``below``)."""
    s = series.dropna().sort_index()
    events: list[pd.Timestamp] = []
    previous = None
    for stamp, value in s.items():
        if previous is not None:
            if above is not None and previous <= above < value:
                events.append(stamp)
            if below is not None and previous >= below > value:
                events.append(stamp)
        previous = value
    return events


def rolling_zscore(series: pd.Series, *, window_years: int = 10, min_obs: int = 60) -> pd.Series:
    """z of each value against the trailing window ending at it (the value included), for spike detection."""
    s = series.dropna().sort_index()
    window = f"{365 * window_years}D"
    mean = s.rolling(window, min_periods=min_obs).mean()
    std = s.rolling(window, min_periods=min_obs).std(ddof=1)
    return ((s - mean) / std).dropna()


def spike_events(series: pd.Series, *, z: float = 2.0, window_years: int = 10) -> list[pd.Timestamp]:
    return crossing_events(rolling_zscore(series, window_years=window_years), above=z)


def uninversion_events(spread: pd.Series, *, min_inverted_months: int = 3) -> list[pd.Timestamp]:
    """Months when a spread turns positive after at least ``min_inverted_months`` negative months."""
    monthly = spread.dropna().resample("MS").mean().dropna()
    events = []
    run = 0
    for stamp, value in monthly.items():
        if value < 0:
            run += 1
        else:
            if run >= min_inverted_months:
                events.append(stamp)
            run = 0
    return events
