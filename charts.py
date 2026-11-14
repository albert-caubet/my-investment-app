"""The look every chart in the app shares.

Streamlit's chart theme draws text at 12px (axis numbers, legends, pie and bar labels,
annotations), 14px (axis titles) and 16px (chart titles), which read small, and draws
axis numbers, axis titles and annotations in grey. Every size here is 50% larger, and
all chart text is black. Charts are 50% taller too: a page sets the height a chart
would naturally have (or none, for Streamlit's 450px default) and :func:`readable`
scales it, unless the page lets the viewer pick one of :data:`HEIGHTS`. Pass each
figure through :func:`readable` once, last, right before ``st.plotly_chart``, so it
also reaches annotations added along the way.

The time-range presets a price chart offers live here too, with the arithmetic that
turns a preset into the dates it shows, the controls of a grid of charts, so the
pages that show one offer the same choices, and the figure of a chart of series, so
the Stock explorer and the Watchlist draw theirs alike.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

#: Every chart is this much taller than the height its page asks for.
HEIGHT_SCALE = 1.5
DEFAULT_HEIGHT = 450  # what Streamlit and Plotly use when a figure sets none
#: Heights, as drawn, that a page may let the viewer choose. Medium is a default chart's.
HEIGHTS = {"Small": 350, "Medium": round(DEFAULT_HEIGHT * HEIGHT_SCALE), "Large": 800}

FONT = 18  # 12 * 1.5
AXIS_TITLE_FONT = 21  # 14 * 1.5
TITLE_FONT = 24  # 16 * 1.5
HOVER_FONT = 20  # Plotly's 13 * 1.5, rounded

#: Black on the light theme; white on the dark one, where black would vanish.
TEXT_COLOR = {"light": "#000000", "dark": "#ffffff"}

#: Line colours for a chart of several series, taken in this order and never
#: generated past eight. The order keeps neighbouring colours apart for
#: colour-blind readers; the dark theme has its own steps of the same hues.
SERIES_COLORS = {
    "light": ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"),
    "dark": ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"),
}
#: Past eight series the colours come round again with the next dash, so up to 24
#: lines each look different. The legend and the hover name every line too.
SERIES_DASHES = ("solid", "dash", "dot")


def _theme() -> str:
    """``"dark"`` or ``"light"``, the theme the viewer has on. Light when it cannot be told."""
    try:
        theme = st.context.theme.type
    except Exception:  # no Streamlit session, e.g. a plain script
        theme = None
    return "dark" if theme == "dark" else "light"


def text_color() -> str:
    """The text colour for the theme the viewer has on. Black when it cannot be told."""
    return TEXT_COLOR[_theme()]


def legend_rows(names: Sequence[str], width: float, font: int) -> int:
    """About how many rows a horizontal legend of ``names`` takes across ``width`` pixels.

    Plotly puts the entries in one row when they fit, and otherwise in a grid whose
    columns are as wide as the widest entry. An estimate, because only the browser
    knows the width of the text: an entry is its line sample and the gaps around it
    (45px) and its text, at about 0.47 of the font size a character (measured at 14px).
    """
    widths = [45 + 0.47 * font * len(name) for name in names]
    if not widths:
        return 0
    if sum(widths) <= width:
        return 1
    columns = max(1, int(width // max(widths)))
    return -(-len(widths) // columns)


def series_line(i: int, dash: str | None = None) -> dict:
    """The line of the ``i``-th series (from 0) of a chart: a colour, and past eight a dash.

    ``dash`` sets the dash instead, for a chart whose colours say which lines go together,
    as the yields of one country, each maturity in a dash of its own.
    """
    colors = SERIES_COLORS[_theme()]
    return dict(color=colors[i % len(colors)], dash=dash or SERIES_DASHES[(i // len(colors)) % len(SERIES_DASHES)],
                width=2)


def readable(fig, *, height: int | None = None):
    """Apply the app's height, font sizes and text colour to a figure, in place, and return it.

    ``height`` is the height as drawn, in pixels, for a chart whose height the viewer
    chose. Without it the figure's own height is scaled, so call this once per figure:
    a second call would make it taller again.

    Text inside pie slices and bars keeps Plotly's automatic contrast (white on a dark
    slice, black on a light one): the global colour below does not override it.
    """
    color = text_color()
    fig.update_layout(
        height=height or round((fig.layout.height or DEFAULT_HEIGHT) * HEIGHT_SCALE),
        font=dict(size=FONT, color=color),
        legend_font=dict(size=FONT, color=color),
        hoverlabel_font=dict(size=HOVER_FONT),
    )
    # Only a title that exists. Streamlit bolds chart titles by rewriting the text as
    # `<b>${String(title.text)}</b>` whenever the layout has a title at all, so styling
    # the title of a chart without one printed the word "undefined" above it.
    if fig.layout.title.text:
        fig.update_layout(title_font=dict(size=TITLE_FONT, color=color))
    for update_axes in (fig.update_xaxes, fig.update_yaxes):
        update_axes(
            tickfont=dict(size=FONT, color=color),
            title_font=dict(size=AXIS_TITLE_FONT, color=color),
        )
    fig.update_annotations(font=dict(size=FONT, color=color))  # e.g. the cost-basis lines' labels
    return fig


#: The preset ranges a price chart offers, shortest first. Max is the whole stored history.
RANGES = ("1M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y", "Max")
DEFAULT_RANGE = "1Y"
_RANGE_OFFSETS = {
    "1M": pd.DateOffset(months=1),
    "6M": pd.DateOffset(months=6),
    "1Y": pd.DateOffset(years=1),
    "3Y": pd.DateOffset(years=3),
    "5Y": pd.DateOffset(years=5),
    "10Y": pd.DateOffset(years=10),
}


def range_start(preset: str, first: date, last: date) -> date:
    """The first date a preset shows, for a history that runs from ``first`` to ``last``.

    Counted back from the last price rather than from today, so a history a few days
    old still shows a full month. Never before ``first``: a preset longer than the
    history shows all of it.
    """
    if preset == "Max":
        start = first
    elif preset == "YTD":
        start = date(last.year, 1, 1)
    else:
        start = (pd.Timestamp(last) - _RANGE_OFFSETS[preset]).date()
    return max(start, first)


def clamp_window(window: tuple[date, date], first: date, last: date) -> tuple[date, date]:
    """``window`` cut to the history from ``first`` to ``last``; all of it when nothing of the window is left."""
    start, end = max(window[0], first), min(window[1], last)
    return (start, end) if start < end else (first, last)


@dataclass(frozen=True)
class SeriesWindow:
    """The series a chart draws together, each cut to the dates shown, and why any is left out."""

    series: dict[str, pd.Series]
    start: date | None  # the first date shown
    missing: dict[str, str]  # why a series shows nothing
    #: In a rebase, the series whose history begins inside the range, by their first day: each
    #: is at 0% there rather than on the range's first day, as the others are.
    late: dict[str, date] = field(default_factory=dict)


def window_series(series: Mapping[str, pd.Series], preset: str, *, rebase: bool = False) -> SeriesWindow:
    """The part of each series a chart shows for a preset range, keyed as given.

    The range counts back from the latest observation of any of them, so a chart of a
    daily price and a monthly indicator ends on the last price.

    To be rebased, as a % change, each series is 0% on its first day in the range: the
    range's first day for one with data before it, its own first day for one whose history
    begins inside the range (``late``). So a stock listed last year does not cut ten years
    of the others to its one; its line starts later, from 0%, and the page says where.
    A series is left out when it is zero or below on its first day, where a % change does
    not exist.
    """
    present = {key: s.dropna().sort_index() for key, s in series.items()}
    missing = {key: "no data" for key, s in present.items() if s.empty}
    present = {key: s for key, s in present.items() if not s.empty}
    if not present:
        return SeriesWindow({}, None, missing)

    first = min(s.index[0] for s in present.values()).date()
    last = max(s.index[-1] for s in present.values()).date()
    start = pd.Timestamp(range_start(preset, first, last))
    shown = {}
    for key, s in present.items():
        if (cut := s[s.index >= start]).empty:
            missing[key] = "nothing in the time range"
        else:
            shown[key] = cut
    if not rebase or not shown:
        return SeriesWindow(shown, start.date() if shown else None, missing)

    for key, s in list(shown.items()):
        if s.iloc[0] <= 0:
            missing[key] = "zero or below on its first day in the range, so it has no % change"
            del shown[key]
    if not shown:
        return SeriesWindow({}, None, missing)
    first_shown = min(s.index[0] for s in shown.values())
    # Late: no data before the range, so not at 0% on its first day as the others are. A monthly
    # series whose first print in the range comes weeks after it is not; it was there before.
    late = {key: s.index[0].date() for key, s in shown.items()
            if present[key].index[0] > start and s.index[0] > first_shown}
    return SeriesWindow(shown, first_shown.date(), missing, late)


def late_caption(view: SeriesWindow, names: Mapping[str, str]) -> str | None:
    """Under a chart in % change: the series that start later than the range, each at 0% on its
    own first day, by name and day; None when there is none."""
    if not view.late:
        return None
    if len(view.late) == 1:
        ((key, day),) = view.late.items()
        return f"{names[key]} starts later, at 0% on its first day of data, {day:%d %b %Y}."
    listed = "; ".join(f"{names[key]}, {day:%d %b %Y}" for key, day in sorted(view.late.items(), key=lambda kv: kv[1]))
    return f"Starting later, each at 0% on its first day of data: {listed}."


#: Characters a legend entry has room for, by charts per row, in a window about 1280 pixels
#: wide. Plotly does not wrap one, and the page cannot know the window's width.
LEGEND_ROOM = {1: 70, 2: 32, 3: 22}


def lines_figure(view: SeriesWindow, names: Mapping[str, str], units: Mapping[str, str],
                 slots: Mapping[str, int], in_pct: bool, *, change_in_hover: bool = True) -> go.Figure:
    """One line per series of ``view``. In % change they share one axis; in value, each unit gets a panel.

    ``names`` are what the legend and the hover call each series, ``units`` what its values
    are in, and ``slots`` its place in :func:`series_line`: a series keeps its look when
    another is taken off the chart, and on every chart it is on. ``change_in_hover`` adds
    the % change since the first date shown to the hover, which a price has and a rate,
    a spread or a survey balance does not.

    Panels stack on one time axis, so their dates line up, rather than a second scale on
    the right: two scales on one plot line up wherever their ranges happen to put them,
    which shows a relationship the data does not have.
    """
    panels = [None] if in_pct else list(dict.fromkeys(units[key] for key in view.series))
    gap = 0.08
    tall = (1 - gap * (len(panels) - 1)) / len(panels)
    fig = go.Figure()
    for n, unit in enumerate(panels):  # top to bottom
        top = 1 - n * (tall + gap)
        title = f"Change since {view.start:%d %b %Y}" if in_pct else unit or None
        fig.update_layout({f"yaxis{n + 1 if n else ''}": dict(domain=[max(top - tall, 0.0), top], anchor="x",
                                                                title=title)})
    for key, values in view.series.items():
        unit = units[key]
        base = float(values.iloc[0])
        change = values / base - 1 if base > 0 else pd.Series(np.nan, index=values.index)
        panel = 0 if in_pct else panels.index(unit)
        hover = f"%{{customdata[0]:{',.2f' if values.abs().max() >= 1 else ',.4f'}}}" + (f" {unit}" if unit else "")
        if base > 0 and (change_in_hover or in_pct):
            hover += " · %{customdata[1]:+.1%}"
        line = series_line(slots[key])
        # A dot where a line that starts later than the range starts, at 0%: its first day.
        dot = in_pct and key in view.late and len(values) > 1
        fig.add_scatter(
            x=values.index, y=change if in_pct else values, name=names[key], yaxis=f"y{panel + 1 if panel else ''}",
            mode="lines+markers" if dot else ("lines" if len(values) > 1 else "markers"),
            line=line, marker=dict(color=line["color"], size=[8] + [0] * (len(values) - 1) if dot else 8),
            customdata=np.column_stack([values.to_numpy(), change.to_numpy()]),
            hovertemplate=hover + "<extra></extra>",
        )
    if in_pct:
        fig.update_yaxes(tickformat="+.1~%")  # +12%, -0.5%: no trailing zeros
    fig.update_layout(
        template="plotly_white", hovermode="x unified", hoversubplots="axis",
        xaxis=dict(anchor=f"y{len(panels) if len(panels) > 1 else ''}", hoverformat="%d %b %Y"),
        # A legend only where it tells lines apart; the page names a single one above its chart.
        # In a row above the plot, so it takes no width from a chart in a narrow column.
        showlegend=len(view.series) > 1,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    return fig


#: A legend above a chart of many lines: smaller than FONT, so up to 19 names sit above a
#: chart in half a row, and the hover's list of every line does not overflow the plot.
LEGEND_FONT = 14
LEGEND_ROW = 21.2  # pixels a legend row takes at that size (measured)
#: The plot's width, by charts per row, in a window 1440 pixels wide with the sidebar
#: open (measured): what the legend's rows are estimated for. In a wider window the
#: names take fewer rows, and the plot gets the height they leave.
PLOT_WIDTH = {1: 880, 2: 350, 3: 185}


def legend_height(names: Sequence[str], per_row: int = 1) -> int:
    """Pixels a legend of ``names`` above the plot takes, at :data:`LEGEND_FONT`: what a chart
    adds to its height, so the plot keeps about the height chosen however many lines it has."""
    return round(legend_rows(names, PLOT_WIDTH[per_row], LEGEND_FONT) * LEGEND_ROW)


@dataclass(frozen=True)
class TickedLayout:
    """The choices above the charts of the rows ticked in a table."""

    together: bool  # one chart for every row, or a chart each
    preset: str  # one of RANGES
    per_row: int
    height: int  # pixels, as drawn
    in_pct: bool  # % change from each line's first day in the range, or values


def ticked_layout(container, prefix: str, *, noun: str, default_range: str = DEFAULT_RANGE,
                  value: str | None = None, change_help: str = "") -> TickedLayout:
    """Layout, time range, charts per row, chart height and, with ``value``, the y axis.

    Kept in the URL under ``prefix`` (?macro_layout=Together&macro_range=5Y), so a reload
    or a bookmark keeps the choice, and one page's choice is not another's. ``noun`` names
    what is charted, in the plural, for the help. Charts per row means nothing for one
    chart, so it is greyed out while the rows are drawn together.
    """
    together = container.segmented_control(
        "Layout", ("Separate", "Together"), default="Separate", required=True, key=f"{prefix}_layout",
        bind="query-params",
        help=f"Separate: a chart for each of the {noun}. Together: one chart, with a panel for each unit on a "
             f"shared time axis, so {noun} in the same unit are drawn on the same scale.",
    ) == "Together"
    preset = container.segmented_control(
        "Time range", RANGES, default=default_range, required=True, key=f"{prefix}_range", bind="query-params"
    )
    per_row = container.segmented_control(
        "Charts per row", (1, 2, 3), default=2, required=True, key=f"{prefix}_per_row", bind="query-params",
        disabled=together,
    )
    height = container.segmented_control(
        "Chart height", tuple(HEIGHTS), default="Medium", required=True, key=f"{prefix}_height", bind="query-params"
    )
    in_pct = value is not None and container.segmented_control(
        "Y axis", (value, "Change"), default=value, required=True, key=f"{prefix}_y_axis", bind="query-params",
        help=change_help,
    ) == "Change"
    return TickedLayout(together, preset, per_row, HEIGHTS[height], in_pct)


def keep_ticked(url_key: str, ticked: Sequence[str]) -> list[str]:
    """``ticked`` in the order the rows were ticked, which the URL keeps under ``url_key``.

    Those the URL has already come first, in its order, then any new one, so a line keeps
    its colour when another is ticked. The URL is set to the result, so a reload or a
    bookmark ticks the same rows again: see :func:`in_url` for reading it back.
    """
    was = in_url(url_key)
    order = [key for key in was if key in ticked] + [key for key in ticked if key not in was]
    if order:
        st.query_params[url_key] = ",".join(order)
    elif url_key in st.query_params:
        del st.query_params[url_key]
    return order


def in_url(url_key: str) -> list[str]:
    """The keys a URL parameter holds, separated by commas."""
    return [key.strip() for key in st.query_params.get(url_key, "").split(",") if key.strip()]


def grid_controls(container, *, value: str, change_help: str) -> tuple[int, int, bool]:
    """Charts per row, chart height and y axis, for a page that lays its charts out in a grid.

    All kept in the URL (?charts_per_row=3&chart_height=Large&y_axis=Change), so a reload
    or a bookmark keeps the choice. The URL carries an option's label, so the labels stay
    plain words: "% change" would read %25+change there. ``value`` names the y axis that
    is not a change: "Price" on the dashboard.

    Returns the charts per row, the height a chart is drawn at, in pixels, and whether
    the y axis is in % change.
    """
    per_row = container.segmented_control(
        "Charts per row", (1, 2, 3), default=2, required=True, key="charts_per_row", bind="query-params"
    )
    height = container.segmented_control(
        "Chart height", tuple(HEIGHTS), default="Medium", required=True, key="chart_height", bind="query-params"
    )
    in_pct = container.segmented_control(
        "Y axis", (value, "Change"), default=value, required=True, key="y_axis", bind="query-params", help=change_help
    ) == "Change"
    return per_row, HEIGHTS[height], in_pct
