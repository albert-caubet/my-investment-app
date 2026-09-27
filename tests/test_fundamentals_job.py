"""The fundamentals job on a fake EDGAR client and canned prices; point-in-time assertion."""

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from invest.data.cache import Store
from invest.fundamentals import screens
from invest.fundamentals.universe import Universe
from invest.jobs import fundamentals as job

FIX = Path(__file__).parent / "fixtures"
CIKS = {"MMM": 66740, "JPM": 19617, "SAP": 1000184}
SICS = {66740: "3841", 19617: "6021", 1000184: "7372"}
FILES = {66740: "mmm", 19617: "jpm", 1000184: "sap"}


class FakeEdgar:
    def __init__(self):
        self.n_requests = 0

    def company_tickers(self):
        return pd.DataFrame({"cik": list(CIKS.values()), "ticker": list(CIKS), "name": ["3M CO", "JPMORGAN CHASE & CO", "SAP SE"]})

    def companyfacts(self, cik):
        self.n_requests += 1
        return json.loads((FIX / f"edgar_companyfacts_{FILES[cik]}.json").read_text(encoding="utf-8"))

    def submissions(self, cik):
        self.n_requests += 1
        return {"cik": cik, "name": {66740: "3M CO", 19617: "JPMORGAN CHASE & CO", 1000184: "SAP SE"}[cik],
                "sic": SICS[cik], "exchanges": ["NYSE"]}


def _prices(symbols, period="12y"):
    days = pd.bdate_range("2014-01-01", "2026-09-09")
    rng = np.random.default_rng(7)
    out = {}
    for i, symbol in enumerate(symbols):
        path = 100.0 * np.cumprod(1 + rng.normal(0.0004, 0.012, len(days)))
        out[symbol] = pd.DataFrame({"close": path, "adj_close": path}, index=days)
    return out


UNIVERSE = Universe("examples", "Examples", ("MMM", "JPM", "SAP", "NOPE"), point_in_time=True)


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(job.yahoo, "fetch_history", _prices)
    with Store(tmp_path / "f.duckdb") as store:
        days = pd.bdate_range("2014-01-01", "2026-09-09")
        store.upsert_series("EURUSD", pd.Series(1.10, index=days), source="yahoo")
        problems = job.refresh_universe(store, FakeEdgar(), UNIVERSE, log=None)
        yield store, problems


def test_refresh_universe_stores_facts_companies_and_prices(loaded):
    store, problems = loaded
    assert problems == ["1 ticker(s) not in the SEC list: NOPE"]
    assert set(store.facts_ciks()) == set(CIKS.values())
    companies = store.companies().set_index("ticker")
    assert companies.loc["JPM", "sector"] == "banks"
    assert companies.loc["SAP", "sector"] == "software and services"
    assert set(store.price_symbols()) == set(CIKS)


def test_metrics_table_has_one_row_per_company_with_every_input_visible(loaded):
    store, _ = loaded
    table = job.build_metrics_table(store, UNIVERSE)
    assert set(table["ticker"]) == set(CIKS)
    rows = table.set_index("ticker")
    mmm = rows.loc["MMM"]
    assert mmm["market_cap"] > 0 and mmm["earnings_yield"] is not None
    assert mmm["basis"] == "ttm" and mmm["currency"] == "USD"
    assert mmm["f_score"] is None or 0 <= mmm["f_score"] <= 9
    assert isinstance(mmm["tags"], dict) and "revenue" in mmm["tags"]
    jpm = rows.loc["JPM"]
    assert jpm["is_financial"] and pd.isna(jpm["earnings_yield"]) and pd.notna(jpm["pb"])
    sap = rows.loc["SAP"]
    assert sap["currency"] == "EUR"
    # a USD price converted at the stored EURUSD rate into the currency of the accounts
    assert sap["market_cap"] == pytest.approx(sap["price"] * sap["shares_outstanding"] / 1.10)
    assert "epv_per_share" in table.columns and "mos_conservative" in table.columns


def test_screen_as_of_a_past_date_uses_only_facts_filed_before_it(loaded):
    """The Phase 3 acceptance test: nothing filed after the as-of date leaks into the row."""
    store, _ = loaded
    as_of = date(2025, 3, 1)
    table = job.build_metrics_table(store, UNIVERSE, as_of=as_of)
    rows = table.set_index("ticker")
    for ticker, cik in CIKS.items():
        facts = store.read_facts(cik)
        later = facts[pd.to_datetime(facts["filed_at"]).dt.date > as_of]
        assert not later.empty  # the fixtures do contain later filings
        row = rows.loc[ticker]
        assert pd.Timestamp(row["period_end"]).date() <= as_of
        assert pd.Timestamp(row["price_date"]).date() <= as_of
        used = store.read_facts(cik, as_of=as_of)
        assert (pd.to_datetime(used["filed_at"]).dt.date <= as_of).all()
        # the fiscal year the row rests on was filed before the as-of date
        fy_end = pd.Timestamp(row["fy_end"]).date()
        fy_rows = used[(used["fp"] == "FY") & (pd.to_datetime(used["end_date"]).dt.date == fy_end)]
        assert not fy_rows.empty
    # and the current table sees a later fiscal year than the past one
    now = job.build_metrics_table(store, UNIVERSE).set_index("ticker")
    assert pd.Timestamp(now.loc["MMM", "fy_end"]) > pd.Timestamp(rows.loc["MMM", "fy_end"])


