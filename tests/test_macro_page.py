"""Headless smoke test for the Macro page on a seeded temporary database. No network."""

import base64
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import charts
from invest.data.cache import Store
from invest.data.releases import Release
from invest.jobs.refresh import ingest_releases
from invest.macro import scorecard as sc
from invest.macro.catalog import load_catalog

ROOT = Path(__file__).resolve().parents[1]


def _seed(path: Path) -> None:
    rng = np.random.default_rng(3)
    months = pd.date_range("2000-01-01", "2026-08-01", freq="MS")
    days = pd.bdate_range("2016-01-01", "2026-09-09")
    catalog = load_catalog()
    with Store(path) as store:
        store.upsert_series("US_UNRATE", pd.Series(rng.normal(4.5, 0.5, len(months)), index=months), source="fred")
        store.upsert_series("US_SAHM", pd.Series(np.abs(rng.normal(0.1, 0.1, len(months))), index=months), source="fred")
        store.upsert_series("US_CPI", pd.Series(100 * 1.025 ** (np.arange(len(months)) / 12), index=months), source="fred")
        store.upsert_series("US_10Y", pd.Series(rng.normal(4.0, 0.5, len(days)), index=days), source="fred")
        store.upsert_series("US_CURVE_10Y3M", pd.Series(rng.normal(0.5, 0.5, len(days)), index=days), source="fred")
        store.upsert_series("US_HY_OAS", pd.Series(rng.normal(4.0, 0.8, len(days)), index=days), source="fred")
        store.upsert_series("SPX", pd.Series(3000 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days))), index=days), source="yahoo")
        usrec = pd.Series(0.0, index=months)
        usrec["2007-12-01":"2009-06-01"] = 1.0
        usrec["2020-03-01":"2020-04-01"] = 1.0
        store.upsert_series("US_RECESSION", usrec, source="fred")
        ingest_releases(store, [Release("US_ISM_MFG_PMI", date(2026, 8, 1), date(2026, 9, 1), 48.7, "test")],
                        catalog, fetched_at=pd.Timestamp("2026-09-01").to_pydatetime())
        _seed_market_prices(store, days)
        run = store.start_run("refresh")
        readings = sc.build_readings(store, catalog, today=date(2026, 9, 10))
        regime, results = sc.build_regime(store, catalog)
        store.save_snapshot(run, "scorecard", sc.build_snapshot(readings, regime, results))
        store.finish_run(run, ok=8, failed=0)


def _seed_market_prices(store: Store, days: pd.DatetimeIndex) -> None:
    """Prices for part of the world chart; its other symbols, and the other charts, have none.

    The S&P 500 and the STOXX 600 cover the whole seeded history, the EEM starts in
    2020, and the MSCI World has two days and Yahoo's live quote dated on a Sunday.
    """
    rng = np.random.default_rng(5)
    for symbol, shown in (("^GSPC", days), ("^STOXX", days), ("EEM", days[days >= "2020-03-02"])):
        closes = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(shown)))
        store.upsert_prices(symbol, pd.DataFrame({"close": closes}, index=shown))
    sunday = pd.DatetimeIndex(["2026-09-08", "2026-09-09", "2026-09-13"])
    store.upsert_prices("^990100-USD-STRD", pd.DataFrame({"close": [4900.0, 4926.3, 4926.25]}, index=sunday))


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    path = tmp_path / "market.duckdb"
    _seed(path)
    monkeypatch.setenv("INVEST_DB", str(path))
    return path


def _app() -> AppTest:
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)


def _macro(at: AppTest) -> AppTest:
    return at.switch_page("pages/macro.py").run()


def test_macro_page_renders_from_the_database(seeded_db):
    at = _macro(_app().run())
    assert not at.exception
    headers = [h.value for h in at.subheader]
    assert any(h.startswith("Regime:") for h in headers)
    assert "Scorecard" in headers and "Data freshness" in headers
    # every scorecard table shows a value with its date
    assert len(at.dataframe) >= 3
    assert any("Regime" in h for h in headers)


