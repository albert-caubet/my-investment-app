"""The indicator contract: one reading per series, every number dated.

``indicator(series, spec)`` turns a stored series into an :class:`IndicatorReading`:
latest value after the catalog transform, its observation date, a z-score over a
trailing window, the percentile within the whole history, the three-month change,
the direction of concern and a one-sentence reading. Pure: no store, no clock
unless ``today`` is passed, no network.

Conventions: z-scores use ``ddof=1`` and need at least :data:`MIN_Z_OBS`
observations in the window; percentiles are mid-ranks in percent; a reading with
too little history says so instead of printing a number.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd

from invest.macro import derived
from invest.macro.catalog import SeriesSpec

MIN_Z_OBS = 12
MIN_PCT_OBS = 24


@dataclass(frozen=True)
class IndicatorReading:
    id: str
    label: str
    group: str
    source: str
    key: str | None
    units: str
    transform: str
    direction: str
    value: float | None
    obs_date: date | None
    age_days: int | None
    stale: bool
    z: float | None
    z_window_years: int
    z_n: int
    percentile: float | None
    n_history: int
    first_obs: date | None
    change_3m: float | None
    concern: float | None
    reading: str
    note: str = ""

    @property
    def value_text(self) -> str:
        return format_value(self.value, self.units)

    def as_dict(self) -> dict:
        out = asdict(self)
        for key in ("obs_date", "first_obs"):
            out[key] = out[key].isoformat() if out[key] else None
        return out


def format_value(value: float | None, units: str) -> str:
    """Human formatting by unit. Never invents precision: index-like values get two decimals."""
    if value is None or not math.isfinite(value):
        return "–"
    if units == "%":
        return f"{value:.2f}%"
    if units == "pp":
        return f"{value:+.2f} pp"
    if units == "prob":
        return f"{value * 100:.0f}%"
    if units == "frac":
        return f"{value * 100:.2f}%"
    if units in ("persons", "thousands", "contracts", "USD mn", "USD bn", "EUR"):
        return f"{value:,.0f}"
    if units == "x":
        return f"{value:.2f}x"
    if units == "USD/bbl":
        return f"${value:.2f}"
    if units == "0/1":
        return "yes" if value >= 0.5 else "no"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.2f}"


def truncate(series: pd.Series, as_of: date | None) -> pd.Series:
    if as_of is None or series.empty:
        return series
    return series[series.index <= pd.Timestamp(as_of)]


def zscore(series: pd.Series, *, window_years: int = 10, min_obs: int = MIN_Z_OBS) -> tuple[float | None, int]:
    """z of the latest value against the trailing ``window_years`` (latest included).

    Returns ``(z, n)``; ``z`` is ``None`` when the window holds fewer than
    ``min_obs`` observations or has no dispersion.
    """
    s = series.dropna()
    if s.empty:
        return None, 0
    end = s.index[-1]
    window = s[s.index > end - pd.DateOffset(years=window_years)]
    n = int(len(window))
    if n < min_obs:
        return None, n
    sd = float(window.std(ddof=1))
    if not math.isfinite(sd) or sd < 1e-12:
        return None, n
    return float((s.iloc[-1] - window.mean()) / sd), n


def percentile(series: pd.Series, *, min_obs: int = MIN_PCT_OBS) -> tuple[float | None, int]:
    """Mid-rank percentile of the latest value within the whole history, in percent."""
    s = series.dropna()
    n = int(len(s))
    if n < min_obs:
        return None, n
    latest = s.iloc[-1]
    values = s.to_numpy()
    below = float(np.sum(values < latest))
    equal = float(np.sum(values == latest))
    return (below + 0.5 * equal) / n * 100.0, n


def change_over_months(series: pd.Series, months: int = 3) -> float | None:
    """Latest value minus the value ``months`` earlier (the last observation at or before that date)."""
    s = series.dropna()
    if len(s) < 2:
        return None
    end = s.index[-1]
    target = end - pd.DateOffset(months=months)
    earlier = s[s.index <= target]
    if earlier.empty:
        return None
    # An earlier point more than one extra period stale is not "three months ago".
    gap_days = (target - earlier.index[-1]).days
    if gap_days > 45:
        return None
    return float(s.iloc[-1] - earlier.iloc[-1])


def concern_score(z: float | None, direction: str) -> float | None:
    """Signed z in the direction of concern: positive means worrying."""
    if z is None or direction == "neutral":
        return None
    return z if direction == "high_bad" else -z


def _ordinal(n: float) -> str:
    n = int(round(n))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def reading_sentence(
    value: float | None,
    obs_date: date | None,
    units: str,
    z: float | None,
    z_window_years: int,
    pct: float | None,
    n_history: int,
    first_obs: date | None,
    change_3m: float | None,
    direction: str,
    stale: bool,
) -> str:
    if value is None or obs_date is None:
        return "No observation available."
    parts = [f"{format_value(value, units)} as of {obs_date.isoformat()}"]
    if z is not None:
        parts.append(f"z {z:+.1f} against the last {z_window_years} years")
    if pct is not None:
        since = f" since {first_obs.year}" if first_obs else ""
        parts.append(f"{_ordinal(pct)} percentile of {n_history} observations{since}")
    if change_3m is not None:
        parts.append(f"{_signed(change_3m, units)} over three months")
    sentence = "; ".join(parts) + "."
    if direction == "high_bad":
        sentence += " Higher is the concern."
    elif direction == "low_bad":
        sentence += " Lower is the concern."
    if stale:
        sentence += " Stale: older than its publication schedule allows."
    return sentence


def _signed(change: float, units: str) -> str:
    if units in ("%", "pp"):
        return f"{change:+.2f} pp"
    if units in ("prob", "frac"):
        return f"{change * 100:+.1f} pts"
    if units in ("persons", "thousands", "contracts", "USD mn", "USD bn", "EUR"):
        return f"{change:+,.0f}"
    return f"{change:+.2f}"


def indicator(
    series: pd.Series,
    spec: SeriesSpec,
    *,
    as_of: date | None = None,
    today: date | None = None,
    stale_after_days: int | None = None,
    note: str = "",
) -> IndicatorReading:
    """The reading for one catalog series.

    ``as_of`` truncates the history (point-in-time by observation date; publication
    lags are the research module's job). ``today`` and ``stale_after_days``
    decide staleness; without them nothing is called stale.
    """
    raw = truncate(series, as_of)
    transformed = derived.apply_transform(raw, spec.transform) if not raw.empty else raw
    transformed = transformed.replace([np.inf, -np.inf], np.nan).dropna()

    if transformed.empty:
        return IndicatorReading(
            id=spec.id, label=spec.label, group=spec.group, source=spec.source, key=spec.key,
            units=spec.units, transform=spec.transform, direction=spec.direction,
            value=None, obs_date=None, age_days=None, stale=False, z=None,
            z_window_years=spec.z_window_years, z_n=0, percentile=None, n_history=0,
            first_obs=None, change_3m=None, concern=None, reading="No observation available.",
            note=note or ("no data" if raw.empty else f"not enough history for transform {spec.transform!r}"),
        )

    value = float(transformed.iloc[-1])
    obs_date = transformed.index[-1].date()
    age_days = (today - obs_date).days if today else None
    stale = bool(stale_after_days is not None and age_days is not None and age_days > stale_after_days)
    z, z_n = zscore(transformed, window_years=spec.z_window_years)
    pct, n_history = percentile(transformed)
    first_obs = transformed.index[0].date()
    change_3m = change_over_months(transformed, 3)
    concern = concern_score(z, spec.direction)
    sentence = reading_sentence(
        value, obs_date, spec.units, z, spec.z_window_years, pct, len(transformed), first_obs,
        change_3m, spec.direction, stale,
    )
    return IndicatorReading(
        id=spec.id, label=spec.label, group=spec.group, source=spec.source, key=spec.key,
        units=spec.units, transform=spec.transform, direction=spec.direction,
        value=value, obs_date=obs_date, age_days=age_days, stale=stale, z=z,
        z_window_years=spec.z_window_years, z_n=z_n, percentile=pct, n_history=int(len(transformed)),
        first_obs=first_obs, change_3m=change_3m, concern=concern, reading=sentence, note=note,
    )


def recession_spans(usrec: pd.Series) -> list[tuple[date, date]]:
    """``(start, end)`` of each run of ones in a 0/1 monthly recession indicator.

    The end is the first day of the last recession month; a chart shades up to
    the end of that month.
    """
    s = usrec.dropna()
    spans: list[tuple[date, date]] = []
    start: pd.Timestamp | None = None
    previous: pd.Timestamp | None = None
    for stamp, value in s.items():
        if value >= 0.5 and start is None:
            start = stamp
        elif value < 0.5 and start is not None:
            spans.append((start.date(), previous.date()))
            start = None
        previous = stamp
    if start is not None and previous is not None:
        spans.append((start.date(), previous.date()))
    return spans


def readings_frame(readings: list[IndicatorReading]) -> pd.DataFrame:
    """Tabular view for pages and reports."""
    rows = []
    for r in readings:
        rows.append(
            {
                "id": r.id,
                "group": r.group,
                "Indicator": r.label,
                "Value": r.value_text,
                "As of": r.obs_date.isoformat() if r.obs_date else "–",
                "z": r.z,
                "Percentile": r.percentile,
                "3m change": r.change_3m,
                "Concern": r.concern,
                "Stale": r.stale,
                "Note": r.note,
            }
        )
    return pd.DataFrame(rows)


def snapshot_time(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return pd.Timestamp(value).to_pydatetime()
