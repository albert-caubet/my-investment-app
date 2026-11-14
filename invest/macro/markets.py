"""The market charts of the Macro page: ``config/markets.toml`` and the arithmetic they plot.

Most charts overlay several indices (or the ETFs standing in for them), every
one rebased to its own first close in the time range, so the lines compare as
% change from a common start whatever currency or level each is quoted in. The
refresh job downloads their symbols into the prices table.

A yield chart (``kind = "yield"``) draws bond yields themselves, in percent:
its lines are catalog series (``config/series.toml``), which the refresh
fetches with the rest of the catalog. Nothing here fetches.
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
#: The colours and dashes of ``charts.SERIES_COLORS`` and ``charts.SERIES_DASHES``, which
#: a line may be given by number and name. This module stays free of Streamlit.
N_COLORS = 8
DASHES = ("solid", "dash", "dot")

#: ``change``: each line is the % change of a Yahoo symbol's closes. ``yield``: each line is
#: a catalog series of yields, in percent, drawn as it is.
KINDS = ("change", "yield")


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
    series_id: str | None = None  # a yield chart's line: its id in config/series.toml
    color: int | None = None  # 1 to 8, the place of its colour in the palette; None: by its place
    dash: str | None = None  # one of DASHES; None: solid with a colour set, else by its place

    @property
    def legend(self) -> str:
        """The legend name: a proxy is starred, and described under the chart."""
        return f"{self.label}*" if self.proxy else self.label


@dataclass(frozen=True)
class MarketChart:
    id: str
    title: str
    series: tuple[MarketSeries, ...]
    kind: str = "change"

    @property
    def charted(self) -> tuple[MarketSeries, ...]:
        """The lines drawn: the series with a Yahoo symbol, or in a yield chart with a catalog series."""
        if self.kind == "yield":
            return tuple(s for s in self.series if s.series_id)
        return tuple(s for s in self.series if s.symbol)

    @property
    def left_out(self) -> tuple[MarketSeries, ...]:
        """The series with nothing to draw, whose note says why."""
        charted = self.charted
        return tuple(s for s in self.series if s not in charted)

    @property
    def symbols(self) -> tuple[str, ...]:
        """The Yahoo symbols the refresh downloads for this chart; none for a yield chart."""
        return tuple(s.symbol for s in self.charted if s.symbol)

    @property
    def series_ids(self) -> tuple[str, ...]:
        """The catalog series a yield chart draws."""
        return tuple(s.series_id for s in self.charted if s.series_id)

    def styles(self) -> tuple[tuple[int, str], ...]:
        """Each line's colour (0 to 7) and dash, in the order of :attr:`charted`.

        As the file sets them, or else by the line's place: the eight colours in turn, then
        again with the next dash. A line keeps its look when another has no data to draw.
        """
        return tuple(
            (s.color - 1 if s.color else i % N_COLORS,
             s.dash or (DASHES[0] if s.color else DASHES[(i // N_COLORS) % len(DASHES)]))
            for i, s in enumerate(self.charted)
        )


def _series(raw: dict, chart_id: str, kind: str) -> MarketSeries:
    label = str(raw.get("label", "")).strip()
    if not label:
        raise MarketsError(f"{chart_id}: a series has no label")
    symbol = str(raw.get("symbol", "")).strip() or None
    series_id = str(raw.get("series_id", "")).strip() or None
    note = str(raw.get("note", "")).strip()
    if kind == "yield":
        if symbol:
            raise MarketsError(f"{chart_id}: {label} has a symbol; a yield chart's lines are catalog series (series_id)")
        if series_id is None and not note:
            raise MarketsError(f"{chart_id}: {label} has no series_id and no note saying why")
    else:
        if series_id:
            raise MarketsError(f"{chart_id}: {label} has a series_id; only a yield chart's lines are catalog series")
        if symbol is None and not note:
            raise MarketsError(f"{chart_id}: {label} has no symbol and no note saying why")
    color = raw.get("color")
    if color is not None and (isinstance(color, bool) or not isinstance(color, int) or not 1 <= color <= N_COLORS):
        raise MarketsError(f"{chart_id}: {label}: color must be a number from 1 to {N_COLORS}, not {color!r}")
    dash = str(raw.get("dash", "")).strip() or None
    if dash is not None and dash not in DASHES:
        raise MarketsError(f"{chart_id}: {label}: dash must be one of {', '.join(DASHES)}, not {dash!r}")
    return MarketSeries(
        label=label,
        symbol=symbol,
        currency=str(raw.get("currency", "")).strip(),
        about=str(raw.get("about", "")).strip(),
        proxy=str(raw.get("proxy", "")).strip(),
        note=note,
        series_id=series_id,
        color=color,
        dash=dash,
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
        kind = str(raw.get("kind", KINDS[0])).strip()
        if kind not in KINDS:
            raise MarketsError(f"{chart_id}: unknown kind {kind!r}; one of {', '.join(KINDS)}")
        series = tuple(_series(s, chart_id, kind) for s in raw.get("series", ()))
        chart = MarketChart(chart_id, str(raw.get("title", chart_id)), series, kind)
        what = "catalog series" if kind == "yield" else "symbol"
        n_lines = len(chart.charted)
        if not n_lines:
            raise MarketsError(f"{chart_id}: no series with a {what}")
        if n_lines > MAX_LINES:
            raise MarketsError(f"{chart_id}: {n_lines} series with a {what}; a chart tells at most {MAX_LINES} apart")
        labels = [s.label for s in series]
        dupes = sorted({l for l in labels if labels.count(l) > 1})
        if dupes:
            raise MarketsError(f"{chart_id}: duplicate labels {dupes}")
        for name, keys in (("symbols", chart.symbols), ("series ids", chart.series_ids)):
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            if dupes:
                raise MarketsError(f"{chart_id}: duplicate {name} {dupes}")
        styles = chart.styles()
        alike = [s.label for s, style in zip(chart.charted, styles) if styles.count(style) > 1]
        if alike:
            raise MarketsError(f"{chart_id}: {', '.join(alike)} would be drawn alike; give each its own color or dash")
        charts.append(chart)
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


def month_steps(monthly: pd.Series, until: date | None = None) -> pd.Series:
    """A monthly series, dated on the first of each month, on every weekday of its month.

    A monthly average describes the whole month. Put on each weekday of it, its line is
    a step a month that sits under the daily yields beside it, rather than a point on
    the 1st, half a month early, and a range of a few weeks still shows the latest
    month. The last month runs to its end, or to ``until`` if that comes first. A month
    without a value stays empty: nothing is carried over from the month before.
    """
    if monthly.empty:
        return monthly
    by_month = pd.Series(monthly.to_numpy(), index=monthly.index.to_period("M"))
    by_month = by_month[~by_month.index.duplicated(keep="last")]
    end = (by_month.index[-1] + 1).to_timestamp() - pd.Timedelta(days=1)
    if until is not None:
        end = min(end, pd.Timestamp(until))
    days = pd.bdate_range(by_month.index[0].to_timestamp(), end)
    spread = pd.Series(by_month.reindex(days.to_period("M")).to_numpy(), index=days, name=monthly.name)
    return spread.dropna()
