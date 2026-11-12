"""Yahoo Finance accounts: saved fundamentals time series into facts and fundamentals. No network.

The fixtures are real responses, trimmed to the series the canonical fields read: Shell
(quoted in pence, accounts in dollars, quarterly reports), Unilever (half-yearly reports,
accounts in euros) and HSBC (a bank).
"""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError

from invest.data import yahoo
from invest.fundamentals import facts as F

FIX = Path(__file__).parent / "fixtures"
KINDS = F.tag_kinds("yahoo")
SEEN = date(2026, 11, 11)


def _payload(symbol: str) -> dict:
    name = symbol.lower().replace(".", "_")
    return json.loads((FIX / f"yahoo_timeseries_{name}.json").read_text(encoding="utf-8"))


def _facts(symbol: str, *, seen: date = SEEN, currency: str | None = None) -> pd.DataFrame:
    return yahoo.fundamentals_facts(_payload(symbol), -1, KINDS, seen_on=seen, currency=currency)


def _series(payload: dict, name: str) -> list[dict]:
    item = next(s for s in payload["timeseries"]["result"] if s["meta"]["type"][0] == name)
    return item[name]


def test_one_request_asks_for_every_item_the_fields_read():
    types = yahoo.fundamentals_types(KINDS)
    assert {"annualTotalRevenue", "trailingTotalRevenue", "annualTotalAssets", "quarterlyTotalAssets",
            "annualOrdinarySharesNumber", "quarterlyOrdinarySharesNumber", "trailingDilutedEPS"} <= set(types)
    # no quarterly flows (a half-year reporter's are labelled as quarters) and no trailing balances
    assert not {"quarterlyTotalRevenue", "trailingTotalAssets"} & set(types)
    assert len(types) == 2 * len(KINDS)


def test_a_response_becomes_facts_in_the_store_shape():
    facts = _facts("SHEL.L")
    assert list(facts.columns) == yahoo.FACT_COLUMNS
    assert set(facts["taxonomy"]) == {"yahoo"} and set(facts["cik"]) == {-1}
    assert set(facts["filed_at"]) == {SEEN}  # Yahoo has no filing dates: the day of the fetch
    assert set(facts["fp"]) == {"FY", "Q", "TTM"}
    units = facts.groupby("tag")["unit"].first()
    assert units["TotalRevenue"] == "USD"  # Shell's accounts, though London quotes it in pence
    assert units["OrdinarySharesNumber"] == "shares"
    assert units["DilutedEPS"] == "USD/shares"
    revenue = facts[facts["tag"] == "TotalRevenue"]
    spans = (pd.to_datetime(revenue["end_date"]) - pd.to_datetime(revenue["start_date"])).dt.days
    assert spans.between(360, 370).all()  # a fiscal year, or the trailing twelve months to its date
    assert facts[facts["tag"] == "TotalAssets"]["start_date"].isna().all()


def test_payments_are_stored_as_positive_amounts_as_xbrl_gives_them():
    facts = _facts("SHEL.L")
    raw = [p["reportedValue"]["raw"] for p in _series(_payload("SHEL.L"), "annualCapitalExpenditure")]
    assert all(v < 0 for v in raw)  # Yahoo signs them as outflows
    for tag in ("CapitalExpenditure", "CashDividendsPaid", "RepurchaseOfCapitalStock"):
        values = facts[facts["tag"] == tag]["value"]
        assert len(values) and (values > 0).all(), tag


def test_a_quarterly_balance_at_a_fiscal_year_end_defers_to_the_annual_one():
    assets = _facts("SHEL.L").query("tag == 'TotalAssets'")
    assert not assets["end_date"].duplicated().any()
    annual = set(assets[assets["fp"] == "FY"]["end_date"])
    quarterly = set(assets[assets["fp"] == "Q"]["end_date"])
    assert quarterly and not annual & quarterly
    assert max(quarterly) > max(annual)  # the latest quarter is kept


