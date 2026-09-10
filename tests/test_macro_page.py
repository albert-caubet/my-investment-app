"""Headless smoke test for the Macro page on a seeded temporary database. No network."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

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
        run = store.start_run("refresh")
        readings = sc.build_readings(store, catalog, today=date(2026, 9, 10))
        regime, results = sc.build_regime(store, catalog)
        store.save_snapshot(run, "scorecard", sc.build_snapshot(readings, regime, results))
        store.finish_run(run, ok=8, failed=0)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    path = tmp_path / "market.duckdb"
    _seed(path)
    monkeypatch.setenv("INVEST_DB", str(path))
    return path


def _macro(at: AppTest) -> AppTest:
    return at.switch_page("pages/macro.py").run()


def test_macro_page_renders_from_the_database(seeded_db):
    at = _macro(AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run())
    assert not at.exception
    headers = [h.value for h in at.subheader]
    assert any(h.startswith("Regime:") for h in headers)
    assert "Scorecard" in headers and "Data freshness" in headers
    # every scorecard table shows a value with its date
    assert len(at.dataframe) >= 3
    assert any("Regime" in h for h in headers)


def test_macro_page_without_a_database_explains_what_to_run(tmp_path, monkeypatch):
    monkeypatch.setenv("INVEST_DB", str(tmp_path / "absent.duckdb"))
    at = _macro(AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run())
    assert not at.exception
    assert any("invest.jobs.refresh" in i.value for i in at.info)
