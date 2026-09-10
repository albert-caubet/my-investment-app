"""Tag mapping, point-in-time queries and TTM arithmetic on real and synthetic facts."""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from invest.data.edgar import facts_frame
from invest.fundamentals import facts as F

FIX = Path(__file__).parent / "fixtures"


def _facts(name):
    return facts_frame(json.loads((FIX / f"edgar_companyfacts_{name}.json").read_text(encoding="utf-8")))


def _row(tag, start, end, value, filed, *, fp="FY", form="10-K", taxonomy="us-gaap", unit="USD", cik=1):
    return {
        "fact_id": f"{tag}|{start}|{end}|{filed}|{value}", "cik": cik, "taxonomy": taxonomy, "tag": tag, "unit": unit,
        "start_date": pd.Timestamp(start).date() if start else None, "end_date": pd.Timestamp(end).date(),
        "value": float(value), "fy": pd.Timestamp(end).year, "fp": fp, "form": form,
        "filed_at": pd.Timestamp(filed).date(), "accession": f"acc-{filed}", "frame": None,
    }


@pytest.fixture(scope="module")
def synthetic():
    rows = [
        # fiscal years, each restated (same value) in the next 10-K; FY2024 revised upward in the FY2025 10-K
        _row("Revenues", "2023-01-01", "2023-12-31", 90, "2024-02-01"),
        _row("Revenues", "2024-01-01", "2024-12-31", 100, "2025-02-01"),
        _row("Revenues", "2024-01-01", "2024-12-31", 105, "2026-02-01"),
        _row("Revenues", "2025-01-01", "2025-12-31", 120, "2026-02-01"),
        # year-to-date rows from 10-Qs
        _row("Revenues", "2025-01-01", "2025-06-30", 55, "2025-08-01", fp="Q2", form="10-Q"),
        _row("Revenues", "2025-04-01", "2025-06-30", 28, "2025-08-01", fp="Q2", form="10-Q"),
        _row("Revenues", "2026-01-01", "2026-06-30", 70, "2026-08-01", fp="Q2", form="10-Q"),
        _row("Revenues", "2026-04-01", "2026-06-30", 36, "2026-08-01", fp="Q2", form="10-Q"),
        # balance sheet instants
        _row("Assets", None, "2024-12-31", 1000, "2025-02-01"),
        _row("Assets", None, "2025-12-31", 1100, "2026-02-01"),
        _row("Assets", None, "2026-06-30", 1150, "2026-08-01", fp="Q2", form="10-Q"),
        # shares: cover page (dei) is preferred and dated after the period
        _row("EntityCommonStockSharesOutstanding", None, "2026-07-20", 98, "2026-08-01", fp="Q2", form="10-Q",
             taxonomy="dei", unit="shares"),
        _row("CommonStockSharesOutstanding", None, "2026-06-30", 99, "2026-08-01", fp="Q2", form="10-Q", unit="shares"),
        _row("CommonStockSharesOutstanding", None, "2025-12-31", 100, "2026-02-01", unit="shares"),
    ]
    return pd.DataFrame(rows)


def test_facts_as_of_uses_only_what_was_filed(synthetic):
    early = F.fundamentals_as_of(synthetic, date(2025, 6, 1))
    assert early.history["revenue"] == {2023: 90.0, 2024: 100.0}
    assert early.latest["revenue"] == 100.0 and early.flow_basis["revenue"].basis == "annual"
    assert early.fy_end == date(2024, 12, 31)
    later = F.fundamentals_as_of(synthetic, date(2026, 3, 1))
    assert later.history["revenue"][2024] == 105.0  # the restatement, once filed
    assert later.history["revenue"][2025] == 120.0


def test_ttm_is_fy_plus_ytd_minus_prior_ytd(synthetic):
    fund = F.fundamentals_as_of(synthetic, date(2026, 9, 1))
    flow = fund.flow_basis["revenue"]
    assert flow.basis == "ttm"
    assert flow.value == pytest.approx(120 + 70 - 55)
    assert flow.period_end == date(2026, 6, 30)
    assert "FY 2025-12-31" in flow.note
    assert fund.period_end == date(2026, 6, 30)


def test_ttm_falls_back_to_annual_when_the_prior_ytd_is_missing(synthetic):
    without_prior = synthetic[~((synthetic["end_date"] == date(2025, 6, 30)) & (synthetic["fp"] == "Q2"))]
    flow = F.fundamentals_as_of(without_prior, date(2026, 9, 1)).flow_basis["revenue"]
    assert flow.basis == "annual" and flow.value == 120.0
    assert "no comparable" in flow.note


