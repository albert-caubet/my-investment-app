"""The market charts of the Macro page: ``config/markets.toml`` and the arithmetic they plot.

Each chart overlays several indices (or the ETFs standing in for them), every
one rebased to its own first close in the time range, so the lines compare as
% change from a common start whatever currency or level each is quoted in. The
refresh job downloads the symbols into the prices table; nothing here fetches.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from invest.paths import CONFIG_DIR

MARKETS_PATH = CONFIG_DIR / "markets.toml"

#: A window longer than this is drawn from weekly closes: a dozen lines of daily
#: closes over five years is more points than a browser draws smoothly, and more
#: detail than a chart a few hundred pixels wide can show.
WEEKLY_AFTER_DAYS = 2 * 366

#: Lines a chart may overlay: eight colours, each in three dashes, tell 24 apart.
MAX_LINES = 24


class MarketsError(ValueError):
    """The markets file is not usable. The message names the chart and the series."""


@dataclass(frozen=True)
class MarketSeries:
    label: str
    symbol: str | None  # None: not charted, ``note`` says why
    currency: str = ""
    about: str = ""  # what the index covers, in a few words: "Greece, 60 largest"
    proxy: str = ""  # what the symbol is, when it stands in for the index
    note: str = ""

    @property
    def legend(self) -> str:
        """The legend name: a proxy is starred, and described under the chart."""
        return f"{self.label}*" if self.proxy else self.label


@dataclass(frozen=True)
class MarketChart:
    id: str
    title: str
    series: tuple[MarketSeries, ...]

    @property
    def charted(self) -> tuple[MarketSeries, ...]:
        return tuple(s for s in self.series if s.symbol)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(s.symbol for s in self.charted)


def _series(raw: dict, chart_id: str) -> MarketSeries:
    label = str(raw.get("label", "")).strip()
    if not label:
        raise MarketsError(f"{chart_id}: a series has no label")
    symbol = str(raw.get("symbol", "")).strip() or None
    note = str(raw.get("note", "")).strip()
    if symbol is None and not note:
        raise MarketsError(f"{chart_id}: {label} has no symbol and no note saying why")
    return MarketSeries(
        label=label,
        symbol=symbol,
        currency=str(raw.get("currency", "")).strip(),
        about=str(raw.get("about", "")).strip(),
        proxy=str(raw.get("proxy", "")).strip(),
        note=note,
    )


def parse_markets(payload: dict) -> tuple[MarketChart, ...]:
    entries = payload.get("chart")
    if not entries:
        raise MarketsError("markets file has no [[chart]] entries")
    charts = []
    for i, raw in enumerate(entries, start=1):
        chart_id = str(raw.get("id", "")).strip()
        if not chart_id:
            raise MarketsError(f"chart #{i} has no id")
        series = tuple(_series(s, chart_id) for s in raw.get("series", ()))
        n_lines = sum(1 for s in series if s.symbol)
        if not n_lines:
            raise MarketsError(f"{chart_id}: no series with a symbol")
        if n_lines > MAX_LINES:
            raise MarketsError(f"{chart_id}: {n_lines} series with a symbol; a chart tells at most {MAX_LINES} apart")
        labels = [s.label for s in series]
        dupes = sorted({l for l in labels if labels.count(l) > 1})
        if dupes:
            raise MarketsError(f"{chart_id}: duplicate labels {dupes}")
        symbols = [s.symbol for s in series if s.symbol]
        dupes = sorted({s for s in symbols if symbols.count(s) > 1})
        if dupes:
            raise MarketsError(f"{chart_id}: duplicate symbols {dupes}")
        charts.append(MarketChart(chart_id, str(raw.get("title", chart_id)), series))
    ids = [c.id for c in charts]
    if len(set(ids)) != len(ids):
        raise MarketsError(f"duplicate chart ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    return tuple(charts)


def load_markets(path: Path = MARKETS_PATH) -> tuple[MarketChart, ...]:
    with Path(path).open("rb") as handle:
        return parse_markets(tomllib.load(handle))


def all_symbols(charts: tuple[MarketChart, ...]) -> tuple[str, ...]:
    """Every symbol to download, once each, in file order."""
    return tuple(dict.fromkeys(symbol for chart in charts for symbol in chart.symbols))


# ---------------------------------------------------------------------------
# What a chart plots
# ---------------------------------------------------------------------------


def weekdays(series: pd.Series) -> pd.Series:
    """The series without Saturday and Sunday rows.

    Yahoo dates its live quote of an MSCI index today, weekend or not: a Sunday
    row repeating Friday's close, which would read as a close made that day.
    Every index and future charted trades Monday to Friday.
    """
    return series[series.index.dayofweek < 5]


def common_start(closes: dict[str, pd.Series]) -> date | None:
    """The latest of the series' first closes: the first date from which every one has data."""
    firsts = [s.index[0] for s in closes.values() if not s.empty]
    return max(firsts).date() if firsts else None


def rebase(closes: pd.Series, start: date, end: date) -> pd.Series:
    """The change since the first close on or after ``start``, to ``end``: 0.1 is +10%."""
    shown = closes[pd.Timestamp(start):pd.Timestamp(end)].dropna()
    if shown.empty:
        return shown
    return shown / shown.iloc[0] - 1


def weekly(series: pd.Series) -> pd.Series:
    """The last observation of each week, on its own date, after the series' first point.

    The first point stays, so a rebased line still starts at 0. Dates are the days
    the closes were made, not the Friday that ends the week: the current week's
    point would otherwise sit on a date that has not happened.
    """
    if len(series) < 2:
        return series
    last_of_week = series.groupby(series.index.to_period("W-FRI")).tail(1)
    return pd.concat([series.iloc[:1], last_of_week]).pipe(lambda s: s[~s.index.duplicated()])