def test_macro_page_without_a_database_explains_what_to_run(tmp_path, monkeypatch):
    monkeypatch.setenv("INVEST_DB", str(tmp_path / "absent.duckdb"))
    at = _macro(_app().run())
    assert not at.exception
    assert any("invest.jobs.refresh" in i.value for i in at.info)


# --- market charts ---------------------------------------------------------------------


def _market_cells(at: AppTest) -> list:
    """The columns holding a market chart's expander, in page order."""
    return [c for c in at.columns if any(e.label.startswith("📈") for e in c.expander)]


def _market_chart(at: AppTest, title: str):
    return next(e for c in _market_cells(at) for e in c.expander if e.label == f"📈 {title}")


def _figure(expander) -> dict:
    (chart,) = expander.get("plotly_chart")
    return json.loads(chart.proto.spec)


def _caption(expander) -> str:
    return " ".join(c.value for c in expander.caption)


def _numbers(values) -> np.ndarray:
    """A trace's numbers, written by Plotly as a plain list or as a base64 typed array."""
    if isinstance(values, dict):
        return np.frombuffer(base64.b64decode(values["bdata"]), dtype=values["dtype"]).astype(float)
    return np.asarray(values, dtype=float)


def _with_range(preset: str) -> AppTest:
    at = _macro(_app().run())
    at.button_group(key="market_range").set_value(preset).run()
    assert not at.exception
    return at


def test_market_charts_sit_two_to_a_row_by_default(seeded_db):
    at = _macro(_app().run())
    assert not at.exception
    assert "Markets" in [h.value for h in at.subheader]
    assert at.button_group(key="market_range").value == "1Y"
    assert at.button_group(key="market_per_row").value == 2
    assert at.button_group(key="market_height").value == "Medium"
    cells = _market_cells(at)
    assert [e.label for c in cells for e in c.expander] == [
        "📈 World main indices", "📈 World small and mid caps", "📈 Europe main indices",
        "📈 Europe mid caps", "📈 Europe small caps", "📈 Commodities",
        "📈 Government bond yields", "📈 Corporate bond yields",
    ]
    assert {c.weight for c in cells} == {1 / 2}


def test_every_line_is_the_change_from_its_first_close_in_the_range(seeded_db):
    fig = _figure(_market_chart(_macro(_app().run()), "World main indices"))
    assert [trace["name"] for trace in fig["data"]] == ["S&P 500", "STOXX Europe 600", "MSCI Emerging Markets*", "MSCI World"]
    with Store(seeded_db, read_only=True) as store:
        spx = store.read_prices("^GSPC")
    shown = spx["2025-09-09":]  # a year back from the last close, 9 Sep 2026
    np.testing.assert_allclose(_numbers(fig["data"][0]["y"]), shown / shown.iloc[0] - 1)
    assert all(_numbers(trace["y"])[0] == 0 for trace in fig["data"])
    assert fig["layout"]["yaxis"]["tickformat"] == "+.1~%"
    assert fig["layout"]["yaxis"]["title"]["text"] == "Change since 09 Sep 2025"
    # the Sunday quote is not a close: the MSCI World line ends on the Wednesday
    assert fig["data"][3]["x"][-1].startswith("2026-09-09")


def test_a_line_keeps_its_colour_when_one_before_it_has_no_prices(seeded_db):
    fig = _figure(_market_chart(_macro(_app().run()), "World main indices"))
    # by place in the file: the Euro Stoxx 50, third and with no prices, leaves its colour unused
    assert [trace["line"]["color"] for trace in fig["data"]] == [charts.SERIES_COLORS["light"][i] for i in (0, 1, 3, 4)]


def test_what_a_chart_lacks_is_said_under_it(seeded_db):
    at = _macro(_app().run())
    world = _caption(_market_chart(at, "World main indices"))
    assert world.startswith("Latest close 09 Sep 2026.")
    assert "MSCI Emerging Markets: EEM, iShares MSCI Emerging Markets ETF." in world
    assert ("No prices stored for Euro Stoxx 50 (^STOXX50E), MSCI ACWI (^892400-USD-STRD), "
            "NASDAQ Composite (^IXIC)") in world
    mid = _market_chart(at, "Europe mid caps")
    assert any("No prices stored for this chart yet" in i.value for i in mid.info)
    assert "Not charted: BEL Mid (Belgium, mid caps after the BEL 20): no history on Yahoo since 2015" in _caption(mid)


