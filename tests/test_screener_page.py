"""Headless smoke test for the Screener page on a seeded temporary database."""

import json
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import charts
from invest.data.cache import Store
from invest.data.edgar import facts_frame
from invest.fundamentals.universe import Universe
from invest.jobs import fundamentals as job

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"


def _seed(path: Path) -> None:
    days = pd.bdate_range("2016-01-01", "2026-09-09")
    rng = np.random.default_rng(5)
    universe = Universe("examples", "Examples", ("MMM", "JPM", "SAP"))
    with Store(path) as store:
        store.upsert_series("EURUSD", pd.Series(1.12, index=days), source="yahoo")
        for ticker, cik, name, sic in (("MMM", 66740, "3M CO", "3841"), ("JPM", 19617, "JPMORGAN CHASE & CO", "6021"),
                                       ("SAP", 1000184, "SAP SE", "7372")):
            facts = facts_frame(json.loads((FIX / f"edgar_companyfacts_{ticker.lower()}.json").read_text(encoding="utf-8")))
            store.upsert_facts(facts)
            store.upsert_companies([{"cik": cik, "ticker": ticker, "name": name, "sic": sic,
                                     "sector": job.sector_from_sic(sic), "exchange": "NYSE"}])
            prices = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days)))
            store.upsert_prices(ticker, pd.DataFrame({"close": prices, "adj_close": prices}, index=days), currency="USD")
        table = job.build_metrics_table(store, universe)
        run = store.start_run("fundamentals")
        job.save_screener_snapshot(store, run, universe, table, as_of=None)
        store.add_watch("MMM", cik=66740, note="seeded")


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    path = tmp_path / "market.duckdb"
    _seed(path)
    monkeypatch.setenv("INVEST_DB", str(path))
    return path


def _screener(at: AppTest) -> AppTest:
    return at.switch_page("pages/screener.py").run()


def test_screener_page_renders_ranked_table_and_detail(seeded_db):
    at = _screener(AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run())
    assert not at.exception
    headers = [h.value for h in at.subheader]
    assert any(h.startswith("Magic formula") for h in headers)
    assert "Company detail" in headers
    assert "Watchlist" not in headers  # it has a page of its own
    # the bank is excluded from the magic formula by default; the two others rank
    first_table = at.dataframe[0].value
    assert "JPM" not in first_table["Ticker"].tolist()
    assert set(first_table["Ticker"]) <= {"MMM", "SAP"}
    columns = list(first_table.columns)
    assert columns.index("EV/EBITDA") == columns.index("EBIT/EV") + 1
    assert any("survivorship" in c.value.lower() for c in at.caption)
    assert "Rebuild now" in [b.label for b in at.button]


def test_screener_page_switches_screen(seeded_db):
    at = _screener(AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run())
    at.selectbox[0].select("Shareholder yield").run()
    assert not at.exception
    assert any(h.startswith("Shareholder yield") for h in [h.value for h in at.subheader])


def test_screener_page_without_a_table_explains_what_to_run(tmp_path, monkeypatch):
    path = tmp_path / "empty.duckdb"
    with Store(path):
        pass
    monkeypatch.setenv("INVEST_DB", str(path))
    at = _screener(AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run())
    assert not at.exception
    assert any("invest.jobs.fundamentals" in i.value for i in at.info)


def _app() -> AppTest:
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)


def _company(at: AppTest):
    return next(s for s in at.selectbox if s.label == "Company")


def _ticked(*tickers: str) -> AppTest:
    """The Screener opened from a URL that ticks ``tickers`` in the ranked list."""
    at = _app().run().switch_page("pages/screener.py")
    at.query_params["screener_chart"] = ",".join(tickers)
    at.run()
    assert not at.exception
    return at


def _chart_cells(at: AppTest) -> list:
    """The expanders holding a company's chart, in page order."""
    return [e for e in at.expander if e.label.startswith("📈")]


def _figure(element) -> dict:
    (chart,) = element.get("plotly_chart")
    return json.loads(chart.proto.spec)


def _list_key(at: AppTest) -> str:
    """The ranked list's key, which names the tickers it lists."""
    tickers = at.dataframe[0].value["Ticker"].tolist()
    return f"screener_list_{zlib.crc32(','.join(tickers).encode())}"