def test_the_same_value_fetched_again_is_the_same_fact_and_a_revised_one_a_new_fact():
    first = _facts("SHEL.L")
    assert set(_facts("SHEL.L", seen=date(2026, 11, 18))["fact_id"]) == set(first["fact_id"])
    payload = _payload("SHEL.L")
    _series(payload, "annualTotalRevenue")[-1]["reportedValue"]["raw"] += 1e6  # restated
    revised = yahoo.fundamentals_facts(payload, -1, KINDS, seen_on=date(2026, 11, 18))
    assert len(set(revised["fact_id"]) - set(first["fact_id"])) == 1


def test_money_without_a_currency_takes_the_profiles_and_is_left_out_without_one():
    payload = _payload("SHEL.L")
    for item in payload["timeseries"]["result"]:
        for point in item[item["meta"]["type"][0]]:
            point.pop("currencyCode", None)
    bare = yahoo.fundamentals_facts(payload, -1, KINDS, seen_on=SEEN)
    assert set(bare["unit"]) == {"shares"}
    assert set(yahoo.fundamentals_facts(payload, -1, KINDS, seen_on=SEEN, currency="USD")["unit"]) == {
        "USD", "USD/shares", "shares"}


def test_a_flow_over_another_period_than_its_series_says_is_left_out():
    payload = _payload("SHEL.L")
    _series(payload, "annualTotalRevenue")[-1]["periodType"] = "6M"
    facts = yahoo.fundamentals_facts(payload, -1, KINDS, seen_on=SEEN)
    annual = facts[(facts["tag"] == "TotalRevenue") & (facts["fp"] == "FY")]
    assert len(annual) == len(_series(payload, "annualTotalRevenue")) - 1


def test_shell_reads_yahoos_trailing_twelve_months_and_its_fiscal_years():
    fund = F.fundamentals_as_of(_facts("SHEL.L"))
    assert fund.currency == "USD"
    revenue = fund.flow_basis["revenue"]
    assert (revenue.basis, revenue.tag) == ("ttm", "yahoo:TotalRevenue")
    assert revenue.period_end > fund.fy_end
    years = fund.history["revenue"]  # fiscal years only: the trailing figure is not one
    assert 4 <= len(years) <= 5 and max(years) == fund.fy_end.year
    assert fund.latest["shares_outstanding"] > 1e9
    assert fund.instant_dates["shares_outstanding"] > fund.fy_end  # the latest quarter's count
    assert fund.latest["capex"] > 0 and not fund.is_financial


def test_a_half_year_reporter_gets_its_trailing_twelve_months_to_june():
    fund = F.fundamentals_as_of(_facts("ULVR.L"))
    assert fund.currency == "EUR"
    assert fund.flow_basis["revenue"].basis == "ttm"
    assert fund.period_end.month == 6


def test_a_bank_is_financial_because_its_sector_says_so():
    facts = _facts("HSBA.L")
    assert not F.fundamentals_as_of(facts).is_financial  # no deposits or interest-income field from Yahoo
    fund = F.fundamentals_as_of(facts, financial=True)
    assert fund.is_financial and any("financial company" in note for note in fund.notes)


def test_quotes_in_pence_are_a_hundredth_of_a_pound():
    assert yahoo.main_unit("GBp") == ("GBP", 100)
    assert yahoo.main_unit("EUR") == ("EUR", 1)
    assert yahoo.fx_symbol("CHF") == "CHF=X"


def test_the_client_waits_out_a_rate_limit_then_gives_up():
    waited = []
    client = yahoo.YahooClient(min_interval=0, waits=(5, 30), sleep=waited.append)
    answers = iter([YFRateLimitError(), {"timeseries": {"result": []}}])

    def fetch(symbol):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert client._call(fetch, "SAP.DE") == {"timeseries": {"result": []}}
    assert waited == [5] and client.n_requests == 2

    def always_limited(symbol):
        raise YFRateLimitError()

    with pytest.raises(YFRateLimitError):
        client._call(always_limited, "SAP.DE")
    assert waited == [5, 5, 30]
