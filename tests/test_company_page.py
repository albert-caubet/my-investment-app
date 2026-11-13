"""Headless tests for the Company financials page, on a seeded temporary database. No network.

A company in a seeded screener table is read from the database. Any other is fetched by the
real lookup (``invest.jobs.fundamentals.look_up_company``) on saved Yahoo answers and canned
prices, put in place of ``market_data.fetch_company``; Yahoo's search answers are canned too.
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

import company_detail
import market_data as md
import screener_help
from invest import secrets
from invest.data import yahoo
from invest.data.cache import Store
from invest.data.edgar import facts_frame
from invest.fundamentals.facts import tag_kinds
from invest.fundamentals.universe import Universe, yahoo_id
from invest.jobs import fundamentals as job

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
DAYS = pd.bdate_range("2016-01-01", "2026-09-09")
QUOTES = {
    "shell": [
        {"symbol": "SHEL.L", "quoteType": "EQUITY", "longname": "Shell plc", "exchDisp": "London"},
        {"symbol": "XLE", "quoteType": "ETF", "longname": "Energy Select Sector SPDR Fund", "exchDisp": "NYSE Arca"},
        {"symbol": "SHEL", "quoteType": "EQUITY", "longname": "Shell plc", "exchDisp": "NYSE"},
    ],
}
#: Closes for what the lookup downloads: Shell in pence, then pounds and euros per dollar.
CLOSES = {"SHEL.L": 3000.0, "GBP=X": 0.75, "EUR=X": 0.90}
#: 3M's fiscal years of revenue older than its saved facts (2019 on), so Max charts more years than 10Y.
OLD_YEARS = range(2008, 2019)


def _old_revenue(cik: int) -> pd.DataFrame:
    """A 10-K revenue figure for each of ``OLD_YEARS``, filed the February after."""
    return pd.DataFrame([
        {"fact_id": f"old-{cik}-{year}", "cik": cik, "taxonomy": "us-gaap", "tag": "Revenues", "unit": "USD",
         "start_date": date(year, 1, 1), "end_date": date(year, 12, 31), "value": 30e9, "fy": year, "fp": "FY",
         "form": "10-K", "filed_at": date(year + 1, 2, 15), "accession": f"old-{year}", "frame": None}
        for year in OLD_YEARS
    ])


@pytest.fixture(autouse=True)
def fresh_caches():
    """The page caches the tables by the file's modification time, which two tests can share."""
    st.cache_data.clear()


def _seed(path: Path) -> None:
    """A US table of two filers from saved EDGAR facts, and a European one of Unilever from saved Yahoo answers."""
    with Store(path) as store:
        store.upsert_series("EURUSD", pd.Series(1.12, index=DAYS), source="yahoo")
        us = Universe("examples", "Examples", ("MMM", "SAP"))
        for ticker, cik, name, sic in (("MMM", 66740, "3M CO", "3841"), ("SAP", 1000184, "SAP SE", "7372")):
            facts = json.loads((FIX / f"edgar_companyfacts_{ticker.lower()}.json").read_text(encoding="utf-8"))
            store.upsert_facts(facts_frame(facts))
            store.upsert_companies([{"cik": cik, "ticker": ticker, "name": name, "sic": sic,
                                     "sector": job.sector_from_sic(sic), "exchange": "NYSE"}])
            store.upsert_prices(ticker, pd.DataFrame({"close": 100.0, "adj_close": 100.0}, index=DAYS), currency="USD")
        store.upsert_facts(_old_revenue(66740))
        job.save_screener_snapshot(store, store.start_run("fundamentals"), us, job.build_metrics_table(store, us),
                                   as_of=None)
        eu = Universe("stoxx600", "STOXX Europe 600 (test)", ("ULVR.L",), point_in_time=False, region="EU",
                      source="yahoo")
        payload = json.loads((FIX / "yahoo_timeseries_ulvr_l.json").read_text(encoding="utf-8"))
        store.insert_new_facts(yahoo.fundamentals_facts(payload, yahoo_id("ULVR.L"), tag_kinds("yahoo"),
                                                        seen_on=date(2026, 9, 1)))
        store.upsert_companies([{"cik": yahoo_id("ULVR.L"), "ticker": "ULVR.L", "name": "Unilever PLC", "sic": None,
                                 "sector": "consumer defensive", "exchange": "LSE"}])
        store.upsert_prices("ULVR.L", pd.DataFrame({"close": 5000.0, "adj_close": 5000.0}, index=DAYS), currency="GBp")
        for symbol in ("GBP=X", "EUR=X"):
            store.upsert_prices(symbol, pd.DataFrame({"close": CLOSES[symbol]}, index=DAYS), currency=symbol[:3])
        job.save_screener_snapshot(store, store.start_run("fundamentals"), eu, job.build_metrics_table(store, eu),
                                   as_of=None)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    path = tmp_path / "market.duckdb"
    _seed(path)
    monkeypatch.setenv("INVEST_DB", str(path))
    monkeypatch.setattr(secrets, "sec_user_agent", lambda: "my-investment-app tests@example.com")
    return path