def test_the_charts_ask_for_companies_to_be_ticked(seeded_db):
    at = _screener(_app().run())
    assert not at.exception
    assert "Charts" in [h.value for h in at.subheader]
    assert any("Tick one or more companies" in i.value for i in at.info)
    # no price chart: the detail's charts are of its accounts
    assert not [c for c in at.get("plotly_chart") if "Daily" in c.proto.spec or "(MMM)" in c.proto.spec]
    assert at.button_group(key="screener_range").value == "1Y"
    assert at.button_group(key="screener_layout").value == "Separate"
    assert at.button_group(key="screener_y_axis").value == "Price"


def test_companies_ticked_in_the_url_are_charted_apart_over_the_last_year(seeded_db):
    at = _ticked("SAP", "MMM")
    assert at.session_state[_list_key(at)]["selection"]["rows"]
    # in the URL's order, which is the order they were ticked in, not the rank
    assert [e.label for e in _chart_cells(at)] == ["📈 SAP SE (SAP)", "📈 3M CO (MMM)"]
    sap, mmm = (_figure(e) for e in _chart_cells(at))
    assert [t["name"] for t in sap["data"]] == ["SAP SE (SAP)"]
    assert sap["data"][0]["line"]["color"] == charts.SERIES_COLORS["light"][0]
    assert mmm["data"][0]["line"]["color"] == charts.SERIES_COLORS["light"][1]
    assert sap["layout"]["yaxis"]["title"]["text"] == "USD"
    assert sap["data"][0]["x"][0].startswith("2025-09-09")  # a year back from the last close
    assert sap["layout"]["height"] == charts.HEIGHTS["Medium"]
    assert " from 09 Sep 2025 to 09 Sep 2026." in " ".join(c.value for c in _chart_cells(at)[0].caption)


def test_ticking_a_company_charts_it_and_puts_it_in_the_url(seeded_db):
    at = _screener(_app().run())
    key = _list_key(at)
    rank = at.dataframe[0].value["Ticker"].tolist()
    at.session_state[key] = {"selection": {"rows": [rank.index("MMM")], "columns": [], "cells": []}}
    at.run()
    assert not at.exception
    assert at.query_params["screener_chart"] == ["MMM"]
    assert [e.label for e in _chart_cells(at)] == ["📈 3M CO (MMM)"]


def test_together_in_change_draws_one_chart_from_a_common_start(seeded_db):
    at = _ticked("MMM", "SAP")
    at.button_group(key="screener_layout").set_value("Together").run()
    at.button_group(key="screener_y_axis").set_value("Change").run()
    assert not at.exception
    assert not _chart_cells(at)
    assert at.button_group(key="screener_per_row").proto.disabled
    (fig,) = [json.loads(c.proto.spec) for c in at.get("plotly_chart") if "3M CO (MMM)" in c.proto.spec]
    assert [t["name"] for t in fig["data"]] == ["3M CO (MMM)", "SAP SE (SAP)"]
    assert fig["layout"]["yaxis"]["tickformat"] == "+.1~%"
    assert fig["layout"]["showlegend"]
    assert fig["layout"]["height"] > charts.HEIGHTS["Medium"]  # the legend's row on top
    assert all(t["x"][0].startswith("2025-09-09") for t in fig["data"])


@pytest.mark.parametrize("per_row", [1, 3])
def test_charts_per_row_height_and_range_set_every_chart(seeded_db, per_row):
    at = _ticked("MMM", "SAP")
    at.button_group(key="screener_per_row").set_value(per_row).run()
    at.button_group(key="screener_height").set_value("Large").run()
    at.button_group(key="screener_range").set_value("Max").run()
    assert not at.exception
    cells = [c for c in at.columns if any(e.label.startswith("📈") for e in c.expander)]
    assert len(cells) == 2 and {round(c.weight, 6) for c in cells} == {round(1 / per_row, 6)}
    figures = [_figure(e) for e in _chart_cells(at)]
    assert {f["layout"]["height"] for f in figures} == {charts.HEIGHTS["Large"]}
    assert {f["data"][0]["x"][0][:10] for f in figures} == {"2016-01-01"}


def test_a_company_the_screen_leaves_out_is_not_charted(seeded_db):
    at = _ticked("JPM", "MMM")  # the bank: excluded from the magic formula by default
    assert [e.label for e in _chart_cells(at)] == ["📈 3M CO (MMM)"]
    assert at.query_params["screener_chart"] == ["MMM"]


