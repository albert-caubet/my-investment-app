"""Headless tests for the Watchlist page, on a seeded temporary database and canned histories. No network.

The watchlist and a screener table live in a temporary DuckDB file; every Yahoo call the
page makes goes through ``market_data``, replaced here before each run.
"""

import base64
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import charts
import market_data as md
from invest.data.cache import Store

ROOT = Path(__file__).resolve().parents[1]
DAYS = pd.bdate_range("2020-01-02", "2026-10-02")
NEWCO_LISTED = "2026-03-02"
NAMES = {"MMM": "3M Company", "AAPL": "Apple Inc.", "SAP.DE": "SAP SE", "NEWCO": "Newco"}
CURRENCIES = {"SAP.DE": "EUR"}
SEEDED = ["MMM", "AAPL", "SAP.DE"]


def _history(symbol: str) -> pd.Series:
    days = DAYS[DAYS >= NEWCO_LISTED] if symbol == "NEWCO" else DAYS
    rng = np.random.default_rng(sum(map(ord, symbol)))
    return pd.Series(100 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days))), index=days, name=symbol)


@pytest.fixture(autouse=True)
def fresh_caches():
    """The page caches by symbol, and the watchlist by the file's modification time."""
    st.cache_data.clear()


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    path = tmp_path / "market.duckdb"
    with Store(path) as store:
        store.save_snapshot(store.start_run("fundamentals"), "screener", {"rows": [
            {"ticker": "MMM", "name": "3M CO", "value_conservative": 150.0, "mos_conservative": 0.25, "f_score": 7},
            {"ticker": "JPM", "name": "JPMORGAN CHASE & CO", "value_conservative": 210.0, "mos_conservative": -0.1,
             "f_score": 5},
        ]})
        for symbol in SEEDED:
            store.add_watch(symbol, note=f"seeded {symbol}")
    monkeypatch.setenv("INVEST_DB", str(path))
    return path


@pytest.fixture
def canned(monkeypatch, seeded_db):
    """Canned Yahoo histories and names, and a record of every history fetched."""
    fetched = []

    def fetch_close_history(symbol):
        fetched.append(symbol)
        if symbol == "BROKEN":
            raise LookupError("Yahoo Finance has no prices for BROKEN")
        return _history(symbol)

    def fetch_symbol_meta(symbol):
        if symbol == "BROKEN":
            raise LookupError("Yahoo Finance does not know BROKEN")
        return {"name": NAMES.get(symbol, symbol), "currency": CURRENCIES.get(symbol, "USD"), "kind": "EQUITY"}

    monkeypatch.setattr(md, "fetch_close_history", fetch_close_history)
    monkeypatch.setattr(md, "fetch_symbol_meta", fetch_symbol_meta)
    return fetched


def _page(**query_params) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.query_params.update(query_params)
    return at.switch_page("pages/watchlist.py").run()


def _figures(at: AppTest) -> list[dict]:
    return [json.loads(chart.proto.spec) for chart in at.get("plotly_chart")]


def _numbers(values) -> np.ndarray:
    """A trace's numbers, written by Plotly as a plain list or as a base64 typed array."""
    if isinstance(values, dict):
        return np.frombuffer(base64.b64decode(values["bdata"]), dtype=values["dtype"]).astype(float)
    return np.asarray(values, dtype=float)


def _watched(path: Path) -> list[str]:
    with Store(path, read_only=True) as store:
        return store.watchlist()["symbol"].tolist()


def _add(path: Path, symbol: str) -> None:
    with Store(path) as store:
        store.add_watch(symbol)


# --- the list --------------------------------------------------------------------------------


def test_the_list_shows_each_stock_with_its_prices_and_the_screeners_figures(canned):
    at = _page()
    assert not at.exception
    table = at.dataframe[0].value
    assert table["Symbol"].tolist() == SEEDED  # first added first
    assert table["Name"].tolist() == ["3M Company", "Apple Inc.", "SAP SE"]  # Yahoo's names
    assert table["Ccy"].tolist() == ["USD", "USD", "EUR"]
    assert table["Note"].tolist() == [f"seeded {s}" for s in SEEDED]

    mmm = _history("MMM")
    year = mmm[mmm.index >= pd.Timestamp("2025-10-02")]  # 1Y, the default time range
    row = table.iloc[0]
    assert row["Last close"] == pytest.approx(mmm.iloc[-1])
    assert row["Day"] == pytest.approx(mmm.iloc[-1] / mmm.iloc[-2] - 1)
    assert row["Change (1Y)"] == pytest.approx(year.iloc[-1] / year.iloc[0] - 1)
    # The screener's figures for the company in its table, none for the others.
    assert (row["Value / share"], row["Margin of safety"], row["F-score"]) == (150.0, 0.25, 7)
    assert table.iloc[1:][["Value / share", "Margin of safety", "F-score"]].isna().all().all()
    assert any("screener table built" in c.value for c in at.caption)


