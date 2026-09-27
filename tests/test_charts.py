"""The shared chart look: every text element 50% larger than Streamlit's theme."""

import plotly.express as px
import plotly.graph_objects as go

import charts


def test_readable_sets_every_font_size_and_returns_the_figure():
    fig = go.Figure(go.Scatter(x=[1, 2], y=[3, 4]))
    fig.update_layout(title="t", xaxis_title="x", yaxis_title="y")
    fig.add_hline(y=3.5, annotation_text="avg cost")  # added before, as the pages do

    assert charts.readable(fig) is fig
    layout = fig.layout
    assert layout.font.size == 18
    assert layout.title.font.size == 24
    assert layout.legend.font.size == 18
    assert layout.hoverlabel.font.size == 20
    assert layout.xaxis.tickfont.size == 18 and layout.yaxis.tickfont.size == 18
    assert layout.xaxis.title.font.size == 21 and layout.yaxis.title.font.size == 21
    assert all(a.font.size == 18 for a in layout.annotations)


def test_a_chart_without_a_title_gets_none():
    """Regression: a title with a size and no text showed as "undefined" in Streamlit,
    which renders any title present in the layout as `<b>${String(text)}</b>`."""
    for fig in (px.pie(values=[1, 2], names=["a", "b"]), go.Figure(go.Bar(x=[1], y=["a"], orientation="h"))):
        charts.readable(fig)
        assert "title" not in fig.to_plotly_json()["layout"]


def test_sizes_are_half_again_the_theme_defaults():
    assert (charts.FONT, charts.AXIS_TITLE_FONT, charts.TITLE_FONT) == (12 * 1.5, 14 * 1.5, 16 * 1.5)


def test_readable_keeps_what_the_page_set():
    fig = px.pie(values=[1, 2], names=["a", "b"], height=675)
    fig.update_layout(legend=dict(orientation="h"))
    charts.readable(fig)
    assert fig.layout.height == 675 and fig.layout.legend.orientation == "h"


def _figure_with_everything():
    fig = go.Figure(go.Scatter(x=[1, 2], y=[3, 4]))
    fig.update_layout(title="t", xaxis_title="x", yaxis_title="y")
    fig.add_hline(y=3.5, annotation_text="avg cost")
    return fig


def _text_colors(fig):
    layout = fig.layout
    return {
        layout.font.color, layout.title.font.color, layout.legend.font.color,
        layout.xaxis.tickfont.color, layout.yaxis.tickfont.color,
        layout.xaxis.title.font.color, layout.yaxis.title.font.color,
        *(a.font.color for a in layout.annotations),
    }


def test_chart_text_is_black_on_the_light_theme():
    """Outside a Streamlit session the theme cannot be told, which is treated as light."""
    assert _text_colors(charts.readable(_figure_with_everything())) == {"#000000"}


def test_chart_text_is_white_on_the_dark_theme(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(charts.st, "context", SimpleNamespace(theme=SimpleNamespace(type="dark")))
    assert _text_colors(charts.readable(_figure_with_everything())) == {"#ffffff"}


def test_slice_and_bar_labels_keep_their_automatic_contrast():
    """Only layout-level colours are set; a trace's own text colour stays unset, so
    Plotly still puts white text on a dark slice."""
    fig = charts.readable(px.pie(values=[1, 2], names=["a", "b"]))
    assert fig.data[0].textfont.color is None and fig.data[0].insidetextfont.color is None