def test_what_each_line_covers_is_said_under_its_chart(seeded_db):
    at = _macro(_app().run())
    world = _caption(_market_chart(at, "World main indices"))
    assert "**Covers.** S&P 500: US, 500 large caps · STOXX Europe 600: Europe," in world
    # also under a chart with no prices stored yet
    europe = _caption(_market_chart(at, "Europe main indices"))
    assert "ATHEX Composite: Greece, 60 largest" in europe
    assert "sWIG80: Poland" in _caption(_market_chart(at, "Europe small caps"))


def test_the_market_charts_open_the_page(seeded_db):
    headers = [h.value for h in _macro(_app().run()).subheader]
    assert headers.index("Markets") == 0
    assert headers[1].startswith("Regime:")


def test_a_line_starting_inside_the_range_is_named_under_the_chart(seeded_db):
    world = _market_chart(_with_range("YTD"), "World main indices")
    assert "Starting later, at 0% on their first close: MSCI World on 08 Sep 2026." in _caption(world)


def test_max_starts_where_every_line_has_data(seeded_db):
    world = _market_chart(_with_range("Max"), "World main indices")
    assert {trace["x"][0][:10] for trace in _figure(world)["data"]} == {"2026-09-08"}  # the MSCI World's first close
    assert "Max starts on 08 Sep 2026, the first day with a close for every line." in _caption(world)


def test_long_ranges_plot_weekly_closes(seeded_db):
    spx = _figure(_market_chart(_with_range("5Y"), "World main indices"))["data"][0]
    assert 255 <= len(spx["x"]) <= 265  # five years of weeks, not 1,300 days
    assert _numbers(spx["y"])[0] == 0


def test_ten_years_back(seeded_db):
    world = _figure(_market_chart(_with_range("10Y"), "World main indices"))
    assert world["layout"]["yaxis"]["title"]["text"] == "Change since 09 Sep 2016"
    assert world["data"][0]["x"][0].startswith("2016-09-09")


@pytest.mark.parametrize("per_row", [1, 3])
def test_charts_per_row_rearranges_the_market_charts(seeded_db, per_row):
    at = _macro(_app().run())
    at.button_group(key="market_per_row").set_value(per_row).run()
    assert not at.exception
    cells = _market_cells(at)
    assert len(cells) == 8
    assert {round(c.weight, 6) for c in cells} == {round(1 / per_row, 6)}


def test_chart_height_sets_every_market_chart_and_the_legend_adds_to_it(seeded_db):
    at = _macro(_app().run())
    medium = _figure(_market_chart(at, "World main indices"))["layout"]
    at.button_group(key="market_height").set_value("Large").run()
    large = _figure(_market_chart(at, "World main indices"))["layout"]
    assert large["height"] - medium["height"] == charts.HEIGHTS["Large"] - charts.HEIGHTS["Medium"]
    assert large["height"] > charts.HEIGHTS["Large"]  # the rows of names above the plot
    assert large["legend"]["font"]["size"] < charts.FONT


def test_market_chart_choices_are_read_from_the_url(seeded_db):
    at = _app().run().switch_page("pages/macro.py")
    at.query_params["market_range"] = "5Y"
    at.query_params["market_per_row"] = "3"
    at.query_params["market_height"] = "Small"
    at.run()
    assert not at.exception
    assert at.button_group(key="market_range").value == "5Y"
    assert at.button_group(key="market_per_row").value == 3
    assert at.button_group(key="market_height").value == "Small"


# --- indicator charts ------------------------------------------------------------------


def _indicator_cells(at: AppTest) -> list:
    """The expanders holding an indicator's chart, in page order."""
    return [e for e in at.expander if e.label.startswith("📊")]


