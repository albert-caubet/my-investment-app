"""Headless smoke test for the Screener page on a seeded temporary database."""

import json
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
