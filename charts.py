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
turns a preset into the dates it shows.
"""

from datetime import date

import pandas as pd
import streamlit as st

#: Every chart is this much taller than the height its page asks for.
HEIGHT_SCALE = 1.5
DEFAULT_HEIGHT = 450  # what Streamlit and Plotly use when a figure sets none
#: Heights, as drawn, that a page may let the viewer choose. Medium is a default chart's.
HEIGHTS = {"Small": 450, "Medium": round(DEFAULT_HEIGHT * HEIGHT_SCALE), "Large": 900}

FONT = 18  # 12 * 1.5
AXIS_TITLE_FONT = 21  # 14 * 1.5
TITLE_FONT = 24  # 16 * 1.5
HOVER_FONT = 20  # Plotly's 13 * 1.5, rounded

#: Black on the light theme; white on the dark one, where black would vanish.
TEXT_COLOR = {"light": "#000000", "dark": "#ffffff"}


def text_color() -> str:
    """The text colour for the theme the viewer has on. Black when it cannot be told."""
    try:
        theme = st.context.theme.type
    except Exception:  # no Streamlit session, e.g. a plain script
        theme = None
    return TEXT_COLOR["dark" if theme == "dark" else "light"]


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
RANGES = ("1M", "6M", "YTD", "1Y", "3Y", "5Y", "Max")
DEFAULT_RANGE = "1Y"
_RANGE_OFFSETS = {
    "1M": pd.DateOffset(months=1),
    "6M": pd.DateOffset(months=6),
    "1Y": pd.DateOffset(years=1),
    "3Y": pd.DateOffset(years=3),
    "5Y": pd.DateOffset(years=5),
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