def _ticked(*ids: str) -> AppTest:
    """The Macro page opened from a URL that ticks ``ids`` in the scorecard."""
    at = _app().run().switch_page("pages/macro.py")
    at.query_params["macro_chart"] = ",".join(ids)
    at.run()
    assert not at.exception
    return at


def test_the_chart_asks_for_rows_to_be_ticked(seeded_db):
    at = _macro(_app().run())
    assert "Chart" in [h.value for h in at.subheader]
    assert any("Tick one or more rows of the scorecard" in i.value for i in at.info)
    assert not _indicator_cells(at)
    assert at.button_group(key="macro_range").value == "10Y"
    assert at.button_group(key="macro_layout").value == "Separate"


def test_rows_ticked_in_the_url_are_ticked_in_the_scorecard_and_charted_apart(seeded_db):
    at = _ticked("US_SAHM", "US_UNRATE")
    assert at.session_state["macro_scorecard_labor"]["selection"]["rows"]
    # in the URL's order, which is the order they were ticked in, not the table's
    assert [e.label for e in _indicator_cells(at)] == ["📊 Sahm rule (real-time)", "📊 US unemployment rate"]
    sahm, unrate = (_figure(e) for e in _indicator_cells(at))
    assert [t["name"] for t in sahm["data"]] == ["Sahm rule (real-time)"]
    assert sahm["data"][0]["line"]["color"] == charts.SERIES_COLORS["light"][0]
    assert unrate["data"][0]["line"]["color"] == charts.SERIES_COLORS["light"][1]
    assert sahm["layout"]["yaxis"]["title"]["text"] == "pp"
    # ten years back from the last observation, Aug 2026
    assert sahm["data"][0]["x"][0].startswith("2016-08-01")
    # the Sahm rule's threshold, and the 2020 recession shaded
    lines = [s for s in sahm["layout"]["shapes"] if s["type"] == "line"]
    assert [(s["y0"], s["yref"]) for s in lines] == [(0.5, "y")]
    assert [a["text"] for a in sahm["layout"]["annotations"]] == ["sahm"]
    rects = [s for s in sahm["layout"]["shapes"] if s["type"] == "rect"]
    assert [r["x0"][:7] for r in rects] == ["2020-03"]
    assert "% change" not in sahm["data"][0]["hovertemplate"] and "customdata[1]" not in sahm["data"][0]["hovertemplate"]
    assert "Source:" in _caption(_indicator_cells(at)[0])


def test_ticking_a_row_charts_it_and_puts_it_in_the_url(seeded_db):
    at = _macro(_app().run())
    with Store(seeded_db, read_only=True) as store:
        readings = sc.build_readings(store, load_catalog(), today=date.today())
    labor = [r.id for r in readings if r.group == "labor"]  # the labour table's rows, in its order
    at.session_state["macro_scorecard_labor"] = {"selection": {"rows": [labor.index("US_UNRATE")], "columns": [],
                                                                "cells": []}}
    at.run()
    assert not at.exception
    assert at.query_params["macro_chart"] == ["US_UNRATE"]
    assert [e.label for e in _indicator_cells(at)] == ["📊 US unemployment rate"]


def test_together_draws_one_chart_with_a_panel_per_unit(seeded_db):
    at = _ticked("US_UNRATE", "US_SAHM", "US_10Y")
    at.button_group(key="macro_layout").set_value("Together").run()
    assert not at.exception
    assert not _indicator_cells(at)
    assert at.button_group(key="macro_per_row").proto.disabled
    (fig,) = [json.loads(c.proto.spec) for c in at.get("plotly_chart")
              if any(t.get("name") == "US unemployment rate" for t in json.loads(c.proto.spec)["data"])]
    assert [(t["name"], t.get("yaxis", "y")) for t in fig["data"]] == [
        ("US unemployment rate", "y"), ("Sahm rule (real-time)", "y2"), ("US 10-year Treasury yield", "y")]
    assert fig["layout"]["showlegend"]
    assert fig["layout"]["yaxis"]["title"]["text"] == "%" and fig["layout"]["yaxis2"]["title"]["text"] == "pp"
    # the Sahm threshold sits in the Sahm rule's panel; the recessions shade every panel
    assert [(s["y0"], s["yref"]) for s in fig["layout"]["shapes"] if s["type"] == "line"] == [(0.5, "y2")]
    assert {s["yref"] for s in fig["layout"]["shapes"] if s["type"] == "rect"} == {"paper"}
    assert fig["layout"]["height"] > charts.HEIGHTS["Medium"]  # the legend's row on top
    notes = " ".join(m.value for m in at.markdown)
    assert "**US unemployment rate.**" in notes and "**Sahm rule (real-time).**" in notes


