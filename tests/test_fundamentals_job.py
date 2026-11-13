"""The fundamentals job on a fake EDGAR client and canned prices; point-in-time assertion.

The European universe runs on saved Yahoo responses and canned prices and exchange rates.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from invest.data.cache import Store, utcnow
from invest.fundamentals import screens
from invest.fundamentals.universe import Universe, yahoo_id
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


# --- a European universe from Yahoo -----------------------------------------------------------

PROFILES = {
    "SHEL.L": {"name": "Shell plc", "sector": "Energy", "currency": "GBp", "financial_currency": "USD",
               "exchange": "LSE"},
    "ULVR.L": {"name": "Unilever PLC", "sector": "Consumer Defensive", "currency": "GBp",
               "financial_currency": "EUR", "exchange": "LSE"},
    "HSBA.L": {"name": "HSBC Holdings plc", "sector": "Financial Services", "currency": "GBp",
               "financial_currency": "USD", "exchange": "LSE"},
}
EU = Universe("eu_examples", "European examples", ("SHEL.L", "ULVR.L", "HSBA.L", "GONE.L"), point_in_time=False,
              region="EU", source="yahoo")
PENCE = {"SHEL.L": 3000.0, "ULVR.L": 5000.0, "HSBA.L": 1000.0}
FX = {"GBP=X": 0.75, "EUR=X": 0.90}  # pounds and euros per dollar


class FakeYahoo:
    """Saved responses for three London listings; Yahoo knows nothing of GONE.L."""

    def __init__(self, revise: float = 0.0):
        self.n_requests = 0
        self.revise = revise

    def profile(self, symbol):
        self.n_requests += 1
        return PROFILES.get(symbol, {})

    def fundamentals(self, symbol, kinds):
        self.n_requests += 1
        name = symbol.lower().replace(".", "_")
        payload = json.loads((FIX / f"yahoo_timeseries_{name}.json").read_text(encoding="utf-8"))
        if self.revise:  # a restatement of the latest fiscal year's revenue
            item = next(s for s in payload["timeseries"]["result"] if s["meta"]["type"][0] == "annualTotalRevenue")
            item["annualTotalRevenue"][-1]["reportedValue"]["raw"] += self.revise
        return payload


def _eu_prices(symbols, period="12y"):
    days = pd.bdate_range("2014-01-01", "2026-11-11")
    closes = {**PENCE, **FX}
    return {s: pd.DataFrame({"close": closes[s], "adj_close": closes[s]}, index=days) for s in symbols if s in closes}


@pytest.fixture
def european(tmp_path, monkeypatch):
    monkeypatch.setattr(job.yahoo, "fetch_history", _eu_prices)
    with Store(tmp_path / "eu.duckdb") as store:
        problems = job.refresh_yahoo_universe(store, FakeYahoo(), EU, log=None)
        yield store, problems


def test_a_yahoo_universe_stores_accounts_names_sectors_prices_and_rates(european):
    store, problems = european
    assert problems == ["GONE.L: Yahoo has no quote for this symbol"]
    companies = store.companies().set_index("ticker")
    assert set(companies.index) == {"SHEL.L", "ULVR.L", "HSBA.L"}
    assert companies.loc["SHEL.L", "cik"] == yahoo_id("SHEL.L") < 0  # no CIK: an id from the symbol
    assert companies.loc["HSBA.L", "sector"] == "financial services"
    assert store.cik_of("SHEL.L") is None
    assert store.price_currency("SHEL.L") == "GBp"
    assert {"GBP=X", "EUR=X"} <= set(store.price_symbols())
    assert set(store.read_facts(yahoo_id("SHEL.L"))["taxonomy"]) == {"yahoo"}


def test_market_caps_are_converted_from_pence_into_the_accounts_and_stated_in_euros(european):
    store, _ = european
    rows = job.build_metrics_table(store, EU).set_index("ticker")
    shel = rows.loc["SHEL.L"]
    assert (shel["price"], shel["currency"]) == (3000.0, "USD")  # 3,000 pence; accounts in dollars
    assert shel["market_cap"] == pytest.approx(30.0 / 0.75 * shel["shares_outstanding"])  # £30 at 0.75 a dollar
    assert shel["market_cap_eur"] == pytest.approx(shel["market_cap"] * 0.90)
    ulvr = rows.loc["ULVR.L"]
    assert ulvr["currency"] == "EUR"
    assert ulvr["market_cap"] == pytest.approx(50.0 / 0.75 * 0.90 * ulvr["shares_outstanding"])
    assert ulvr["market_cap_eur"] == pytest.approx(ulvr["market_cap"])
    assert rows.loc["HSBA.L", "is_financial"] and not shel["is_financial"]
    assert (shel["basis"], shel["sector"]) == ("ttm", "energy")
    assert shel["tags"]["revenue"] == "yahoo:TotalRevenue"
    assert pd.notna(shel["earnings_yield"]) and pd.notna(shel["epv_per_share"])


def test_a_fetch_again_keeps_the_day_each_value_was_first_seen(european, monkeypatch):
    store, _ = european
    first = store.read_facts(yahoo_id("SHEL.L"))
    first_day = pd.to_datetime(first["filed_at"]).dt.date.max()
    week_later = utcnow() + timedelta(days=7)
    monkeypatch.setattr(job, "utcnow", lambda: week_later)
    job.refresh_yahoo_universe(store, FakeYahoo(), EU, log=None, skip_prices=True)
    assert len(store.read_facts(yahoo_id("SHEL.L"))) == len(first)  # the same values: nothing new
    job.refresh_yahoo_universe(store, FakeYahoo(revise=1e9), EU, log=None, skip_prices=True)
    after = store.read_facts(yahoo_id("SHEL.L"))
    assert len(after) == len(first) + 1  # the restated revenue, dated the day it was seen
    assert pd.to_datetime(after["filed_at"]).dt.date.max() == week_later.date()
    assert len(store.read_facts(yahoo_id("SHEL.L"), as_of=first_day)) == len(first)


def test_a_table_as_of_a_day_before_the_first_fetch_has_no_accounts(european):
    store, _ = european
    rows = job.build_metrics_table(store, EU, as_of=date(2026, 1, 2)).set_index("ticker")
    assert rows["market_cap"].isna().all()
    assert all("no facts filed by this date" in notes for notes in rows["notes"])


def test_the_european_table_is_a_snapshot_of_its_own(european):
    store, _ = european
    store.save_snapshot(store.start_run("fundamentals"), "screener", {"universe": "sp500_core", "rows": []})
    job.save_screener_snapshot(store, store.start_run("fundamentals"), EU, job.build_metrics_table(store, EU),
                               as_of=None)
    payload = store.load_snapshot("screener_eu")[2]
    assert (payload["region"], payload["source"], payload["point_in_time"]) == ("EU", "yahoo", False)
    assert payload["n_companies"] == 3
    assert store.load_snapshot("screener")[2]["universe"] == "sp500_core"  # still the latest US table


def test_the_command_builds_a_yahoo_universe_without_the_sec_setting_or_breadth(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("invest.data.edgar.sec_user_agent", lambda: None)
    monkeypatch.setattr(job, "load_universe", lambda: {"eu_examples": EU})
    monkeypatch.setattr(job.yahoo, "YahooClient", FakeYahoo)
    monkeypatch.setattr(job.yahoo, "fetch_history", _eu_prices)
    breadth = []
    monkeypatch.setattr(job, "store_breadth", lambda *args, **kwargs: breadth.append(args))
    db = tmp_path / "cli.duckdb"
    assert job.main(["--db", str(db), "--universe", "eu_examples"]) == 1  # GONE.L is a problem
    out = capsys.readouterr().out
    assert "7 Yahoo Finance requests" in out and "GONE.L: Yahoo has no quote" in out
    assert not breadth  # the macro scorecard's breadth series are of the US market
    with Store(db, read_only=True) as store:
        assert store.load_snapshot("screener_eu")[2]["n_companies"] == 3
        assert store.load_snapshot("screener") is None


def test_rates_come_from_whichever_of_eurusd_and_eur_x_closed_later(tmp_path):
    with Store(tmp_path / "fx.duckdb") as store:
        store.upsert_series("EURUSD", pd.Series(1.10, index=pd.bdate_range("2026-01-01", "2026-11-06")),
                            source="yahoo")
        assert job.usd_per_unit("EUR", store, None) == pytest.approx(1.10)
        store.upsert_prices("EUR=X", pd.DataFrame({"close": 0.80}, index=pd.bdate_range("2026-01-01", "2026-11-11")),
                            currency="EUR")
        assert job.usd_per_unit("EUR", store, None) == pytest.approx(1 / 0.80)  # the later close
        assert job.usd_per_unit("GBp", store, None) is None  # no pound rate stored
        assert job.usd_per_unit("USD", store, None) == 1.0
        assert job.convert(100.0, "EUR", "EUR", store, None) == 100.0
        assert job.convert(100.0, "USD", "EUR", store, None) == pytest.approx(80.0)
        assert job.convert(100.0, "GBp", "EUR", store, None) is None


# --- one company in no universe, for the Company financials page -----------------------------


@pytest.fixture
def downloads(monkeypatch):
    """Canned closes for every symbol asked for, euros at 0.90 a dollar, and a record of what was asked."""
    asked = []

    def fetch_history(symbols, period="12y"):
        asked.extend(symbols)
        frames = {**_prices([s for s in symbols if not s.endswith("=X")]), **_eu_prices(symbols)}
        return {s: frames[s] for s in symbols if s in frames}

    monkeypatch.setattr(job.yahoo, "fetch_history", fetch_history)
    return asked


@pytest.fixture
def untouched(tmp_path, monkeypatch):
    """The market database a lookup must never create or write."""
    db = tmp_path / "market.duckdb"
    monkeypatch.setenv("INVEST_DB", str(db))
    return db


def test_a_us_filer_is_looked_up_on_edgar_in_memory(downloads, untouched):
    edgar = FakeEdgar()
    data = job.look_up_company(" mmm ", edgar_client=edgar, yahoo_client=FakeYahoo())
    assert (data.source, data.stored, data.problems) == ("edgar", False, [])
    row = data.row
    assert (row["ticker"], row["name"], row["sector"], row["currency"]) == ("MMM", "3M CO", "medical equipment", "USD")
    assert row["market_cap"] > 0 and pd.notna(row["earnings_yield"]) and pd.notna(row["epv_per_share"])
    assert row["tags"]["revenue"] and "f_checks" in row.index
    assert data.currency == "USD" and len(data.history) >= 5 and "revenue" in data.history.columns
    assert data.price_currency == "USD" and not data.prices.empty
    assert downloads == ["MMM"]  # accounts in dollars: no exchange rate to fetch
    assert edgar.n_requests == 2  # its facts and its submissions, nothing else
    assert not untouched.exists()


def test_a_filer_with_accounts_in_euros_has_its_price_converted_at_a_rate_fetched_with_it(downloads, untouched):
    data = job.look_up_company("SAP", edgar_client=FakeEdgar(), yahoo_client=FakeYahoo())
    assert sorted(downloads) == ["EUR=X", "SAP"]  # one download, the rate with the price
    row = data.row
    assert (data.source, row["currency"], data.price_currency) == ("edgar", "EUR", "USD")
    assert row["market_cap"] == pytest.approx(row["price"] * 0.90 * row["shares_outstanding"])  # 0.90 euros a dollar
    assert not untouched.exists()


def test_a_symbol_with_an_exchange_suffix_comes_from_yahoo_without_asking_sec(downloads, untouched):
    asked = []

    def sec_tickers():
        asked.append("list")
        return FakeEdgar().company_tickers()

    data = job.look_up_company("ULVR.L", edgar_client=FakeEdgar(), yahoo_client=FakeYahoo(), sec_tickers=sec_tickers)
    assert not asked
    row = data.row
    assert (data.source, data.stored, row["name"], row["currency"]) == ("yahoo", False, "Unilever PLC", "EUR")
    assert data.price_currency == "GBp" and not data.prices.empty
    assert row["market_cap"] == pytest.approx(50.0 / 0.75 * 0.90 * row["shares_outstanding"])
    assert row["tags"]["revenue"] == "yahoo:TotalRevenue" and 4 <= len(data.history) <= 5
    assert not untouched.exists()


def test_without_an_edgar_client_a_us_filer_is_asked_of_yahoo_alone(downloads):
    with pytest.raises(LookupError, match="^MMM: Yahoo has no quote for this symbol$"):
        job.look_up_company("MMM", yahoo_client=FakeYahoo())


def test_a_filer_edgar_has_no_accounts_for_is_asked_of_yahoo_next_and_the_reasons_kept(downloads):
    class Unfiled(FakeEdgar):
        def companyfacts(self, cik):
            raise LookupError("no XBRL facts")

    with pytest.raises(LookupError) as raised:
        job.look_up_company("MMM", edgar_client=Unfiled(), yahoo_client=FakeYahoo())
    assert str(raised.value) == "MMM (66740): no XBRL facts; MMM: Yahoo has no quote for this symbol"
    assert not downloads  # no prices for a company without accounts


def test_sec_not_answering_is_said_and_yahoo_asked(downloads):
    def down():
        raise OSError("SEC did not answer")

    with pytest.raises(LookupError) as raised:
        job.look_up_company("MMM", edgar_client=FakeEdgar(), yahoo_client=FakeYahoo(), sec_tickers=down)
    assert str(raised.value) == ("SEC's list of tickers could not be had: SEC did not answer; "
                                 "MMM: Yahoo has no quote for this symbol")
