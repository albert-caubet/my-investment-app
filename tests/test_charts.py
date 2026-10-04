"""The shared chart look: every text element 50% larger than Streamlit's theme; time-range presets."""

from datetime import date

import numpy as np
import pandas as pd
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
    fig = px.pie(values=[1, 2], names=["a", "b"])
    fig.update_layout(legend=dict(orientation="h"))
    charts.readable(fig)
    assert fig.layout.legend.orientation == "h"


def test_charts_are_half_again_as_tall_as_the_page_asks():
    assert charts.readable(px.pie(values=[1], names=["a"])).layout.height == 675  # 450 default
    assert charts.readable(go.Figure().update_layout(height=230)).layout.height == 345  # design bars
    assert charts.readable(go.Figure().update_layout(height=420)).layout.height == 630  # macro chart


def test_a_chosen_height_is_drawn_as_it_is():
    assert charts.readable(go.Figure(), height=900).layout.height == 900
    assert charts.readable(go.Figure().update_layout(height=420), height=450).layout.height == 450


def test_the_medium_height_is_a_default_charts():
    assert charts.HEIGHTS["Medium"] == charts.readable(go.Figure()).layout.height
    assert list(charts.HEIGHTS) == ["Small", "Medium", "Large"]
    assert charts.HEIGHTS["Small"] < charts.HEIGHTS["Medium"] < charts.HEIGHTS["Large"]


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


# ------------------------------------------------------------------------------------------
# Time-range presets
# ------------------------------------------------------------------------------------------

FIRST, LAST = date(2014, 10, 3), date(2026, 10, 2)


def test_presets_count_back_from_the_last_price():
    assert charts.range_start("1M", FIRST, LAST) == date(2026, 9, 2)
    assert charts.range_start("6M", FIRST, LAST) == date(2026, 4, 2)
    assert charts.range_start("1Y", FIRST, LAST) == date(2025, 10, 2)
    assert charts.range_start("5Y", FIRST, LAST) == date(2021, 10, 2)
    assert charts.range_start("10Y", FIRST, LAST) == date(2016, 10, 2)


def test_a_month_back_from_the_end_of_a_month_lands_on_the_shorter_months_end():
    assert charts.range_start("1M", FIRST, date(2026, 3, 31)) == date(2026, 2, 28)


def test_ytd_starts_on_the_first_of_january_of_the_last_prices_year():
    assert charts.range_start("YTD", FIRST, LAST) == date(2026, 1, 1)


def test_max_and_presets_longer_than_the_history_show_all_of_it():
    assert charts.range_start("Max", FIRST, LAST) == FIRST
    assert charts.range_start("5Y", date(2022, 1, 3), LAST) == date(2022, 1, 3)
    assert charts.range_start("YTD", date(2026, 3, 2), LAST) == date(2026, 3, 2)


def test_every_preset_has_a_start():
    assert charts.DEFAULT_RANGE in charts.RANGES
    for preset in charts.RANGES:
        assert FIRST <= charts.range_start(preset, FIRST, LAST) < LAST


def test_a_window_is_cut_to_the_history():
    assert charts.clamp_window((date(2010, 1, 1), date(2030, 1, 1)), FIRST, LAST) == (FIRST, LAST)
    assert charts.clamp_window((date(2020, 1, 1), date(2021, 1, 1)), FIRST, LAST) == (date(2020, 1, 1), date(2021, 1, 1))


def test_a_window_outside_the_history_becomes_all_of_it():
    assert charts.clamp_window((date(2000, 1, 1), date(2001, 1, 1)), FIRST, LAST) == (FIRST, LAST)


# --- charts of several series ------------------------------------------------------


def test_series_take_the_palette_in_order_then_a_dash_past_eight():
    lines = [charts.series_line(i) for i in range(19)]
    assert [line["color"] for line in lines[:8]] == list(charts.SERIES_COLORS["light"])
    assert {line["dash"] for line in lines[:8]} == {"solid"}
    assert lines[8]["color"] == lines[0]["color"] and lines[8]["dash"] == "dash"
    assert lines[16]["dash"] == "dot"
    assert len({(line["color"], line["dash"]) for line in lines}) == 19  # no two lines alike