def test_rules_on_one_level_share_a_line(seeded_db):
    (cell,) = _indicator_cells(_ticked("US_CURVE_10Y3M"))
    fig = _figure(cell)
    assert [s["y0"] for s in fig["layout"]["shapes"] if s["type"] == "line"] == [0.0]
    assert [a["text"] for a in fig["layout"]["annotations"]] == ["curve_inverted, curve_uninversion"]


@pytest.mark.parametrize("per_row", [1, 3])
def test_charts_per_row_and_height_set_the_indicator_charts(seeded_db, per_row):
    at = _ticked("US_UNRATE", "US_SAHM", "US_10Y")
    at.button_group(key="macro_per_row").set_value(per_row).run()
    at.button_group(key="macro_height").set_value("Large").run()
    assert not at.exception
    cells = [c for c in at.columns if any(e.label.startswith("📊") for e in c.expander)]
    assert len(cells) == 3 and {round(c.weight, 6) for c in cells} == {round(1 / per_row, 6)}
    assert {_figure(e)["layout"]["height"] for e in _indicator_cells(at)} == {charts.HEIGHTS["Large"]}


def test_max_shows_the_whole_history(seeded_db):
    at = _ticked("US_UNRATE")
    at.button_group(key="macro_range").set_value("Max").run()
    (cell,) = _indicator_cells(at)
    assert _figure(cell)["data"][0]["x"][0].startswith("2000-01-01")


def test_an_unknown_id_in_the_url_is_dropped(seeded_db):
    at = _ticked("NOT_A_SERIES", "US_UNRATE")
    assert [e.label for e in _indicator_cells(at)] == ["📊 US unemployment rate"]
    assert at.query_params["macro_chart"] == ["US_UNRATE"]


# --- EUR/USD ---------------------------------------------------------------------------


def _with_eurusd(path: Path) -> pd.Series:
    """A daily EUR/USD rate, as the refresh stores it, from 2016 to 9 Sep 2026."""
    days = pd.bdate_range("2016-01-01", "2026-09-09")
    rate = pd.Series(np.linspace(1.05, 1.17, len(days)), index=days)
    with Store(path) as store:
        store.upsert_series("EURUSD", rate, source="yahoo")
    return rate


def _eurusd_cell(at: AppTest):
    return next(e for e in at.expander if e.label == "💱 EUR/USD")


def test_eurusd_opens_the_market_charts_as_a_rate(seeded_db):
    _with_eurusd(seeded_db)
    at = _macro(_app().run())
    assert not at.exception
    first_row = next(c for c in at.columns if c.expander)
    assert first_row.expander[0].label == "💱 EUR/USD"  # ahead of the world indices
    assert [e.label for e in at.expander if e.label[0] in "💱📈"][:2] == ["💱 EUR/USD", "📈 World main indices"]
    fig = _figure(_eurusd_cell(at))
    assert fig["layout"]["yaxis"]["title"]["text"] == "USD per EUR"
    values = _numbers(fig["data"][0]["y"])
    assert 1.0 < values.min() and values.max() < 1.2  # the rate itself, not a % change
    assert fig["data"][0]["x"][0].startswith("2025-09-09")  # a year back, the default range
    assert fig["layout"]["height"] == charts.HEIGHTS["Medium"]
    assert "dollars per euro on 09 Sep 2026" in _caption(_eurusd_cell(at))