class FakeYahoo:
    """Yahoo's saved answers for Shell, quoted in pence with accounts in dollars; it knows no other company."""

    def profile(self, symbol):
        if symbol != "SHEL.L":
            return {}
        return {"name": "Shell plc", "sector": "Energy", "currency": "GBp", "financial_currency": "USD",
                "exchange": "LSE"}

    def fundamentals(self, symbol, kinds):
        return json.loads((FIX / "yahoo_timeseries_shel_l.json").read_text(encoding="utf-8"))


@pytest.fixture
def canned(monkeypatch, seeded_db):
    """Canned search answers, and every fetch of a company through the real lookup on Yahoo's saved
    answers. Returns the symbols fetched."""
    fetched = []

    def fetch_company(symbol):
        fetched.append(symbol)
        return job.look_up_company(symbol, yahoo_client=FakeYahoo())

    def fetch_history(symbols, period="12y"):
        return {s: pd.DataFrame({"close": CLOSES[s], "adj_close": CLOSES[s]}, index=DAYS) for s in symbols if s in CLOSES}

    monkeypatch.setattr(md, "search_yahoo", lambda query: QUOTES.get(query.lower(), []))
    monkeypatch.setattr(md, "fetch_company", fetch_company)
    monkeypatch.setattr(job.yahoo, "fetch_history", fetch_history)
    return fetched


def _page(**query_params) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.query_params.update(query_params)
    return at.switch_page("pages/company.py").run()


def _captions(at: AppTest) -> str:
    return " ".join(c.value for c in at.caption)


def _chart_titles(at: AppTest) -> list[str]:
    """The bold title above each of the detail's charts, in page order."""
    return [m.value.strip("*") for cell in at.columns for m in cell.markdown
            if m.value.startswith("**") and cell.get("plotly_chart")]


def _watched(path: Path) -> pd.DataFrame:
    with Store(path, read_only=True) as store:
        return store.watchlist()


def test_the_page_opens_on_a_search_box_and_asks_for_a_company(canned):
    at = _page()
    assert not at.exception
    assert at.title[0].value == "Company financials"
    assert at.text_input(key="company_query").proto.placeholder == "Find a company: name, ticker or ISIN"
    assert any("Search for a company" in i.value for i in at.info)
    assert not at.metric and not at.get("plotly_chart")
    assert "a US filer's accounts from SEC EDGAR" in _captions(at)


def test_a_search_lists_stocks_only_and_a_pick_shows_it_and_keeps_it_in_the_url(canned):
    at = _page()
    at.text_input(key="company_query").input("shell").run()
    hits = [b for b in at.button if b.key.startswith("company_hit_")]
    assert len(hits) == 2 and all(b.label.startswith("Shell plc") for b in hits)  # not the ETF
    next(b for b in hits if "SHEL.L" in b.label).click().run()
    assert not at.exception
    assert at.query_params["company"] == ["SHEL.L"]
    assert at.text_input(key="company_query").value == ""  # this search is done
    assert canned == ["SHEL.L"]
    assert at.subheader[0].value == "Shell plc (SHEL.L)"


def test_a_search_marks_the_company_shown(canned):
    at = _page(company="SHEL.L")
    at.text_input(key="company_query").input("shell").run()
    (shown,) = [b for b in at.button if b.key.startswith("company_hit_") and b.disabled]
    assert "SHEL.L" in shown.label