def test_the_change_in_the_list_follows_the_time_range(canned):
    at = _page(time_range="Max")
    table = at.dataframe[0].value
    mmm = _history("MMM")
    assert table.iloc[0]["Change (Max)"] == pytest.approx(mmm.iloc[-1] / mmm.iloc[0] - 1)


def test_a_european_stock_shows_the_european_tables_figures(canned, seeded_db):
    with Store(seeded_db) as store:
        store.save_snapshot(store.start_run("fundamentals"), "screener_eu", {"rows": [
            {"ticker": "SAP.DE", "name": "SAP SE", "value_conservative": 180.0, "mos_conservative": 0.1, "f_score": 8},
        ]})
    at = _page()
    assert not at.exception
    table = at.dataframe[0].value.set_index("Symbol")
    assert (table.loc["SAP.DE", "Value / share"], table.loc["SAP.DE", "F-score"]) == (180.0, 8)
    assert table.loc["MMM", "Value / share"] == 150.0  # the US table's, read as before
    assert any("(US) and" in c.value and "(EU)" in c.value for c in at.caption)


def test_without_a_screener_table_the_list_leaves_its_columns_out(canned, tmp_path, monkeypatch):
    path = tmp_path / "bare.duckdb"
    _add(path, "AAPL")
    monkeypatch.setenv("INVEST_DB", str(path))
    at = _page()
    assert not at.exception
    table = at.dataframe[0].value
    assert table["Symbol"].tolist() == ["AAPL"]
    assert "Value / share" not in table.columns and "Margin of safety" not in table.columns


def test_an_empty_watchlist_says_where_to_put_a_stock_on_it(canned, seeded_db):
    with Store(seeded_db) as store:
        for symbol in SEEDED:
            store.remove_watch(symbol)
    at = _page()
    assert not at.exception
    assert any("No stock is on the watchlist" in i.value for i in at.info)
    assert not _figures(at)
    assert canned == []  # nothing to fetch
    # the macro series' box is there, and says how to put one on
    assert at.selectbox(key="watchlist_add_series").value is None
    assert any("No macro series is on the watchlist" in c.value for c in at.caption)


def test_without_a_database_the_watchlist_is_empty_and_none_is_created(canned, tmp_path, monkeypatch):
    monkeypatch.setenv("INVEST_DB", str(tmp_path / "absent.duckdb"))
    at = _page()
    assert not at.exception
    assert any("No stock is on the watchlist" in i.value for i in at.info)
    assert any("No macro series is on the watchlist" in c.value for c in at.caption)
    assert not (tmp_path / "absent.duckdb").exists()


# --- the charts ------------------------------------------------------------------------------


def test_the_chart_controls_are_the_dashboards(canned):
    at = _page()
    assert not at.exception
    assert at.button_group(key="time_range").value == charts.DEFAULT_RANGE
    assert at.button_group(key="charts_per_row").value == 2
    assert at.button_group(key="chart_height").value == "Medium"
    assert at.button_group(key="y_axis").value == "Price"


def test_the_first_chart_has_every_stock_then_each_has_its_own_in_the_same_colour(canned):
    at = _page()
    together, *each = _figures(at)
    assert [t["name"] for t in together["data"]] == ["3M Company", "Apple Inc.", "SAP SE"]
    assert [fig["data"][0]["name"] for fig in each] == ["3M Company", "Apple Inc.", "SAP SE"]
    assert [fig["layout"]["showlegend"] for fig in each] == [False] * 3  # the heading above names it
    colours = [t["line"]["color"] for t in together["data"]]
    assert colours == list(charts.SERIES_COLORS["light"][:3])
    assert [fig["data"][0]["line"]["color"] for fig in each] == colours
    # In price, the two in dollars share a panel and the one in euros has its own.
    assert [t["yaxis"] for t in together["data"]] == ["y", "y", "y2"]
    assert together["layout"]["yaxis"]["title"]["text"] == "USD"
    assert together["layout"]["yaxis2"]["title"]["text"] == "EUR"
    assert {fig["layout"]["height"] for fig in [together, *each]} == {charts.HEIGHTS["Medium"]}