def test_the_chart_choices_are_read_from_the_url(seeded_db):
    at = _app().run().switch_page("pages/screener.py")
    at.query_params["screener_chart"] = "MMM"
    at.query_params["screener_range"] = "5Y"
    at.query_params["screener_per_row"] = "1"
    at.query_params["screener_y_axis"] = "Change"
    at.run()
    assert not at.exception
    assert at.button_group(key="screener_range").value == "5Y"
    assert at.button_group(key="screener_per_row").value == 1
    assert at.button_group(key="screener_y_axis").value == "Change"
    (cell,) = _chart_cells(at)
    assert _figure(cell)["layout"]["yaxis"]["tickformat"] == "+.1~%"


def _watched(path: Path) -> pd.DataFrame:
    with Store(path, read_only=True) as store:
        return store.watchlist()


def test_the_company_in_the_detail_goes_on_the_watchlist_and_off_it(seeded_db):
    at = _screener(AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run())
    _company(at).select("SAP").run()
    assert not at.exception
    assert not [b for b in at.button if b.key == "watch_remove"]
    at.button(key="watch_add").click().run()
    assert not at.exception
    sap = _watched(seeded_db).set_index("symbol").loc["SAP"]
    assert sap["cik"] == 1000184
    assert sap["note"].startswith("from magic_formula on ")
    assert [t.value for t in at.toast] == ["SAP SE (SAP) is on the watchlist."]

    at.button(key="watch_remove").click().run()
    assert not at.exception
    assert _watched(seeded_db)["symbol"].tolist() == ["MMM"]  # as seeded
    assert at.button(key="watch_add").label == "Add to watchlist"


def test_a_company_already_on_the_watchlist_offers_to_take_it_off(seeded_db):
    at = _screener(AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run())
    _company(at).select("MMM").run()  # seeded on the watchlist
    assert not at.exception
    assert at.button(key="watch_remove").label == "Remove from watchlist"


# --- fiscal-year history ---------------------------------------------------------------


def _history_figures(at: AppTest) -> dict[str, dict]:
    """The detail's history charts, by the bold title above each."""
    out = {}
    for cell in at.columns:
        titles = [m.value.strip("*") for m in cell.markdown if m.value.startswith("**")]
        figures = cell.get("plotly_chart")
        if len(titles) == 1 and len(figures) == 1:
            out[titles[0]] = json.loads(figures[0].proto.spec)
    return out


def _values(trace: dict) -> np.ndarray:
    values = trace["y"]
    if isinstance(values, dict):  # Plotly's base64 typed array
        import base64
        return np.frombuffer(base64.b64decode(values["bdata"]), dtype=values["dtype"]).astype(float)
    return np.asarray([np.nan if v is None else v for v in values], dtype=float)


def test_the_detail_charts_the_history_in_millions_of_the_accounts_currency(seeded_db):
    at = _screener(_app().run())
    _company(at).select("MMM").run()
    assert not at.exception
    figures = _history_figures(at)
    assert list(figures)[:4] == ["Share price", "Revenue and profit", "Margins", "Costs, interest and tax"]
    price = figures["Share price"]
    assert price["layout"]["yaxis"]["title"]["text"] == "USD"  # a price, not in millions
    first_year = int(np.nanmin(_values({"y": figures["Revenue and profit"]["data"][0]["x"]})))
    assert price["data"][0]["x"][0][:4] == str(first_year)  # over the years of the history
    assert "Shares outstanding" in figures and "Diluted EPS" in figures
    revenue = figures["Revenue and profit"]
    assert revenue["layout"]["yaxis"]["title"]["text"] == "USD m"
    assert revenue["layout"]["yaxis"]["tickformat"] == ",~f"
    assert [t["name"] for t in revenue["data"]][0] == "Revenue"
    np.testing.assert_allclose(np.nanmax(_values(revenue["data"][0])), 35_355, rtol=0.2)  # 3M's revenue, about $35bn
    margins = _values(figures["Margins"]["data"][0])
    assert np.nanmin(margins) > 1 and np.nanmax(margins) < 100  # in %, not fractions
    assert figures["Shares outstanding"]["layout"]["yaxis"]["title"]["text"] == "Shares, m"
    assert figures["Diluted EPS"]["layout"]["yaxis"]["title"]["text"] == "USD per share"
    assert np.nanmax(_values(figures["Diluted EPS"]["data"][0])) < 100  # per share, not scaled
    # no table of the history any more: the only tables are the list and the tags
    assert not any("revenue" in map(str, d.value.index) for d in at.dataframe)


