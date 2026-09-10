"""13F parsing and quarter-over-quarter diff on two real Berkshire information tables."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from invest.data.cache import Store
from invest.jobs.filings import refresh_holdings
from invest.positioning import filings13f as f13
from invest.positioning.managers import Manager, parse_managers

FIX = Path(__file__).parent / "fixtures"
Q1 = FIX / "berkshire_13f_2026-03-31.xml"
Q2 = FIX / "berkshire_13f_2026-06-30.xml"


@pytest.fixture(scope="module")
def quarters():
    prev = f13.holdings_frame(f13.parse_info_table(Q1.read_bytes()), manager_cik=1067983, manager="Berkshire Hathaway",
                              period_end=date(2026, 3, 31), filed_at=date(2026, 5, 15), accession="0001193125-26-226661")
    curr = f13.holdings_frame(f13.parse_info_table(Q2.read_bytes()), manager_cik=1067983, manager="Berkshire Hathaway",
                              period_end=date(2026, 6, 30), filed_at=date(2026, 8, 14), accession="0001193125-26-352200")
    return prev, curr


def test_parse_info_table_reads_every_field():
    table = f13.parse_info_table(Q2.read_bytes())
    assert len(table) == 12  # the fixture keeps the twelve largest positions
    row = table.iloc[0]
    assert row["cusip"] and len(row["cusip"]) == 9
    assert row["value"] > 1e9 and row["shares"] > 0
    assert row["sh_prn"] == "SH"
    assert {"issuer", "title_class", "put_call", "discretion", "voting_sole"} <= set(table.columns)


def test_parse_primary_doc():
    header = f13.parse_primary_doc((FIX / "berkshire_13f_primary_doc.xml").read_bytes())
    assert header.filer_cik == 1067983
    assert header.period_of_report == date(2026, 6, 30)
    assert not header.is_amendment
    assert header.table_entry_total and header.table_entry_total >= 80


def test_parse_rejects_other_xml():
    with pytest.raises(ValueError):
        f13.parse_info_table(b"<root><a/></root>")


def test_holdings_frame_values_are_dollars_and_positions_are_summed(quarters):
    _, curr = quarters
    assert curr["value_usd"].sum() > 1e11  # Berkshire's equity book is hundreds of billions
    assert not curr.duplicated(["cusip", "title_class", "put_call"]).any()
    old = f13.holdings_frame(f13.parse_info_table(Q2.read_bytes()), manager_cik=1, manager="x",
                             period_end=date(2022, 6, 30), filed_at=date(2022, 8, 15), accession="a")
    # a 2022 filing reported thousands: the same table converts to a thousand times the value
    assert old["value_usd"].sum() == pytest.approx(curr["value_usd"].sum() * 1000)
    assert curr.attrs["value_factor"] == 1.0 and old.attrs["value_factor"] == 1000.0


def test_a_recent_filing_still_in_thousands_is_detected_by_implied_price():
    """Baupost kept reporting thousands after 2023; the implied share price gives it away."""
    table = f13.parse_info_table(Q2.read_bytes())
    in_thousands = table.assign(value=table["value"] / 1000.0)
    assert f13.reports_thousands(in_thousands, date(2026, 6, 30))
    assert not f13.reports_thousands(table, date(2026, 6, 30))
    fixed = f13.holdings_frame(in_thousands, manager_cik=1, manager="x", period_end=date(2026, 6, 30),
                               filed_at=date(2026, 8, 14), accession="a")
    proper = f13.holdings_frame(table, manager_cik=1, manager="x", period_end=date(2026, 6, 30),
                                filed_at=date(2026, 8, 14), accession="a")
    assert fixed["value_usd"].sum() == pytest.approx(proper["value_usd"].sum())


def test_diff_quarters_classifies_changes(quarters):
    prev, curr = quarters
    diff = f13.diff_quarters(prev, curr)
    assert set(diff["status"]) <= {"new", "exit", "increased", "reduced", "unchanged"}
    assert len(diff) >= max(len(prev), len(curr))
    unchanged = diff[diff["status"] == "unchanged"]
    assert (unchanged["change_shares"] == 0).all()
    # sorted by absolute dollar change
    abs_change = diff["change_value"].abs().tolist()
    assert abs_change == sorted(abs_change, reverse=True)
    # a synthetic exit and a new position
    prev2 = prev.copy()
    prev2.loc[prev2.index[0], ["cusip", "issuer"]] = ["999999999", "GONE CO"]
    diff2 = f13.diff_quarters(prev2, curr)
    assert (diff2[diff2["issuer"] == "GONE CO"]["status"] == "exit").all()
    assert (diff2["status"] == "new").sum() >= 1
    assert diff2[diff2["status"].isin(["new", "exit"])]["big"].all()


def test_concentration_and_summary(quarters):
    prev, curr = quarters
    conc = f13.concentration(curr)
    # twelve table entries, fewer positions: the same security listed by several sub-managers is summed
    assert conc.n_positions == len(curr) <= 12
    assert 0 < conc.top1_pct <= conc.top5_pct <= conc.top10_pct <= 100
    assert 0 < conc.hhi <= 1
    text = f13.summarise("Berkshire Hathaway", date(2026, 6, 30), date(2026, 8, 14), f13.diff_quarters(prev, curr), conc)
    assert "2026-06-30" in text and "45 days later" in text
    assert "long US positions only" in text
    assert f"{conc.n_positions} positions" in text


def test_latest_two_quarters_from_store(quarters, tmp_path):
    prev, curr = quarters
    with Store(tmp_path / "h.duckdb") as store:
        assert store.upsert_holdings(prev) == len(prev)
        assert store.upsert_holdings(curr) == len(curr)
        assert store.upsert_holdings(curr) == len(curr)  # replace, not duplicate
        pair = f13.latest_two_quarters(store.read_holdings(1067983))
        assert pair is not None
        assert pair[0]["period_end"].iloc[0] == date(2026, 3, 31)
        assert pair[1]["period_end"].iloc[0] == date(2026, 6, 30)
        assert store.holdings_periods(1067983) == [date(2026, 3, 31), date(2026, 6, 30)]


class FakeEdgar:
    """Serves the Berkshire fixtures for any CIK, so two managers can be simulated."""

    def __init__(self):
        import json

        self.submissions_payload = json.loads((FIX / "edgar_submissions_1067983.json").read_text(encoding="utf-8"))
        self.n_requests = 0

    def submissions(self, cik):
        payload = dict(self.submissions_payload)
        payload["name"] = "BERKSHIRE HATHAWAY INC" if cik == 1067983 else "OTHER FUND LP"
        return payload

    def filing_index(self, cik, accession):
        return ["primary_doc.xml", "table.xml"]

    def filing_file(self, cik, accession, name):
        if name == "primary_doc.xml":
            return (FIX / "berkshire_13f_primary_doc.xml").read_bytes()
        return (Q2 if accession.endswith("352200") else Q1).read_bytes()


def test_refresh_holdings_prints_two_managers_from_parsed_data(tmp_path):
    managers = [Manager(1067983, "Berkshire Hathaway"), Manager(4242, "Other Fund")]
    with Store(tmp_path / "f.duckdb") as store:
        problems = refresh_holdings(store, FakeEdgar(), managers, log=None)
        assert problems == []
        for manager in managers:
            pair = f13.latest_two_quarters(store.read_holdings(manager.cik))
            assert pair is not None
            text = f13.summarise(manager.name, pair[1]["period_end"].iloc[0], pair[1]["filed_at"].iloc[0],
                                 f13.diff_quarters(*pair), f13.concentration(pair[1]))
            assert "positions worth" in text


def test_refresh_holdings_flags_a_name_mismatch(tmp_path):
    with Store(tmp_path / "f.duckdb") as store:
        problems = refresh_holdings(store, FakeEdgar(), [Manager(4242, "Scion Asset Management")], log=None)
        assert problems and "check the CIK" in problems[0]


def test_managers_config_parses_and_rejects_duplicates():
    managers = parse_managers({"manager": [{"cik": 1067983, "name": "Berkshire Hathaway"}]})
    assert managers[0].cik == 1067983
    with pytest.raises(ValueError):
        parse_managers({"manager": [{"cik": 1, "name": "a"}, {"cik": 1, "name": "b"}]})