def test_eurusd_follows_the_market_controls(seeded_db):
    _with_eurusd(seeded_db)
    at = _macro(_app().run())
    at.button_group(key="market_range").set_value("5Y").run()
    at.button_group(key="market_height").set_value("Large").run()
    assert not at.exception
    fig = _figure(_eurusd_cell(at))
    assert fig["layout"]["height"] == charts.HEIGHTS["Large"]
    assert 255 <= len(fig["data"][0]["x"]) <= 265  # five years of weekly closes, as the indices


def test_eurusd_without_a_stored_rate_says_so(seeded_db):
    at = _macro(_app().run())
    assert not at.exception
    assert any("No EUR/USD rate stored yet" in i.value for i in _eurusd_cell(at).info)


# --- bond yields -----------------------------------------------------------------------


def _with_yields(path: Path) -> None:
    """Yields as the refresh stores them, to 9 Sep 2026 like the seeded prices. The 10-year Bund
    starts below zero, as it was in 2016; the US high-yield index has FRED's last three years only;
    Spain's 10-year is a monthly average, to August. The US 10-year is the seeded one."""
    days = pd.bdate_range("2016-01-01", "2026-09-09")
    months = pd.date_range("2016-01-01", "2026-08-01", freq="MS")
    recent = days[days >= "2023-09-11"]
    with Store(path) as store:
        store.upsert_series("DE_10Y", pd.Series(np.linspace(-0.5, 3.0, len(days)), index=days), source="bbk")
        store.upsert_series("ES_10Y", pd.Series(np.linspace(1.5, 3.5, len(months)), index=months), source="eurostat")
        store.upsert_series("US_HY_YIELD", pd.Series(np.linspace(9.0, 6.5, len(recent)), index=recent), source="fred")
        store.upsert_series("DE_CORP_YIELD", pd.Series(np.linspace(1.0, 4.0, len(days)), index=days), source="bbk")


def _trace(fig: dict, name: str) -> dict:
    return next(trace for trace in fig["data"] if trace["name"] == name)


def test_the_bond_charts_are_yields_in_percent(seeded_db):
    _with_yields(seeded_db)
    at = _macro(_app().run())
    assert not at.exception
    cell = _market_chart(at, "Government bond yields")
    fig = _figure(cell)
    # the lines with yields stored, in the file's order
    assert [trace["name"] for trace in fig["data"]] == ["US 10-year", "Germany 10-year", "Spain 10-year"]
    with Store(seeded_db, read_only=True) as store:
        bund = store.read_series("DE_10Y")
    shown = bund["2025-09-09":]  # a year back from the last yield, 9 Sep 2026
    np.testing.assert_allclose(_numbers(_trace(fig, "Germany 10-year")["y"]), shown)  # the yield itself, not a change
    assert fig["layout"]["yaxis"]["title"]["text"] == "Yield" and fig["layout"]["yaxis"]["ticksuffix"] == "%"
    # a colour a country, a dash a maturity
    light = charts.SERIES_COLORS["light"]
    assert [(t["line"]["color"], t["line"]["dash"]) for t in fig["data"]] == [
        (light[0], "solid"), (light[1], "solid"), (light[4], "solid")]
    caption = _caption(cell)
    assert caption.startswith("Latest yields on 09 Sep 2026.")
    assert "No yields stored for US 2-year (US_2Y), US 30-year (US_30Y), Germany 2-year (DE_2Y)" in caption
    # what each line covers, the maturities of one country together
    assert ("**Covers.** US 2-year, US 10-year, US 30-year: Treasuries at a constant maturity (FRED) · "
            "Germany 2-year, Germany 10-year, Germany 30-year: Bunds,") in caption


def test_the_hover_adds_a_yields_move_in_basis_points(seeded_db):
    _with_yields(seeded_db)
    bund = _trace(_figure(_market_chart(_macro(_app().run()), "Government bond yields")), "Germany 10-year")
    moved, y = _numbers(bund["customdata"]), _numbers(bund["y"])
    assert moved[0] == 0
    assert moved[-1] == round((y[-1] - y[0]) * 100)  # whole basis points
    assert (moved == np.round(moved)).all()
    # "+d": Plotly turns "+.0f" into "~+.0f", which d3 refuses, and the hover showed the raw float
    assert bund["hovertemplate"] == "Germany 10-year: %{y:.2f}% · %{customdata:+d} bp<extra></extra>"