def test_a_search_yahoo_does_not_answer_says_so(canned, monkeypatch):
    def down(query):
        raise ConnectionError("https://query2.finance.yahoo.com/v1/finance/search: timed out")

    monkeypatch.setattr(md, "search_yahoo", down)
    at = _page()
    at.text_input(key="company_query").input("shell").run()
    assert not at.exception
    assert "Yahoo Finance did not answer: timed out" in _captions(at)


def test_a_company_in_a_screener_table_is_read_from_the_database_not_fetched(canned):
    at = _page(company="MMM")
    assert not at.exception
    assert not canned
    assert at.subheader[0].value == "3M CO (MMM)"
    captions = _captions(at)
    assert "Medical equipment · As the screener table of the Examples has it, built " in captions
    assert "not point-in-time" not in captions
    metrics = {m.label: m.value for m in at.metric}
    assert list(metrics) == ["Price", "Market cap", "EPV / share", "DCF / share", "Margin of safety", "Data to"]
    assert metrics["Price"] == "100.00 USD"
    titles = _chart_titles(at)
    assert titles[:3] == ["Share price", "Revenue and profit", "Margins"]
    assert "as filed, latest restatement" in captions


def test_a_european_company_in_its_table_says_it_is_not_point_in_time(canned):
    at = _page(company="ULVR.L")
    assert not at.exception
    assert not canned
    assert "As the screener table of the STOXX Europe 600 (test) has it, built " in _captions(at)
    assert ", not point-in-time." in _captions(at)
    assert {m.label: m.value for m in at.metric}["Price"] == "5,000.00 GBp"
    assert any("Yahoo Finance item" in d.value.columns for d in at.dataframe)


def test_a_company_in_no_table_is_fetched_and_says_so(canned):
    at = _page(company="shel.l")
    assert not at.exception
    assert canned == ["SHEL.L"]
    captions = _captions(at)
    assert ("Energy · Fetched from Yahoo Finance: four or five fiscal years, not point-in-time. In no screener "
            "table, so it has no rank; what is fetched here is kept for half a day and stored nowhere.") in captions
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Price"] == "3,000.00 GBp"
    assert metrics["Market cap"].endswith(" bn USD") and metrics["EPV / share"].endswith(" USD")  # Shell's accounts
    assert "Revenue and profit" in _chart_titles(at)
    assert "as Yahoo Finance gives it, restatements included" in captions
    (items,) = [d.value for d in at.dataframe if "Yahoo Finance item" in d.value.columns]
    assert items.set_index("field").loc["revenue", "Yahoo Finance item"] == "TotalRevenue"


def test_the_measures_are_in_a_row_with_the_screeners_help(canned):
    at = _page(company="SHEL.L")
    (measures,) = [d for d in at.dataframe if "EBIT/EV" in d.value.columns]
    labels = [label for _, label, _ in company_detail.MEASURES]
    assert list(measures.value.columns) == labels and len(measures.value) == 1
    config = json.loads(measures.proto.columns)
    assert all(config[label]["help"] == screener_help.COLUMN_HELP[label] for label in labels)
    assert measures.proto.placeholder == "–"


def test_the_screener_shows_no_measures_row_of_its_own(canned):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run().switch_page("pages/screener.py").run()
    assert not at.exception
    assert sum("EBIT/EV" in d.value.columns for d in at.dataframe) == 1  # its list's


def test_a_company_nothing_has_accounts_for_says_why(canned):
    at = _page(company="NOPE")
    assert not at.exception
    assert canned == ["NOPE"]
    (warning,) = at.warning
    assert warning.value == "No accounts for NOPE.\n\nNOPE: Yahoo has no quote for this symbol."
    assert not at.metric


def test_without_the_sec_setting_the_page_says_where_a_us_filers_accounts_come_from(canned, monkeypatch):
    monkeypatch.setattr(secrets, "sec_user_agent", lambda: None)
    at = _page()
    captions = _captions(at)
    assert "its accounts from Yahoo Finance." in captions
    assert "`SEC_USER_AGENT`" in captions and "ten years and more" in captions


