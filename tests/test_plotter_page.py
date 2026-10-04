"""Headless tests for the Custom charts page, on canned search answers and histories. No network.

Every Yahoo and FRED call the page makes goes through ``market_data``, replaced here
before each run; macro series come from a seeded temporary database, as on the Macro page.
"""

import base64
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import charts
import market_data as md
from invest import secrets
from invest.data.cache import Store
from invest.data.fred import parse_series_list

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
DAYS = pd.bdate_range("2020-01-02", "2026-10-02")
NEWCO_LISTED = "2026-03-02"

QUOTES = {
    "apple": [
        {"symbol": "AAPL", "quoteType": "EQUITY", "longname": "Apple Inc.", "exchDisp": "NASDAQ"},
        {"symbol": "APLE", "quoteType": "EQUITY", "longname": "Apple Hospitality REIT, Inc.", "exchDisp": "NYSE"},
        {"symbol": "AAPL261218C00200000", "quoteType": "OPTION", "shortname": "AAPL Dec 2026 200 call"},
    ],
    "microsoft": [{"symbol": "MSFT", "quoteType": "EQUITY", "longname": "Microsoft Corporation", "exchDisp": "NASDAQ"}],
}
NAMES = {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation", "GOOG": "Alphabet Inc.", "NEWCO": "Newco",
         "IWDA.AS": "iShares Core MSCI World UCITS ETF USD (Acc)"}
CURRENCIES = {"IWDA.AS": "EUR"}


@pytest.fixture(autouse=True)
def fresh_caches():
    """The page caches by reference, and a reference means the same canned series in every test only by luck."""
    st.cache_data.clear()


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    path = tmp_path / "market.duckdb"
    months = pd.date_range("2000-01-01", "2026-08-01", freq="MS")
    with Store(path) as store:
        store.upsert_series("US_UNRATE", pd.Series(np.linspace(4.0, 4.4, len(months)), index=months), source="fred")
        store.upsert_series("US_CPI", pd.Series(100 * 1.025 ** (np.arange(len(months)) / 12), index=months), source="fred")
    monkeypatch.setenv("INVEST_DB", str(path))
    return path


@pytest.fixture
def canned(monkeypatch, seeded_db):
    """Canned Yahoo and FRED answers, no FRED key, and a record of every search and FRED fetch."""
    calls = {"yahoo": [], "fred": [], "fred_series": []}

    def search_yahoo(query):
        calls["yahoo"].append(query)
        return QUOTES.get(query.lower(), [])

    def fetch_close_history(symbol):
        if symbol == "BROKEN":
            raise LookupError("Yahoo Finance has no prices for BROKEN")
        days = DAYS[DAYS >= NEWCO_LISTED] if symbol == "NEWCO" else DAYS
        rng = np.random.default_rng(sum(map(ord, symbol)))
        return pd.Series(100 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days))), index=days, name=symbol)

    def fetch_symbol_meta(symbol):
        if symbol == "BROKEN":
            raise LookupError("Yahoo Finance does not know BROKEN")
        return {"name": NAMES.get(symbol, symbol), "currency": CURRENCIES.get(symbol, "USD"), "kind": "EQUITY"}

    def search_fred(query):
        calls["fred"].append(query)
        return parse_series_list(json.loads((FIX / "fred_series_search.json").read_text("utf-8")))

    def fetch_fred_series(series_id):
        calls["fred_series"].append(series_id)
        months = pd.date_range("2016-01-01", "2026-08-01", freq="MS")
        return pd.Series(np.linspace(300.0, 330.0, len(months)), index=months, name=series_id)

    def fetch_fred_meta(series_id):
        raise LookupError("FRED series details need a FRED_API_KEY")

    for name, fn in (("search_yahoo", search_yahoo), ("fetch_close_history", fetch_close_history),
                     ("fetch_symbol_meta", fetch_symbol_meta), ("search_fred", search_fred),
                     ("fetch_fred_series", fetch_fred_series), ("fetch_fred_meta", fetch_fred_meta)):
        monkeypatch.setattr(md, name, fn)
    monkeypatch.setattr(secrets, "fred_api_key", lambda: None)
    return calls


def _page(**query_params) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.query_params.update(query_params)
    return at.switch_page("pages/plotter.py").run()


def _search(at: AppTest, text: str, chart: int = 0) -> AppTest:
    return at.text_input(key=f"plotter_query_{chart}").input(text).run()


def _hits(at: AppTest, chart: int = 0) -> list:
    return [b for b in at.button if b.key.startswith(f"plotter_hit_{chart}_")]


def _figures(at: AppTest) -> list[dict]:
    return [json.loads(chart.proto.spec) for chart in at.get("plotly_chart")]


def _numbers(values) -> np.ndarray:
    """A trace's numbers, written by Plotly as a plain list or as a base64 typed array."""
    if isinstance(values, dict):
        return np.frombuffer(base64.b64decode(values["bdata"]), dtype=values["dtype"]).astype(float)
    return np.asarray(values, dtype=float)