def test_a_monthly_average_is_drawn_across_its_month(seeded_db):
    _with_yields(seeded_db)
    cell = _market_chart(_macro(_app().run()), "Government bond yields")
    spain = _trace(_figure(cell), "Spain 10-year")
    days = pd.DatetimeIndex(spain["x"])
    assert days[0] == pd.Timestamp("2025-09-09")  # September's average from the range's first day
    assert days[-1] == pd.Timestamp("2026-08-31")  # August's, the latest, to the end of its month
    y = pd.Series(_numbers(spain["y"]), index=days)
    assert y.groupby(days.to_period("M")).nunique().eq(1).all()  # one value a month: a step
    assert y.nunique() == 12
    assert spain["text"][-1] == "August 2026" and "%{text} average" in spain["hovertemplate"]
    assert ("Spain 10-year is a monthly average, each drawn across its month; the latest is August 2026's."
            in _caption(cell))


def test_monthly_averages_of_different_months_are_each_dated(seeded_db):
    _with_yields(seeded_db)
    months = pd.date_range("2025-01-01", "2026-07-01", freq="MS")  # Italy's published to July only
    with Store(seeded_db) as store:
        store.upsert_series("IT_10Y", pd.Series(np.linspace(3.5, 3.9, len(months)), index=months), source="eurostat")
    caption = _caption(_market_chart(_macro(_app().run()), "Government bond yields"))
    assert ("Italy 10-year and Spain 10-year are monthly averages, each drawn across its month; "
            "the latest: Italy 10-year July 2026, Spain 10-year August 2026.") in caption


def test_zero_is_marked_only_where_a_yield_went_below_it(seeded_db):
    _with_yields(seeded_db)
    assert not _figure(_market_chart(_macro(_app().run()), "Government bond yields"))["layout"].get("shapes")
    fig = _figure(_market_chart(_with_range("Max"), "Government bond yields"))
    assert [(s["type"], s["y0"]) for s in fig["layout"]["shapes"]] == [("line", 0)]  # the Bund in 2016
    assert _trace(fig, "Germany 10-year")["x"][0].startswith("2016-01-01")  # Max: the whole history


def test_a_yield_with_a_shorter_history_starts_later_and_is_named(seeded_db):
    _with_yields(seeded_db)
    at = _with_range("5Y")
    cell = _market_chart(at, "Corporate bond yields")
    fig = _figure(cell)
    assert [trace["name"] for trace in fig["data"]] == ["US high yield", "German companies"]
    assert [trace["line"]["dash"] for trace in fig["data"]] == ["solid", "dash"]  # Europe dashed
    assert "Starting later: US high yield on 11 Sep 2023." in _caption(cell)
    assert 255 <= len(_trace(fig, "German companies")["x"]) <= 265  # weekly past two years, as the indices
    assert "Not charted: Euro investment grade (ICE BofA Euro Corporate Index): FRED does not publish it" in _caption(cell)


def test_a_bond_chart_without_yields_says_so(seeded_db):
    at = _macro(_app().run())
    assert not at.exception
    corporate = _market_chart(at, "Corporate bond yields")
    assert any("No yields stored for this chart yet" in i.value for i in corporate.info)
    assert "Not charted: Euro investment grade" in _caption(corporate)


def test_the_bond_charts_follow_the_market_controls(seeded_db):
    _with_yields(seeded_db)
    at = _macro(_app().run())
    at.button_group(key="market_height").set_value("Large").run()
    assert not at.exception
    fig = _figure(_market_chart(at, "Government bond yields"))
    names = [trace["name"] for trace in fig["data"]]
    assert fig["layout"]["height"] == charts.HEIGHTS["Large"] + charts.legend_height(names, 2)
