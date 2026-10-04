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
    assert len(cells) == 6
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