# --- opening, searching, picking -------------------------------------------------------


def test_the_page_opens_on_one_empty_chart(canned):
    at = _page()
    assert not at.exception
    assert [t.key for t in at.text_input] == ["plotter_query_0"]
    assert not _figures(at)
    assert at.button(key="plotter_add_chart").label == "Add chart"
    # Without a FRED key, the page says how to reach FRED anyway.
    assert any("fred:CPIAUCSL" in c.value for c in at.caption)


def test_a_search_lists_what_it_finds_and_a_pick_charts_it(canned):
    at = _search(_page(), "apple")
    assert not at.exception
    labels = [b.label for b in _hits(at)]
    assert len(labels) == 2  # the option is left out: it expires
    assert labels[0].startswith("Apple Inc.") and "AAPL · Stock, NASDAQ" in labels[0]

    _hits(at)[0].click().run()
    assert not at.exception
    assert at.text_input(key="plotter_query_0").value == ""  # the search is done
    assert at.multiselect(key="plotter_series_0").value == ["AAPL"]
    (fig,) = _figures(at)
    assert [t["name"] for t in fig["data"]] == ["Apple Inc."]
    assert fig["layout"]["yaxis"]["title"]["text"] == "USD"
    assert fig["layout"]["showlegend"] is False  # one line: the chip above names it
    assert at.query_params["chart"] == ["AAPL"]


def test_a_series_already_on_the_chart_is_ticked_off_not_offered(canned):
    at = _search(_page(chart=["AAPL"]), "apple")
    aapl, aple = _hits(at)
    assert aapl.disabled and aapl.proto.icon == ":material/check:"
    assert not aple.disabled


def test_a_macro_series_is_found_in_the_catalog_and_read_from_the_database(canned):
    at = _search(_page(), "us unemployment")
    next(b for b in _hits(at) if b.label.startswith("US unemployment rate")).click().run()
    assert not at.exception
    (fig,) = _figures(at)
    assert fig["data"][0]["name"] == "US unemployment rate"
    assert fig["layout"]["yaxis"]["title"]["text"] == "%"
    assert _numbers(fig["data"][0]["y"])[-1] == pytest.approx(4.4)  # the stored value, as stored
    assert at.query_params["chart"] == ["macro:US_UNRATE"]


def test_a_catalog_series_is_charted_as_the_scorecard_reads_it(canned):
    """US CPI is stored as an index and read as its change on a year: 2.5% here, every month."""
    at = _page(chart=["macro:US_CPI"])
    (fig,) = _figures(at)
    np.testing.assert_allclose(_numbers(fig["data"][0]["y"]), 2.5, rtol=1e-9)


def test_fred_is_searched_only_with_a_key(canned, monkeypatch):
    at = _search(_page(), "unemployment")
    assert canned["fred"] == []
    assert not any("FRED" in b.label for b in _hits(at))

    monkeypatch.setattr(secrets, "fred_api_key", lambda: "test-key")
    at = _search(_page(), "unemployment")
    assert canned["fred"] == ["unemployment"]
    assert not any("fred:CPIAUCSL" in c.value for c in at.caption)  # nothing to explain once FRED is searchable
    next(b for b in _hits(at) if "UNRATE · FRED" in b.label).click().run()
    assert not at.exception
    assert canned["fred_series"] == ["UNRATE"]
    (fig,) = _figures(at)
    assert fig["data"][0]["name"] == "Unemployment Rate"  # what the search said, not asked again
    assert fig["layout"]["yaxis"]["title"]["text"] == "%"


def test_a_fred_id_typed_in_full_charts_without_a_key(canned):
    at = _search(_page(), "fred:CPIAUCSL")
    assert canned["yahoo"] == []  # a reference typed in full is not searched for
    (hit,) = _hits(at)
    hit.click().run()
    assert not at.exception
    assert canned["fred_series"] == ["CPIAUCSL"]
    assert _figures(at)[0]["data"][0]["name"] == "CPIAUCSL"  # without a key, its id is all FRED says


def test_a_search_source_that_fails_says_so_and_the_others_still_answer(canned, monkeypatch):
    def down(query):
        raise ConnectionError("Yahoo is down")

    monkeypatch.setattr(md, "search_yahoo", down)
    at = _search(_page(), "unemployment")
    assert not at.exception
    assert any(b.label.startswith("US unemployment rate") for b in _hits(at))
    assert any(c.value == "Yahoo Finance did not answer: Yahoo is down" for c in at.caption)


# --- the charts, in the session and in the URL -------------------------------------------


def test_the_charts_come_back_from_the_url(canned):
    at = _page(chart=["AAPL,MSFT", "macro:US_UNRATE"])
    assert not at.exception
    assert at.multiselect(key="plotter_series_0").value == ["AAPL", "MSFT"]
    assert at.multiselect(key="plotter_series_1").value == ["macro:US_UNRATE"]
    assert [[t["name"] for t in fig["data"]] for fig in _figures(at)] == [
        ["Apple Inc.", "Microsoft Corporation"],
        ["US unemployment rate"],
    ]
    assert canned["yahoo"] == []  # nothing searched: the URL says what each series is