def test_instants_take_the_latest_and_shares_prefer_the_cover_page(synthetic):
    fund = F.fundamentals_as_of(synthetic, date(2026, 9, 1))
    assert fund.latest["total_assets"] == 1150.0
    assert fund.latest["shares_outstanding"] == 98.0
    assert fund.tags["shares_outstanding"] == "dei:EntityCommonStockSharesOutstanding"
    assert fund.history["shares_outstanding"] == {2025: 100.0}  # cover-page counts are not fiscal-year rows
    assert fund.fy_latest["total_assets"] == 1100.0 and fund.fy_previous["total_assets"] == 1000.0


def test_apple_resolves_revenue_through_the_fallback_list():
    facts = _facts("aapl")
    fund = F.fundamentals_as_of(facts, date(2026, 9, 10))
    assert fund.tags["revenue"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert fund.currency == "USD"
    assert not fund.is_financial
    assert fund.fiscal_years[-1] == 2025 and fund.fy_end == date(2025, 9, 27)
    flow = fund.flow_basis["net_income"]
    assert flow.basis == "ttm" and flow.period_end == date(2026, 6, 27)
    # TTM = FY2025 + YTD to 2026-06-27 - YTD to 2025-06-28, taken straight from the filings
    ni = facts[(facts["tag"] == "NetIncomeLoss")]
    fy25 = ni[(ni["fp"] == "FY") & (ni["end_date"] == date(2025, 9, 27))]["value"].iloc[-1]
    ytd26 = ni[(ni["start_date"] == date(2025, 9, 28)) & (ni["end_date"] == date(2026, 6, 27))]["value"].iloc[-1]
    ytd25 = ni[(ni["start_date"] == date(2024, 9, 29)) & (ni["end_date"] == date(2025, 6, 28))]["value"].iloc[-1]
    assert flow.value == pytest.approx(fy25 + ytd26 - ytd25)
    assert fund.history["gross_margin"][2025] == pytest.approx(
        fund.history["gross_profit"][2025] / fund.history["revenue"][2025])
    assert fund.history["fcf"][2025] == pytest.approx(fund.history["cfo"][2025] - fund.history["capex"][2025])


def test_apple_point_in_time_excludes_later_filings():
    facts = _facts("aapl")
    fund = F.fundamentals_as_of(facts, date(2025, 6, 1))
    assert fund.fiscal_years[-1] == 2024  # the FY2025 10-K was filed in October 2025
    assert fund.period_end <= date(2025, 6, 1)
    used = F.facts_as_of(facts, date(2025, 6, 1))
    assert (pd.to_datetime(used["filed_at"]).dt.date <= date(2025, 6, 1)).all()


def test_bank_is_flagged_financial_and_uses_bank_tags():
    fund = F.fundamentals_as_of(_facts("jpm"), date(2026, 9, 10))
    assert fund.is_financial
    # "Revenues" is only reported annually; the net-of-interest tag has quarterly rows,
    # so it gives a complete TTM and wins, and the value never mixes the two tags
    flow = fund.flow_basis["revenue"]
    assert flow.tag == "RevenuesNetOfInterestExpense" and flow.basis == "ttm"
    assert fund.tags["revenue"] == "RevenuesNetOfInterestExpense"
    facts = _facts("jpm")
    rows = facts[facts["tag"] == "RevenuesNetOfInterestExpense"]
    fy25 = rows[(rows["fp"] == "FY") & (rows["end_date"] == date(2025, 12, 31))]["value"].iloc[-1]
    ytd26 = rows[(rows["start_date"] == date(2026, 1, 1)) & (rows["end_date"] == date(2026, 6, 30))]["value"].iloc[-1]
    ytd25 = rows[(rows["start_date"] == date(2025, 1, 1)) & (rows["end_date"] == date(2025, 6, 30))]["value"].iloc[-1]
    assert flow.value == pytest.approx(fy25 + ytd26 - ytd25)
    assert fund.latest["operating_income"] is None
    assert fund.latest["deposits"] is not None
    assert any("financial" in n for n in fund.notes)


def test_ifrs_filer_reads_euro_annual_facts():
    fund = F.fundamentals_as_of(_facts("sap"), date(2026, 9, 10))
    assert fund.currency == "EUR"
    assert fund.tags["revenue"] == "ifrs-full:Revenue"
    assert fund.tags["net_income"] == "ifrs-full:ProfitLossAttributableToOwnersOfParent"
    assert fund.flow_basis["revenue"].basis == "annual"  # 20-F filers report once a year
    assert fund.fiscal_years[-1] == 2025
    table = fund.history_frame()
    assert set(fund.fiscal_years) <= set(table.index)  # balance-sheet comparatives reach further back
    assert table.loc[2025, "revenue"] == fund.history["revenue"][2025]


def test_empty_facts_report_rather_than_raise():
    fund = F.fundamentals_as_of(pd.DataFrame(columns=["cik", "taxonomy", "tag", "unit", "start_date", "end_date",
                                                      "value", "fy", "fp", "form", "filed_at", "accession", "frame"]))
    assert fund.latest == {} and "no facts" in fund.notes[0]
