"""Headless smoke test for the Screener page on a seeded temporary database."""

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

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


def test_screener_page_renders_ranked_table_detail_and_watchlist(seeded_db):
    at = _screener(AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run())
    assert not at.exception
    headers = [h.value for h in at.subheader]
    assert any(h.startswith("Magic formula") for h in headers)
    assert "Company detail" in headers and "Watchlist" in headers
    # the bank is excluded from the magic formula by default; the two others rank
    first_table = at.dataframe[0].value
    assert "JPM" not in first_table["Ticker"].tolist()
    assert set(first_table["Ticker"]) <= {"MMM", "SAP"}
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


def _chart_layout(at: AppTest) -> dict:
    charts_shown = at.get("plotly_chart")
    assert len(charts_shown) == 1
    return json.loads(charts_shown[0].proto.spec)["layout"]


def _company(at: AppTest):
    return next(s for s in at.selectbox if s.label == "Company")


def test_screener_page_charts_the_picked_companys_price_over_the_last_year(seeded_db):
    at = _screener(AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run())
    assert not at.exception
    company = _company(at)
    pick = company.value
    name = {"MMM": "3M CO", "SAP": "SAP SE"}[pick]  # as seeded; the bank is not ranked
    assert sorted(company.options) == ["3M CO (MMM)", "SAP SE (SAP)"]
    layout = _chart_layout(at)
    assert layout["title"]["text"].startswith(f"{name} ({pick}): ")
    assert layout["yaxis"]["title"]["text"] == "Daily close (USD)"
    assert at.button_group(key="price_range").value == "1Y"
    assert at.slider(key="price_window").value == (date(2025, 9, 9), date(2026, 9, 9))


def test_a_range_button_sets_the_slider_and_dragging_the_slider_clears_the_button(seeded_db):
    at = _screener(AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run())
    at.button_group(key="price_range").set_value("Max").run()
    assert not at.exception
    assert at.slider(key="price_window").value == (date(2016, 1, 1), date(2026, 9, 9))

    at.slider(key="price_window").set_value((date(2020, 1, 1), date(2021, 6, 30))).run()
    assert not at.exception
    assert at.button_group(key="price_range").value is None
    assert at.slider(key="price_window").value == (date(2020, 1, 1), date(2021, 6, 30))


def test_the_range_carries_over_to_the_next_company(seeded_db):
    at = _screener(AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run())
    at.slider(key="price_window").set_value((date(2020, 1, 1), date(2021, 6, 30))).run()
    company = _company(at)
    other = next(t for t in ("MMM", "SAP") if t != company.value)  # the options show names; the value is the ticker
    company.select(other).run()
    assert not at.exception
    assert f"({other})" in _chart_layout(at)["title"]["text"]
    assert at.slider(key="price_window").value == (date(2020, 1, 1), date(2021, 6, 30))

    at.button_group(key="price_range").set_value("YTD").run()
    assert at.slider(key="price_window").value == (date(2026, 1, 1), date(2026, 9, 9))
