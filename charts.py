"""The look every chart in the app shares.

Streamlit's chart theme draws text at 12px (axis numbers, legends, pie and bar labels,
annotations), 14px (axis titles) and 16px (chart titles), which read small, and draws
axis numbers, axis titles and annotations in grey. Every size here is 50% larger, and
all chart text is black. Pass each figure through :func:`readable` last, right before
``st.plotly_chart``, so it also reaches annotations added along the way.
"""

import streamlit as st

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


def readable(fig):
    """Apply the app's font sizes and text colour to a Plotly figure, in place, and return it.

    Text inside pie slices and bars keeps Plotly's automatic contrast (white on a dark
    slice, black on a light one): the global colour below does not override it.
    """
    color = text_color()
    fig.update_layout(
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