def test_the_grid_controls_lay_out_the_charts_as_on_the_dashboard(canned):
    at = _page(charts_per_row="3", chart_height="Large", time_range="Max")
    assert not at.exception
    assert {round(c.weight, 6) for c in at.columns} == {round(1 / 3, 6)}
    figures = _figures(at)
    assert len(figures) == 4
    assert {fig["layout"]["height"] for fig in figures} == {charts.HEIGHTS["Large"]}
    assert {pd.Timestamp(fig["data"][0]["x"][0]) for fig in figures} == {DAYS[0]}  # Max: the whole history


def test_in_change_every_line_starts_at_zero_with_the_range_or_on_its_first_close(canned, seeded_db):
    _add(seeded_db, "NEWCO")
    at = _page(y_axis="Change")
    assert not at.exception
    together, *each = _figures(at)
    starts = [pd.Timestamp(trace["x"][0]) for trace in together["data"]]
    assert starts == [pd.Timestamp("2025-10-02")] * 3 + [pd.Timestamp(NEWCO_LISTED)]  # Newco listed in the range
    assert all(_numbers(trace["y"])[0] == 0 for trace in together["data"])
    assert together["layout"]["yaxis"]["title"]["text"] == "Change since 02 Oct 2025"
    assert "yaxis2" not in together["layout"]  # one scale: every line is in %
    assert [pd.Timestamp(fig["data"][0]["x"][0]) for fig in each] == starts
    assert all(_numbers(fig["data"][0]["y"])[0] == 0 for fig in each)
    assert any("Newco" in c.value and "starts later" in c.value and "02 Mar 2026" in c.value for c in at.caption)


def test_a_stock_yahoo_cannot_price_is_named_and_the_rest_drawn(canned, seeded_db):
    _add(seeded_db, "BROKEN")
    at = _page()
    assert not at.exception
    together, *each = _figures(at)
    assert [t["name"] for t in together["data"]] == ["3M Company", "Apple Inc.", "SAP SE"]
    assert len(each) == 3
    captions = [c.value for c in at.caption]
    assert "Not drawn: BROKEN, Yahoo Finance has no prices for BROKEN." in captions
    assert "Not drawn: Yahoo Finance has no prices for BROKEN." in captions  # on its own chart
    assert pd.isna(at.dataframe[0].value.set_index("Symbol").loc["BROKEN", "Last close"])


def test_the_close_button_on_a_chart_takes_its_stock_off_the_watchlist(canned, seeded_db):
    at = _page()
    at.button(key="watchlist_remove_AAPL").click().run()
    assert not at.exception
    assert _watched(seeded_db) == ["MMM", "SAP.DE"]
    assert at.dataframe[0].value["Symbol"].tolist() == ["MMM", "SAP.DE"]
    assert len(_figures(at)) == 3
    assert [t.value for t in at.toast] == ["Apple Inc. is off the watchlist."]


def _company_links(at: AppTest) -> list[str]:
    """The query of each link to the Company financials page, in page order."""
    return [link.proto.query_string for link in at.get("page_link") if link.proto.page.endswith("company")]


def test_each_stocks_chart_links_to_its_financials_unless_yahoo_says_it_has_none(canned, monkeypatch):
    meta = md.fetch_symbol_meta
    monkeypatch.setattr(md, "fetch_symbol_meta", lambda s: {**meta(s), "kind": "ETF"} if s == "AAPL" else meta(s))
    at = _page()
    assert not at.exception
    assert _company_links(at) == ["company=MMM", "company=SAP.DE"]
    (link,) = [link for link in at.get("page_link") if link.proto.query_string == "company=MMM"]
    assert link.proto.label == "Financials" and "Company financials" in link.proto.help


# --- macro series ----------------------------------------------------------------------------

