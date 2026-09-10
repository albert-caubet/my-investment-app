"""Form 4 parsing on real Apple filings and aggregation on a synthetic cluster."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from invest.positioning import insiders as ins

FIX = Path(__file__).parent / "fixtures"

SYNTHETIC = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <periodOfReport>2026-08-20</periodOfReport>
  <issuer><issuerCik>0000000042</issuerCik><issuerName>Test Co</issuerName></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>{owner}</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>{director}</isDirector><isOfficer>false</isOfficer></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>{day}</value></transactionDate>
      <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def _form(owner, day, code, shares, price, director="true"):
    ad = "A" if code == "P" else "D"
    return SYNTHETIC.format(owner=owner, day=day, code=code, shares=shares, price=price, ad=ad, director=director).encode()


def test_parse_real_form4_reads_owner_and_grant():
    frame = ins.parse_form4((FIX / "form4_000114036126035362.xml").read_bytes(),
                            accession="0001140361-26-035362", filed_at=date(2026, 9, 1))
    assert frame["issuer_cik"].iloc[0] == 320193
    assert frame["owner"].iloc[0] == "Ternus John"
    assert bool(frame["is_officer"].iloc[0]) and bool(frame["is_director"].iloc[0])
    assert (frame["code"] == "A").all()  # a grant, not an open-market trade
    assert frame["derivative"].all()
    assert frame["trans_date"].iloc[0] == date(2026, 9, 1)


def test_only_open_market_codes_count():
    frame = ins.parse_form4((FIX / "form4_000114036126035636.xml").read_bytes(),
                            accession="0001140361-26-035636", filed_at=date(2026, 9, 3))
    summary = ins.aggregate(frame)
    open_market = frame[~frame["derivative"] & frame["code"].isin(["P", "S"])]
    assert summary.n_buys == (open_market["code"] == "P").sum()
    assert summary.n_sells == (open_market["code"] == "S").sum()
    assert summary.n_buys == 0  # an executive selling and receiving grants, no purchase
    assert "not counted" in summary.note


def test_open_market_buys_and_sells_are_aggregated():
    frames = [
        ins.parse_form4(_form("Alice", "2026-06-01", "P", 1000, 50.0), accession="a", filed_at=date(2026, 6, 3)),
        ins.parse_form4(_form("Bob", "2026-06-15", "P", 500, 52.0), accession="b", filed_at=date(2026, 6, 17)),
        ins.parse_form4(_form("Carol", "2026-07-01", "P", 200, 55.0), accession="c", filed_at=date(2026, 7, 2)),
        ins.parse_form4(_form("Dave", "2026-07-10", "S", 3000, 60.0), accession="d", filed_at=date(2026, 7, 12)),
    ]
    tx = pd.concat(frames, ignore_index=True)
    summary = ins.aggregate(tx)
    assert summary.n_buys == 3 and summary.distinct_buyers == 3
    assert summary.buy_shares == 1700 and summary.buy_value == pytest.approx(1000 * 50 + 500 * 52 + 200 * 55)
    assert summary.n_sells == 1 and summary.sell_value == pytest.approx(180000)
    assert summary.cluster_buying  # three buyers within 90 days
    assert summary.n_filings == 4
    assert summary.window_start == date(2026, 6, 1) and summary.window_end == date(2026, 7, 10)


def test_cluster_needs_three_distinct_buyers_inside_ninety_days():
    frames = [
        ins.parse_form4(_form("Alice", "2026-01-01", "P", 100, 10.0), accession="a", filed_at=date(2026, 1, 3)),
        ins.parse_form4(_form("Alice", "2026-01-05", "P", 100, 10.0), accession="b", filed_at=date(2026, 1, 7)),
        ins.parse_form4(_form("Bob", "2026-06-01", "P", 100, 10.0), accession="c", filed_at=date(2026, 6, 3)),
        ins.parse_form4(_form("Carol", "2026-06-02", "P", 100, 10.0), accession="d", filed_at=date(2026, 6, 4)),
    ]
    tx = pd.concat(frames, ignore_index=True)
    assert not ins.aggregate(tx).cluster_buying  # Alice twice, and Bob and Carol months later
    assert ins.aggregate(tx, since=date(2026, 5, 1)).n_buys == 2


def test_empty_window_is_reported_not_faked():
    frame = ins.parse_form4(_form("Alice", "2026-01-01", "P", 100, 10.0), accession="a", filed_at=date(2026, 1, 3))
    summary = ins.aggregate(frame, since=date(2026, 6, 1))
    assert summary.n_buys == 0 and "No Form 4 transactions" in summary.note
    assert ins.aggregate(pd.DataFrame(columns=ins.COLUMNS)).issuer_cik is None