def _fact(cik, tag, end, value, filed, *, taxonomy="us-gaap", unit="USD", start=None, fp="FY", form="10-K"):
    return {"fact_id": f"{cik}|{tag}|{end}|{value}", "cik": cik, "taxonomy": taxonomy, "tag": tag, "unit": unit,
            "start_date": pd.Timestamp(start).date() if start else None, "end_date": pd.Timestamp(end).date(),
            "value": float(value), "fy": pd.Timestamp(end).year, "fp": fp, "form": form,
            "filed_at": pd.Timestamp(filed).date(), "accession": f"acc-{filed}", "frame": None}


def test_stale_share_counts_and_class_mismatches_produce_no_market_cap(tmp_path):
    days = pd.bdate_range("2026-01-01", "2026-09-09")
    with Store(tmp_path / "g.duckdb") as store:
        # company 1: the only share count is from 2010 (Mastercard's cover-page case)
        # company 2: a class-A count against a class-B price, caught by the public float
        rows = [
            _fact(1, "Revenues", "2025-12-31", 1e9, "2026-02-01", start="2025-01-01"),
            _fact(1, "EntityCommonStockSharesOutstanding", "2010-10-27", 1.2e8, "2010-11-02", taxonomy="dei", unit="shares", fp="Q3", form="10-Q"),
            _fact(2, "Revenues", "2025-12-31", 1e9, "2026-02-01", start="2025-01-01"),
            _fact(2, "EntityCommonStockSharesOutstanding", "2026-07-20", 941_481, "2026-08-01", taxonomy="dei", unit="shares", fp="Q2", form="10-Q"),
            _fact(2, "EntityPublicFloat", "2025-06-30", 700e9, "2026-02-01", taxonomy="dei"),
            _fact(3, "Revenues", "2025-12-31", 1e9, "2026-02-01", start="2025-01-01"),
            _fact(3, "EntityCommonStockSharesOutstanding", "2026-07-20", 1e9, "2026-08-01", taxonomy="dei", unit="shares", fp="Q2", form="10-Q"),
            _fact(3, "EntityPublicFloat", "2025-06-30", 400e9, "2026-02-01", taxonomy="dei"),
        ]
        store.upsert_facts(pd.DataFrame(rows))
        for cik, ticker in ((1, "OLD"), (2, "CLASSB"), (3, "FINE")):
            store.upsert_companies([{"cik": cik, "ticker": ticker, "name": ticker, "sic": "7372"}])
            store.upsert_prices(ticker, pd.DataFrame({"close": 500.0, "adj_close": 500.0}, index=days), currency="USD")
        old = job.company_row(store, 1, "OLD", "OLD", "7372", as_of=None)
        assert old["market_cap"] is None and any("too old" in n for n in old["notes"])
        classb = job.company_row(store, 2, "CLASSB", "CLASSB", "7372", as_of=None)
        assert classb["market_cap"] is None and any("public float" in n for n in classb["notes"])
        fine = job.company_row(store, 3, "FINE", "FINE", "7372", as_of=None)
        assert fine["market_cap"] == pytest.approx(500e9)


def test_snapshot_round_trip_and_screen(loaded):
    store, _ = loaded
    table = job.build_metrics_table(store, UNIVERSE)
    run = store.start_run("fundamentals")
    job.save_screener_snapshot(store, run, UNIVERSE, table, as_of=None)
    payload = store.load_snapshot("screener")[2]
    assert payload["n_companies"] == 3 and payload["universe"] == "examples"
    restored = pd.DataFrame(payload["rows"])
    ranked, excluded = screens.run_screen("magic_formula", restored)
    assert "JPM" not in ranked["ticker"].tolist()  # financial
    assert ranked["rank"].tolist() == list(range(1, len(ranked) + 1))


def test_breadth_needs_enough_names(loaded):
    store, _ = loaded
    assert job.compute_breadth(store, ["MMM", "JPM", "SAP"]) == {}  # three names is not breadth
    symbols = [f"S{i}" for i in range(25)]
    frames = _prices(symbols)
    for symbol, frame in frames.items():
        store.upsert_prices(symbol, frame, currency="USD")
    series = job.compute_breadth(store, symbols)
    assert set(series) == {"BREADTH_ABOVE_200D_PCT", "BREADTH_NH_NL_PCT"}
    pct = series["BREADTH_ABOVE_200D_PCT"]
    assert pct.between(0, 100).all() and len(pct) > 2000
    assert series["BREADTH_NH_NL_PCT"].between(-100, 100).all()
    assert job.store_breadth(store, symbols) > 0
    assert not store.read_series("BREADTH_ABOVE_200D_PCT").empty


def test_without_the_sec_setting_the_command_explains_and_leaves_the_database_alone(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("invest.data.edgar.sec_user_agent", lambda: None)
    db = tmp_path / "untouched.duckdb"
    assert job.main(["--db", str(db)]) == 2
    out = capsys.readouterr().out
    assert "SEC_USER_AGENT" in out and ".streamlit/secrets.toml" in out
    assert "Traceback" not in out
    assert not db.exists()
