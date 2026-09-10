"""The weekly job end to end on a seeded store, and with one section blocked."""

import json
import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from invest.data.cache import Store
from invest.data.edgar import facts_frame
from invest.fundamentals.universe import Universe
from invest.jobs import fundamentals as fj
from invest.jobs import weekly
from invest.macro import scorecard as sc
from invest.macro.catalog import load_catalog
from invest.positioning import filings13f as f13
from invest.report import build

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
TODAY = date(2026, 9, 10)
DOCS = json.loads((FIX / "transactions_sample.json").read_text("utf-8"))
SPOT = {"ES0000000001": 12.0, "ES0000000002": 9.0, "USST": 14.04, "IE0000000003": 30.0, "EXIT": 31.0,
        "LU0000000004": 55.0, "FEES": 110.0}
PORTFOLIO_KWARGS = {"spot": SPOT, "fx": {"EUR": 1.0, "USD": 1.17},
                    "currencies": {s: ("USD" if s == "USST" else "EUR") for s in SPOT}}


def _seed(path: Path) -> None:
    rng = np.random.default_rng(11)
    months = pd.date_range("2000-01-01", "2026-08-01", freq="MS")
    days = pd.bdate_range("2016-01-01", "2026-09-09")
    catalog = load_catalog()
    with Store(path) as store:
        store.upsert_series("US_SAHM", pd.Series(np.abs(rng.normal(0.1, 0.1, len(months))), index=months), source="fred")
        store.upsert_series("US_UNRATE", pd.Series(rng.normal(4.3, 0.3, len(months)), index=months), source="fred")
        store.upsert_series("US_CURVE_10Y3M", pd.Series(rng.normal(0.6, 0.4, len(days)), index=days), source="fred")
        store.upsert_series("US_HY_OAS", pd.Series(rng.normal(3.8, 0.6, len(days)), index=days), source="fred")
        store.upsert_series("SPX", pd.Series(3000 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days))), index=days), source="yahoo")
        store.upsert_series("EURUSD", pd.Series(1.1, index=days), source="yahoo")
        usrec = pd.Series(0.0, index=months)
        usrec["2007-12-01":"2009-06-01"] = 1.0
        store.upsert_series("US_RECESSION", usrec, source="fred")
        # an earlier scorecard snapshot, so the "changed since" panel has a baseline
        run = store.start_run("refresh")
        readings = sc.build_readings(store, catalog, today=TODAY)
        regime, results = sc.build_regime(store, catalog)
        store.save_snapshot(run, "scorecard", sc.build_snapshot(readings, regime, results))
        # 13F holdings for one manager
        for name, period, filed, acc in (("berkshire_13f_2026-03-31.xml", date(2026, 3, 31), date(2026, 5, 15), "a1"),
                                         ("berkshire_13f_2026-06-30.xml", date(2026, 6, 30), date(2026, 8, 14), "a2")):
            table = f13.parse_info_table((FIX / name).read_bytes())
            store.upsert_holdings(f13.holdings_frame(table, manager_cik=1067983, manager="Berkshire Hathaway",
                                                     period_end=period, filed_at=filed, accession=acc))
        # a screener table from the fixture facts
        universe = Universe("examples", "Examples", ("MMM", "JPM", "SAP"))
        for ticker, cik, cname, sic in (("MMM", 66740, "3M CO", "3841"), ("JPM", 19617, "JPMORGAN CHASE & CO", "6021"),
                                        ("SAP", 1000184, "SAP SE", "7372")):
            store.upsert_facts(facts_frame(json.loads((FIX / f"edgar_companyfacts_{ticker.lower()}.json").read_text(encoding="utf-8"))))
            store.upsert_companies([{"cik": cik, "ticker": ticker, "name": cname, "sic": sic, "sector": fj.sector_from_sic(sic)}])
            prices = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days)))
            store.upsert_prices(ticker, pd.DataFrame({"close": prices, "adj_close": prices}, index=days), currency="USD")
        fj.save_screener_snapshot(store, run, universe, fj.build_metrics_table(store, universe), as_of=None)
        store.add_watch("MMM", cik=66740, note="seeded")


@pytest.fixture
def seeded(tmp_path):
    path = tmp_path / "market.duckdb"
    _seed(path)
    return path