WEEKS = pd.date_range("2014-01-03", "2026-09-25", freq="W-FRI")
MONTHS = pd.date_range("2000-01-01", "2026-09-01", freq="MS")
NFCI = pd.Series(np.linspace(-0.2, -0.5, len(WEEKS)), index=WEEKS)
MARGIN_DEBT = pd.Series(np.linspace(400_000.0, 1_448_360.0, len(MONTHS)), index=MONTHS)
HY_OAS = pd.Series(np.linspace(5.0, 3.0, len(MONTHS)), index=MONTHS)  # what FRED sends, canned
HY_TITLE = "ICE BofA US High Yield Index Option-Adjusted Spread"
WATCHED = ["macro:US_NFCI", "macro:US_MARGIN_DEBT_LEVEL", "fred:BAMLH0A0HYM2"]
NAMES_OF_WATCHED = ["Chicago Fed financial conditions (NFCI)", "FINRA margin debt (customer debit balances)", HY_TITLE]


@pytest.fixture
def macro(seeded_db, monkeypatch):
    """Two catalog series stored with the NBER recessions, a FRED series canned, and the three on the watchlist.
    Returns the FRED ids fetched."""
    with Store(seeded_db) as store:
        store.upsert_series("US_NFCI", NFCI, source="fred")
        store.upsert_series("US_MARGIN_DEBT_LEVEL", MARGIN_DEBT, source="finra")
        usrec = pd.Series(0.0, index=MONTHS)
        usrec["2020-03-01":"2020-04-01"] = 1.0
        store.upsert_series("US_RECESSION", usrec, source="fred")
        for ref in WATCHED:
            store.add_watched_series(ref, note=f"seeded {ref}")
    fetched = []

    def fetch_fred_series(series_id):
        fetched.append(series_id)
        if series_id == "DOWN":
            raise LookupError("FRED did not answer")
        return HY_OAS.rename(series_id)

    monkeypatch.setattr(md, "fetch_fred_series", fetch_fred_series)
    monkeypatch.setattr(md, "fetch_fred_meta", lambda series_id: {"title": HY_TITLE, "units": "Percent"})
    return fetched


def _watched_series(path: Path) -> pd.DataFrame:
    with Store(path, read_only=True) as store:
        return store.watched_series()


def _macro_table(at: AppTest) -> pd.DataFrame:
    return at.dataframe[-1].value  # under the stocks' list


def _series_figures(at: AppTest) -> list[dict]:
    return [json.loads(c.proto.spec) for c in at.get("plotly_chart") if "watchlist_series_chart_" in c.proto.id]


def _change(series: pd.Series, years: int, digits: int) -> str:
    """The change a list shows for ``series`` over ``years`` back from its latest value."""
    shown = series[series.index >= series.index[-1] - pd.DateOffset(years=years)]
    return f"{shown.iloc[-1] - shown.iloc[0]:+,.{digits}f}"


def test_the_macro_series_are_listed_under_the_stocks_with_their_latest_values(canned, macro):
    at = _page()
    assert not at.exception
    assert [h.value for h in at.subheader] == ["Stocks", "Price history", "Macro series"]
    table = _macro_table(at)
    assert table["Series"].tolist() == NAMES_OF_WATCHED  # first added first
    # Decimals by size: three for a series that stays below one, none for $ millions, two between
    assert table["Latest"].tolist() == ["-0.500", "1,448,360", "3.00"]
    assert table["Unit"].tolist() == ["sd", "USD mn", "Percent"]
    assert table["As of"].tolist() == [date(2026, 9, 25), date(2026, 9, 1), date(2026, 9, 1)]
    # Over ten years, the macro charts' default range, in the series' own unit
    assert table["Change (10Y)"].tolist() == [_change(NFCI, 10, 3), _change(MARGIN_DEBT, 10, 0), _change(HY_OAS, 10, 2)]
    assert table["From"].tolist() == ["Macro catalog", "Macro catalog", "FRED"]
    assert table["Note"].tolist() == [f"seeded {ref}" for ref in WATCHED]
    assert macro == ["BAMLH0A0HYM2"]  # only the FRED series is fetched; the others are the database's