def test_charts_are_added_and_removed(canned):
    at = _page(chart=["AAPL"])
    at.button(key="plotter_add_chart").click().run()
    assert [t.key for t in at.text_input] == ["plotter_query_0", "plotter_query_1"]
    _hits(_search(at, "microsoft", chart=1), chart=1)[0].click().run()
    assert at.query_params["chart"] == ["AAPL", "MSFT"]

    at.button(key="plotter_remove_0").click().run()
    assert not at.exception
    assert [t.key for t in at.text_input] == ["plotter_query_1"]  # the other chart keeps its widgets
    assert at.multiselect(key="plotter_series_1").value == ["MSFT"]
    assert at.query_params["chart"] == ["MSFT"]


def test_a_series_taken_off_leaves_the_others_their_colours(canned):
    at = _page(chart=["AAPL,MSFT,GOOG"])
    before = {t["name"]: t["line"]["color"] for t in _figures(at)[0]["data"]}
    assert list(before.values()) == list(charts.SERIES_COLORS["light"][:3])

    at.multiselect(key="plotter_series_0").unselect("AAPL").run()
    assert not at.exception
    after = {t["name"]: t["line"]["color"] for t in _figures(at)[0]["data"]}
    assert after == {name: colour for name, colour in before.items() if name != "Apple Inc."}
    assert at.query_params["chart"] == ["MSFT,GOOG"]

    _hits(_search(at, "apple"))[0].click().run()  # back again: it takes the free colour
    colours = {t["name"]: t["line"]["color"] for t in _figures(at)[0]["data"]}
    assert colours == before


def test_a_chart_holds_eight_series(canned):
    at = _page(chart=[",".join(f"S{n}" for n in range(9))])
    assert len(at.multiselect(key="plotter_series_0").value) == 8
    at = _search(at, "apple")
    assert not _hits(at)
    assert any("A chart holds 8 series" in c.value for c in at.caption)


# --- drawing -------------------------------------------------------------------------------


def test_values_in_different_units_get_a_panel_each_not_a_second_scale(canned):
    at = _page(chart=["AAPL,IWDA.AS,MSFT"])
    (fig,) = _figures(at)
    layout = fig["layout"]
    assert layout["yaxis"]["title"]["text"] == "USD" and layout["yaxis2"]["title"]["text"] == "EUR"
    assert [t["yaxis"] for t in fig["data"]] == ["y", "y2", "y"]  # the two USD prices share one
    assert layout["yaxis"]["domain"][0] > layout["yaxis2"]["domain"][1]  # stacked, not overlaid
    assert "overlaying" not in layout["yaxis2"]
    assert layout["xaxis"]["anchor"] == "y2"  # the dates under the bottom panel


def test_in_change_every_line_starts_at_zero_on_the_same_day(canned):
    at = _page(chart=["AAPL,NEWCO"], y_axis="Change")
    assert not at.exception
    (fig,) = _figures(at)
    for trace in fig["data"]:
        assert pd.Timestamp(trace["x"][0]) == pd.Timestamp(NEWCO_LISTED)
        assert _numbers(trace["y"])[0] == 0
        assert trace["yaxis"] == "y"
    assert fig["layout"]["yaxis"]["tickformat"] == "+.1~%"
    assert fig["layout"]["yaxis"]["title"]["text"] == "Change since 02 Mar 2026"
    assert "yaxis2" not in fig["layout"]  # one scale: every line is in %
    assert any("the first day in the range that all of them have data" in c.value for c in at.caption)


def test_the_grid_controls_work_as_on_the_dashboard(canned):
    at = _page(chart=["AAPL", "MSFT", "GOOG"], charts_per_row="3", chart_height="Large", time_range="Max")
    assert not at.exception
    assert at.button_group(key="charts_per_row").value == 3
    assert {round(c.weight, 6) for c in at.columns} == {round(1 / 3, 6)}
    figures = _figures(at)
    assert {fig["layout"]["height"] for fig in figures} == {charts.HEIGHTS["Large"]}
    assert {pd.Timestamp(fig["data"][0]["x"][0]) for fig in figures} == {DAYS[0]}  # Max: the whole history


def test_a_series_that_cannot_be_fetched_is_named_and_the_rest_drawn(canned):
    at = _page(chart=["AAPL,BROKEN"])
    assert not at.exception
    (fig,) = _figures(at)
    assert [t["name"] for t in fig["data"]] == ["Apple Inc."]
    assert any(c.value == "Not drawn: BROKEN, Yahoo Finance has no prices for BROKEN." for c in at.caption)


def test_without_a_database_a_macro_series_says_where_it_comes_from(canned, monkeypatch, tmp_path):
    monkeypatch.setenv("INVEST_DB", str(tmp_path / "absent.duckdb"))
    at = _page(chart=["macro:US_UNRATE"])
    assert not at.exception
    assert not _figures(at)
    assert any("no market database yet" in c.value for c in at.caption)