def test_the_history_of_accounts_in_euros_says_so(seeded_db):
    at = _screener(_app().run())
    _company(at).select("SAP").run()
    assert not at.exception
    assert _history_figures(at)["Revenue and profit"]["layout"]["yaxis"]["title"]["text"] == "EUR m"
    assert any("millions of EUR" in c.value for c in at.caption)


# --- help bubbles ----------------------------------------------------------------------

#: The ranked list's measures, from EBIT/EV on, as each screen shows them.
MEASURES = ["EBIT/EV", "ROIC", "FCF yield", "F", "Accruals", "GM slope/yr", "P/B", "P/E", "EV/EBITDA",
            "ND/EBITDA", "Sh. yield", "Margin of safety", "Data to", "Basis"]


def _column_help(at: AppTest) -> dict[str, str]:
    """The ranked list's help per column, as the page configures it."""
    config = json.loads(at.dataframe[0].proto.columns or "{}")
    return {label: column.get("help") for label, column in config.items() if column.get("help")}


@pytest.mark.parametrize("screen, extra", [("Magic formula (earnings yield + ROIC)", "Rank sum"),
                                           ("Quality-value composite", "Composite z")])
def test_every_measure_in_the_list_has_a_help_bubble(seeded_db, screen, extra):
    at = _screener(_app().run())
    at.selectbox[0].select(screen).run()
    assert not at.exception
    shown = at.dataframe[0].value.columns.tolist()
    helped = _column_help(at)
    measures = [c for c in shown[shown.index("EBIT/EV"):]]
    assert extra in measures
    assert set(measures) <= set(helped), set(measures) - set(helped)
    assert not {"Rank", "Ticker", "Name", "Sector"} & set(helped)  # the columns before EBIT/EV need none
    assert "Greenblatt" in helped["EBIT/EV"] and "Piotroski" in helped["F"]
    config = json.loads(at.dataframe[0].proto.columns)
    assert all(config[label]["label"] == label for label in measures)  # no mark: hovering shows the help
    assert at.dataframe[0].proto.placeholder == "–"  # not "None" in a column given help


def test_every_measure_has_a_text(seeded_db):
    import screener_help

    assert set(MEASURES) | {"Rank sum", "Composite z"} == set(screener_help.COLUMN_HELP)
    assert all(len(text) > 300 for text in screener_help.COLUMN_HELP.values())  # an explanation, not a label


def test_the_screen_dropdown_and_the_filters_explain_themselves(seeded_db):
    at = _screener(_app().run())
    screen = at.selectbox[0]
    for name in ("Magic formula", "Quality-value composite", "Deep value", "Shareholder yield"):
        assert f"**{name}" in screen.help
    helps = {c.label: c.help for c in at.checkbox}
    assert "banks" in helps["Exclude financials"]
    assert "net debt above 4 times EBITDA" in helps["Filter value traps"]
    assert "billions" in at.number_input[0].help


def test_the_detail_shows_epv_and_dcf_with_their_currencies(seeded_db):
    at = _screener(_app().run())
    _company(at).select("SAP").run()
    assert not at.exception
    metrics = {m.label: m for m in at.metric}
    assert "Conservative value / share" not in metrics
    assert list(metrics)[:6] == ["Price", "Market cap", "EPV / share", "DCF / share", "Margin of safety", "Data to"]
    assert metrics["Price"].value.endswith(" USD")  # the US listing
    assert metrics["Market cap"].value.endswith(" bn EUR")  # SAP's accounts
    assert "shares outstanding" in metrics["Market cap"].proto.help
    for label in ("EPV / share", "DCF / share"):
        value = metrics[label].value
        assert value == "–" or value.endswith(" EUR")  # SAP's accounts
    assert "Greenwald" in metrics["EPV / share"].proto.help
    assert "terminal value" in metrics["DCF / share"].proto.help
    assert "lower" in metrics["Margin of safety"].proto.help