def test_series_take_the_dark_steps_on_the_dark_theme(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(charts.st, "context", SimpleNamespace(theme=SimpleNamespace(type="dark")))
    assert [charts.series_line(i)["color"] for i in range(8)] == list(charts.SERIES_COLORS["dark"])


def test_a_legend_that_fits_takes_one_row():
    assert charts.legend_rows(["Gold", "Silver"], width=400, font=14) == 1
    assert charts.legend_rows([], width=400, font=14) == 0


def test_a_legend_that_wraps_is_a_grid_as_wide_as_its_longest_name():
    short = ["AEX", "SMI", "ATX", "PSI", "BUX"]
    # too wide for one row, so a grid: the long name makes every column 216px, two to a row
    # where the short names alone would sit seven to a row
    assert charts.legend_rows(short + ["European natural gas (TTF)"], width=500, font=14) == 3
    assert charts.legend_rows(short * 4, width=200, font=14) == 7  # 20 entries, three columns of 64px


def _daily(start, end="2026-10-02", value=100.0):
    days = pd.bdate_range(start, end)
    return pd.Series(value + np.arange(len(days)), index=days, dtype=float)


def _monthly(start, end="2026-08-01", value=4.0):
    months = pd.date_range(start, end, freq="MS")
    return pd.Series(value, index=months, dtype=float)


def test_the_range_counts_back_from_the_latest_observation_of_any_series():
    view = charts.window_series({"price": _daily("2020-01-01"), "rate": _monthly("2000-01-01")}, "1Y")
    assert view.start == date(2025, 10, 2)  # a year before the last price, not before the last monthly print
    assert view.series["price"].index[0] == pd.Timestamp("2025-10-02")
    assert view.series["rate"].index[0] == pd.Timestamp("2025-11-01")  # its first print in the range
    assert not view.missing and not view.common_start


def test_rebased_series_start_on_the_first_day_every_one_has_data():
    view = charts.window_series({"old": _daily("2000-01-03"), "new": _daily("2026-03-02")}, "1Y", rebase=True)
    assert view.start == date(2026, 3, 2)
    assert {key: s.index[0] for key, s in view.series.items()} == {"old": pd.Timestamp("2026-03-02"),
                                                                   "new": pd.Timestamp("2026-03-02")}
    assert view.common_start  # "old" had data from the start of the range, and lost it


def test_a_range_that_opens_on_a_weekend_is_no_common_start():
    """A year back from Monday 5 October 2026 is a Sunday. The first trading day after it is where
    the line begins, and nothing about it is common to several series."""
    view = charts.window_series({"only": _daily("2020-01-01", end="2026-10-05")}, "1Y", rebase=True)
    assert view.start == date(2025, 10, 6)
    assert not view.common_start


def test_without_a_rebase_nothing_is_cut_to_a_common_start():
    view = charts.window_series({"old": _daily("2000-01-03"), "new": _daily("2026-03-02")}, "1Y")
    assert view.series["old"].index[0] == pd.Timestamp("2025-10-02")


def test_a_series_that_ends_before_the_others_start_is_left_out_of_a_rebase():
    view = charts.window_series(
        {"gone": _daily("2025-11-03", end="2025-12-31"), "a": _daily("2026-01-05"), "b": _daily("2025-10-01")},
        "1Y", rebase=True,
    )
    assert set(view.series) == {"a", "b"}
    assert view.missing == {"gone": "ends before the others start"}
    assert view.start == date(2026, 1, 5)


def test_a_series_at_or_below_zero_has_no_change_and_the_start_moves_back():
    spread = _daily("2026-06-01", value=0.0)  # starts at 0: no % change from there
    view = charts.window_series({"price": _daily("2020-01-01"), "spread": spread}, "1Y", rebase=True)
    assert set(view.series) == {"price"}
    assert "no % change" in view.missing["spread"]
    assert view.start == date(2025, 10, 2)  # the price alone starts with the range again
    assert not view.common_start


def test_a_series_with_nothing_in_the_range_or_no_data_says_so():
    view = charts.window_series(
        {"price": _daily("2020-01-01"), "old": _daily("2001-01-01", end="2005-12-30"), "empty": pd.Series(dtype=float)},
        "1Y",
    )
    assert view.missing == {"old": "nothing in the time range", "empty": "no data"}
    assert set(view.series) == {"price"}


def test_nothing_to_show():
    assert charts.window_series({}, "1Y") == charts.SeriesWindow({}, None, {})
    view = charts.window_series({"empty": pd.Series(dtype=float)}, "Max", rebase=True)
    assert view.series == {} and view.start is None
