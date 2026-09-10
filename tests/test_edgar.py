"""EDGAR client parsers on saved responses, and the configuration guard."""

import json
from datetime import date
from pathlib import Path

import pytest

from invest.data import edgar

FIX = Path(__file__).parent / "fixtures"


def _json(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_client_refuses_to_run_without_a_contact_address(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(edgar, "sec_user_agent", lambda: None)
    with pytest.raises(edgar.EdgarConfigError):
        edgar.EdgarClient()
    with pytest.raises(edgar.EdgarConfigError):
        edgar.EdgarClient("no-address-here")
    client = edgar.EdgarClient("my-investment-app test@example.com")
    assert client.n_requests == 0


def test_tickers_frame():
    frame = edgar.tickers_frame(_json("edgar_company_tickers.json"))
    assert {"cik", "ticker", "name"} == set(frame.columns)
    assert frame.loc[frame["ticker"] == "AAPL", "cik"].iloc[0] == 320193
    assert not frame["ticker"].duplicated().any()


def test_filings_frame_and_selection():
    submissions = _json("edgar_submissions_1067983.json")
    filings = edgar.filings_frame(submissions)
    assert {"accession", "form", "filing_date", "report_date", "primary_document"} <= set(filings.columns)
    refs = edgar.select_filings(filings, 1067983, ("13F-HR",), limit=2)
    assert len(refs) == 2
    assert refs[0].filing_date > refs[1].filing_date  # newest first
    assert refs[0].report_date == date(2026, 6, 30)
    assert all(r.form == "13F-HR" for r in refs)
    assert edgar.select_filings(filings, 1067983, ("13F-HR",), since=date(2099, 1, 1)) == []


def test_facts_frame_keeps_filing_dates_and_accessions():
    frame = edgar.facts_frame(_json("edgar_companyfacts_aapl.json"))
    assert frame["cik"].unique().tolist() == [320193]
    assert {"us-gaap", "dei"} <= set(frame["taxonomy"].unique())
    revenue = frame[(frame["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax") & (frame["fp"] == "FY")]
    assert not revenue.empty
    assert revenue["filed_at"].notna().all() and revenue["accession"].str.len().gt(0).all()
    assert not frame["fact_id"].duplicated().any()
    # instant facts (balance sheet) have no start date; duration facts do
    assets = frame[frame["tag"] == "Assets"]
    assert assets["start_date"].isna().all()
    assert revenue["start_date"].notna().all()


def test_facts_frame_can_keep_only_the_tags_asked_for():
    payload = _json("edgar_companyfacts_aapl.json")
    everything = edgar.facts_frame(payload)
    some = edgar.facts_frame(payload, tags={"us-gaap": {"Assets"}, "dei": {"EntityCommonStockSharesOutstanding"}})
    assert set(some["tag"]) == {"Assets", "EntityCommonStockSharesOutstanding"}
    assert len(some) < len(everything)


def test_facts_frame_reads_ifrs_filers():
    frame = edgar.facts_frame(_json("edgar_companyfacts_sap.json"))
    assert (frame["taxonomy"] == "ifrs-full").any()
    assert (frame["form"] == "20-F").any()


def test_frames_frame():
    frame = edgar.frames_frame(_json("edgar_frames_revenues_cy2025.json"))
    assert len(frame) == 25
    assert frame.attrs["tag"] == "Revenues"
    assert frame["value"].gt(0).all()