def test_weekly_report_runs_end_to_end_on_stored_data(seeded, tmp_path):
    reports = tmp_path / "reports"
    with Store(seeded) as store:
        result = weekly.run_weekly(store, today=TODAY, skip_refresh=True, skip_filings=True, send=False,
                                   transaction_docs=DOCS, portfolio_kwargs=PORTFOLIO_KWARGS, reports_directory=reports, log=None)
    md = result.markdown
    for heading in ("## Header", "## Portfolio", "## Macro", "## Positioning", "## Opportunities",
                    "## Hypothesis register", "## Missing and failed", "## Appendix"):
        assert heading in md
    assert "€11,725" in md  # the golden portfolio value
    assert "Regime:" in md and "Weights against targets" in md
    assert "BERKSHIRE" in md.upper() and "45 days later" in md
    assert "Magic formula" in md and "MMM" in md
    assert result.failures == {}
    assert result.paths["html"].exists() and (reports / "latest.html").exists()
    assert "<svg" in result.html  # sparklines rendered
    assert any("archived" in s for s in result.statuses) and any("delivery skipped" in s for s in result.statuses)
    with Store(seeded, read_only=True) as store:
        assert store.load_snapshot("report")[2]["summary"].startswith("Weekly report 2026-09-10")
        assert store.runs("weekly").iloc[0]["sources_failed"] == 0


def test_a_blocked_source_lands_under_missing_and_failed(seeded, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise ConnectionError("simulated outage")

    monkeypatch.setattr(weekly.sc, "build_readings", boom)
    monkeypatch.setattr(weekly, "load_transaction_docs", boom)
    with Store(seeded) as store:
        result = weekly.run_weekly(store, today=TODAY, skip_refresh=True, skip_filings=True, send=False,
                                   reports_directory=tmp_path / "r", log=None)
    assert set(result.failures) >= {"macro scorecard", "portfolio"}
    assert "simulated outage" in result.markdown
    assert "## Missing and failed" in result.markdown and "Section not produced" in result.markdown
    assert result.exit_code == 1
    assert result.paths["html"].exists()  # the report still came out


def test_report_golden_sections_are_stable(seeded, tmp_path):
    """The Markdown for a fixed store and date; regenerate with UPDATE_GOLDEN=1 after a deliberate change."""
    with Store(seeded) as store:
        result = weekly.run_weekly(store, today=TODAY, skip_refresh=True, skip_filings=True, send=False,
                                   transaction_docs=DOCS, portfolio_kwargs=PORTFOLIO_KWARGS, reports_directory=tmp_path / "r", log=None)
    # strip what legitimately varies between runs: the run id and the table build time
    lines = [line for line in result.markdown.splitlines()
             if not line.startswith("Run ") and "table built" not in line and "<svg" not in line]
    golden = FIX / "report_golden.md"
    if os.environ.get("UPDATE_GOLDEN") or not golden.exists():
        golden.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert "\n".join(lines) + "\n" == golden.read_text(encoding="utf-8")


def test_report_page_lists_archives(seeded, tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    with Store(seeded) as store:
        weekly.run_weekly(store, today=TODAY, skip_refresh=True, skip_filings=True, send=False,
                          transaction_docs=DOCS, portfolio_kwargs=PORTFOLIO_KWARGS, reports_directory=reports, log=None)
    monkeypatch.setenv("INVEST_DB", str(seeded))
    monkeypatch.setenv("INVEST_REPORTS_DIR", str(reports))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    at = at.switch_page("pages/report.py").run()
    assert not at.exception
    assert at.selectbox[0].options and "2026-09-10" in at.selectbox[0].options[0]
    assert any("Weekly report, 2026-09-10" in m.value for m in at.markdown)


def test_report_page_without_archives(tmp_path, monkeypatch):
    monkeypatch.setenv("INVEST_DB", str(tmp_path / "none.duckdb"))
    monkeypatch.setenv("INVEST_REPORTS_DIR", str(tmp_path / "empty"))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    at = at.switch_page("pages/report.py").run()
    assert not at.exception
    assert any("invest.jobs.weekly" in i.value for i in at.info)


def test_build_markdown_with_nothing_still_produces_every_section():
    ctx = build.ReportContext(run_date=TODAY, run_id="x")
    md = build.build_markdown(ctx)
    assert md.count("## ") == 8
    assert "Section not produced" in md
    assert set(ctx.failures) >= {"Portfolio", "Macro", "Opportunities"}
