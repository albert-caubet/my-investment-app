"""Walk-forward evaluation of the regime rules against NBER recessions. Pure.

A rule is evaluated as it would have been at each month end, on series shifted
forward by their publication lag, so nothing is known before it was published.
A "signal" is the first month of a run of firing months. It is a hit when a
recession starts within the lead window after it and a false alarm otherwise;
a recession is "warned" when some signal preceded its start within the window.
Both counts print their n, and with about eight recessions since 1970 the n is
the most important number on the table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd

from invest.macro import regimes

DEFAULT_LEAD_MONTHS = 12


def apply_publication_lags(series: Mapping[str, pd.Series], lag_days: Mapping[str, int]) -> dict[str, pd.Series]:
    """Shift each series forward by its lag, so the index is when the value became known."""
    out = {}
    for sid, s in series.items():
        lag = int(lag_days.get(sid, 0))
        shifted = s.copy()
        shifted.index = shifted.index + pd.Timedelta(days=lag)
        out[sid] = shifted
    return out


def recession_starts(usrec: pd.Series) -> list[pd.Timestamp]:
    s = usrec.dropna().sort_index()
    starts = []
    previous = 0.0
    for stamp, value in s.items():
        if value >= 0.5 and previous < 0.5:
            starts.append(stamp)
        previous = value
    return starts


def in_recession(usrec: pd.Series, when: pd.Timestamp) -> bool:
    s = usrec.dropna().sort_index()
    pos = s.index.searchsorted(when, side="right") - 1
    return bool(pos >= 0 and s.iloc[pos] >= 0.5)


def rule_history(series: Mapping[str, pd.Series], rule_name: str, dates: Sequence[pd.Timestamp]) -> pd.Series:
    """Whether one rule fired as of each date (``None`` where inputs were missing)."""
    fn = dict((rule.name, f) for rule, f in regimes.RULES)[rule_name]
    out = {}
    for when in dates:
        try:
            fired, _, _, _ = fn(series, pd.Timestamp(when).date())
        except Exception:
            fired = None
        out[pd.Timestamp(when)] = fired
    return pd.Series(out, dtype=object)


def signal_starts(fired: pd.Series) -> list[pd.Timestamp]:
    """First month of each run of firing months."""
    starts = []
    previous = False
    for stamp, value in fired.items():
        now = bool(value) if value is not None else False
        if now and not previous:
            starts.append(stamp)
        previous = now
    return starts


@dataclass(frozen=True)
class HitRate:
    rule: str
    n_months: int
    n_signals: int
    hits: int
    false_alarms: int
    hit_rate: float | None
    recessions: int
    recessions_warned: int
    warned_rate: float | None
    recessions_confirmed: int      # the rule fired within the first months of the recession
    confirmed_rate: float | None
    mean_lead_months: float | None
    first_date: str
    last_date: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


CONFIRM_MONTHS = 3


def hit_rates(fired: pd.Series, usrec: pd.Series, *, rule: str, lead_months: int = DEFAULT_LEAD_MONTHS) -> HitRate:
    starts = recession_starts(usrec)
    signals = [s for s in signal_starts(fired) if not in_recession(usrec, s)]  # a signal inside a recession warns of nothing
    evaluated = fired.dropna()
    hits, leads = 0, []
    for signal in signals:
        window_end = signal + pd.DateOffset(months=lead_months)
        following = [r for r in starts if signal < r <= window_end]
        if following:
            hits += 1
            leads.append((following[0].year - signal.year) * 12 + following[0].month - signal.month)
    sample_starts = [r for r in starts if len(evaluated) and evaluated.index[0] <= r <= evaluated.index[-1] + pd.DateOffset(months=lead_months)]
    warned, confirmed = 0, 0
    firing_months = [stamp for stamp, value in fired.items() if value]
    for start in sample_starts:
        if any(start - pd.DateOffset(months=lead_months) <= s < start for s in signals):
            warned += 1
        # coincident rules (Sahm, CFNAI) confirm rather than warn: did the rule fire in the first months?
        if any(start <= m <= start + pd.DateOffset(months=CONFIRM_MONTHS) for m in firing_months):
            confirmed += 1
    n_signals = len(signals)
    return HitRate(
        rule=rule,
        n_months=int(len(evaluated)),
        n_signals=n_signals,
        hits=hits,
        false_alarms=n_signals - hits,
        hit_rate=(hits / n_signals) if n_signals else None,
        recessions=len(sample_starts),
        recessions_warned=warned,
        warned_rate=(warned / len(sample_starts)) if sample_starts else None,
        recessions_confirmed=confirmed,
        confirmed_rate=(confirmed / len(sample_starts)) if sample_starts else None,
        mean_lead_months=(sum(leads) / len(leads)) if leads else None,
        first_date=evaluated.index[0].date().isoformat() if len(evaluated) else "",
        last_date=evaluated.index[-1].date().isoformat() if len(evaluated) else "",
    )


def month_ends(start, end) -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="ME")


def scoreboard(series: Mapping[str, pd.Series], usrec: pd.Series, *, lag_days: Mapping[str, int] | None = None,
               rules: Sequence[str] | None = None, start=None, end=None, lead_months: int = DEFAULT_LEAD_MONTHS) -> pd.DataFrame:
    """One row per rule: signals, hits, false alarms, recessions warned, mean lead. Publication lags applied."""
    lagged = apply_publication_lags(series, lag_days or {})
    first = min((s.index[0] for s in lagged.values() if len(s)), default=None)
    last = max((s.index[-1] for s in lagged.values() if len(s)), default=None)
    if first is None:
        return pd.DataFrame()
    dates = month_ends(start or first, end or last)
    names = list(rules) if rules else [rule.name for rule, _ in regimes.RULES]
    rows = []
    for name in names:
        fired = rule_history(lagged, name, dates)
        rows.append(hit_rates(fired, usrec, rule=name, lead_months=lead_months).as_dict())
    return pd.DataFrame(rows)


def expanding_splits(index: pd.DatetimeIndex, *, min_train_years: int = 10, step_months: int = 12) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """(train_end, test_end) pairs for an expanding window: fit on everything before train_end, test until test_end."""
    if len(index) == 0:
        return []
    splits = []
    train_end = index[0] + pd.DateOffset(years=min_train_years)
    while train_end < index[-1]:
        test_end = min(train_end + pd.DateOffset(months=step_months), index[-1])
        splits.append((train_end, test_end))
        train_end = test_end
    return splits