def test_the_company_goes_on_the_watchlist_with_its_cik_and_off_it(canned, seeded_db):
    at = _page(company="MMM")
    at.button(key="watch_add").click().run()
    assert not at.exception
    entry = _watched(seeded_db).set_index("symbol").loc["MMM"]
    assert entry["cik"] == 66740
    assert entry["note"].startswith("from the Company financials page on ")
    assert [t.value for t in at.toast] == ["3M CO (MMM) is on the watchlist."]
    at.button(key="watch_remove").click().run()
    assert _watched(seeded_db).empty


def test_a_fetched_company_goes_on_the_watchlist_without_a_cik(canned, seeded_db):
    at = _page(company="SHEL.L")
    at.button(key="watch_add").click().run()
    assert not at.exception
    assert pd.isna(_watched(seeded_db).set_index("symbol").loc["SHEL.L", "cik"])


def test_without_a_database_a_company_is_fetched(canned, tmp_path, monkeypatch):
    monkeypatch.setenv("INVEST_DB", str(tmp_path / "none.duckdb"))
    at = _page(company="SHEL.L")
    assert not at.exception
    assert canned == ["SHEL.L"]
    assert {m.label: m.value for m in at.metric}["Price"] == "3,000.00 GBp"


# --- the time range -------------------------------------------------------------------------


def _figure(at: AppTest, title: str) -> dict:
    """The detail's chart under the bold ``title``."""
    for cell in at.columns:
        titles = [m.value.strip("*") for m in cell.markdown if m.value.startswith("**")]
        figures = cell.get("plotly_chart")
        if titles == [title] and len(figures) == 1:
            return json.loads(figures[0].proto.spec)
    raise AssertionError(f"no chart titled {title}")


def _years(fig: dict) -> list[int]:
    """The fiscal years on a history chart, from every line."""
    years = set()
    for trace in fig["data"]:
        x = trace["x"]
        if isinstance(x, dict):  # Plotly's base64 typed array
            x = np.frombuffer(base64.b64decode(x["bdata"]), dtype=x["dtype"])
        years |= {int(v) for v in x}
    return sorted(years)


def test_the_time_range_charts_ten_fiscal_years_by_default(canned):
    at = _page(company="MMM")
    control = at.button_group(key="company_detail_range")
    assert control.value == "10Y" and control.options == ["5Y", "10Y", "Max"]
    assert "Yahoo Finance" in control.help and "10Y and Max show no more" in control.help
    # the ten fiscal years to 2025: 2026 has only the share count 3M's last 10-K gives as of January
    assert _years(_figure(at, "Revenue and profit")) == list(range(2016, 2026))
    assert _figure(at, "Share price")["data"][0]["x"][0].startswith("2016-01-01")  # from the first fiscal year


@pytest.mark.parametrize("label, years, price_from", [
    ("5Y", range(2021, 2026), "2021-01-01"),
    ("Max", range(2008, 2026), "2016-01-01"),  # every year stored; the prices begin in 2016
])
def test_the_time_range_sets_the_years_of_every_chart(canned, label, years, price_from):
    at = _page(company="MMM")
    at.button_group(key="company_detail_range").set_value(label).run()
    assert not at.exception
    assert _years(_figure(at, "Revenue and profit")) == list(years)
    shares = _years(_figure(at, "Shares outstanding"))
    assert shares[0] >= years[0] and shares[-1] == 2026  # the January count after the last year end, kept
    assert _figure(at, "Share price")["data"][0]["x"][0].startswith(price_from)


def test_the_time_range_is_read_from_the_url(canned):
    at = _page(company="MMM", company_detail_range="5Y")
    assert not at.exception
    assert at.button_group(key="company_detail_range").value == "5Y"
    assert _years(_figure(at, "Revenue and profit")) == list(range(2021, 2026))


def test_a_company_from_yahoo_has_its_four_years_whatever_the_range(canned):
    at = _page(company="SHEL.L")
    for label in ("5Y", "Max"):
        at.button_group(key="company_detail_range").set_value(label).run()
        assert _years(_figure(at, "Revenue and profit")) == [2022, 2023, 2024, 2025]