def test_each_macro_series_has_its_own_chart_with_the_recessions_shaded(canned, macro):
    at = _page()
    figures = _series_figures(at)
    assert [fig["data"][0]["name"] for fig in figures] == NAMES_OF_WATCHED
    assert [fig["data"][0]["line"]["color"] for fig in figures] == list(charts.SERIES_COLORS["light"][:3])
    nfci, margin_debt, _ = figures
    assert nfci["layout"]["yaxis"]["title"]["text"] == "sd"
    assert pd.Timestamp(nfci["data"][0]["x"][0]) == pd.Timestamp("2016-09-30")  # ten years back from 25 Sep 2026
    assert [(s["x0"][:7], s["yref"]) for s in nfci["layout"]["shapes"]] == [("2020-03", "paper")]
    assert margin_debt["layout"]["yaxis"]["exponentformat"] == "none"  # 1,400,000 USD mn, not 1.4M USD mn
    assert {fig["layout"]["height"] for fig in figures} == {charts.HEIGHTS["Medium"]}
    assert not any(fig["layout"]["showlegend"] for fig in figures)  # the heading above names it


def test_the_macro_charts_have_controls_of_their_own(canned, macro):
    at = _page(series_range="Max", series_per_row="3", series_height="Large")
    assert not at.exception
    assert at.button_group(key="time_range").value == charts.DEFAULT_RANGE  # the stocks' range is untouched
    figures = _series_figures(at)
    assert {fig["layout"]["height"] for fig in figures} == {charts.HEIGHTS["Large"]}
    assert pd.Timestamp(figures[1]["data"][0]["x"][0]) == MONTHS[0]  # Max: the whole history
    assert round(1 / 3, 6) in {round(c.weight, 6) for c in at.columns}
    assert "Change (Max)" in _macro_table(at).columns


def test_a_series_picked_in_the_box_goes_on_the_watchlist(canned, seeded_db):
    at = _page()
    at.selectbox(key="watchlist_add_series").set_value("macro:US_NFCI").run()
    assert not at.exception
    watched = _watched_series(seeded_db)
    assert watched["ref"].tolist() == ["macro:US_NFCI"]
    assert watched.iloc[0]["note"].startswith("from the Watchlist page on ")
    assert [t.value for t in at.toast] == ["Chicago Fed financial conditions (NFCI) is on the watchlist."]
    assert at.selectbox(key="watchlist_add_series").value is None  # emptied for the next one
    assert _macro_table(at)["Series"].tolist() == ["Chicago Fed financial conditions (NFCI)"]
    # nothing is stored for it in this database: listed all the same, and its chart says why it is empty
    assert "Not drawn: not stored yet; the refresh on the Macro page fetches it." in [c.value for c in at.caption]

    at.selectbox(key="watchlist_add_series").set_value("macro:US_NFCI").run()
    assert [t.value for t in at.toast] == ["Chicago Fed financial conditions (NFCI) is on the watchlist already."]
    assert len(_watched_series(seeded_db)) == 1


def test_the_close_button_on_a_macro_chart_takes_its_series_off(canned, macro, seeded_db):
    at = _page()
    at.button(key="watchlist_remove_series_macro:US_NFCI").click().run()
    assert not at.exception
    assert _watched_series(seeded_db)["ref"].tolist() == WATCHED[1:]
    assert [t.value for t in at.toast] == ["Chicago Fed financial conditions (NFCI) is off the watchlist."]
    assert len(_series_figures(at)) == 2
    assert _watched(seeded_db) == SEEDED  # the stocks stay


def test_with_no_stock_on_the_watchlist_its_macro_series_still_show(canned, macro, seeded_db):
    with Store(seeded_db) as store:
        for symbol in SEEDED:
            store.remove_watch(symbol)
    at = _page()
    assert not at.exception
    assert any("No stock is on the watchlist" in i.value for i in at.info)
    assert _macro_table(at)["Series"].tolist() == NAMES_OF_WATCHED
    assert len(_series_figures(at)) == 3
    assert canned == []  # no stock's prices fetched


def test_a_fred_series_that_does_not_answer_is_listed_and_the_rest_drawn(canned, macro, seeded_db):
    with Store(seeded_db) as store:
        store.add_watched_series("fred:DOWN")
    at = _page()
    assert not at.exception
    row = _macro_table(at).iloc[-1]
    assert row["Series"] == f"{HY_TITLE} (DOWN)"  # named apart from the other series FRED gives that title
    assert pd.isna(row["Latest"])
    assert "Not drawn: FRED did not answer." in [c.value for c in at.caption]
    assert len(_series_figures(at)) == 3
