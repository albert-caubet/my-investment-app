"""XBRL facts to canonical fields, as they were known on a date.

Companies tag the same concept differently (``Revenues`` versus
``RevenueFromContractWithCustomerExcludingAssessedTax``, ``NetIncomeLoss`` versus
``ProfitLoss``), switch tags over time, and restate prior years in later
filings. This module resolves each canonical field through an ordered fallback
list, records which tag was used, and answers every question "as of" a date by
using only facts filed on or before it.

Flows come in three shapes: fiscal-year totals, year-to-date totals from 10-Qs,
and single quarters. Trailing twelve months is the previous fiscal year plus the
current year-to-date minus the same year-to-date a year earlier; when the pieces
are not all there, the latest fiscal year is used and the note says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

#: Ordered fallbacks per canonical field. Bare tags are us-gaap.
FIELDS: dict[str, list[str]] = {
    "revenue": [
        "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet",
        "RevenuesNetOfInterestExpense", "ifrs-full:Revenue",
    ],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "ifrs-full:CostOfSales"],
    "gross_profit": ["GrossProfit", "ifrs-full:GrossProfit"],
    "operating_income": ["OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "ifrs-full:ProfitLossBeforeTax",
    ],
    "net_income": [
        "NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
        "ifrs-full:ProfitLossAttributableToOwnersOfParent", "ifrs-full:ProfitLoss",
    ],
    "cfo": [
        "NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "ifrs-full:CashFlowsFromUsedInOperatingActivities",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
        "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    ],
    "depreciation": [
        "DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet",
        "Depreciation", "ifrs-full:DepreciationAndAmortisationExpense",
    ],
    "total_assets": ["Assets", "ifrs-full:Assets"],
    "current_assets": ["AssetsCurrent", "ifrs-full:CurrentAssets"],
    "current_liabilities": ["LiabilitiesCurrent", "ifrs-full:CurrentLiabilities"],
    "total_liabilities": ["Liabilities", "ifrs-full:Liabilities"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndDueFromBanks", "ifrs-full:CashAndCashEquivalents",
    ],
    "short_term_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "CommercialPaper", "ifrs-full:CurrentBorrowings"],
    "debt_noncurrent": [
        "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebtAndFinanceLeaseObligations",
        "LongTermDebt", "ifrs-full:NoncurrentBorrowings", "ifrs-full:Borrowings",
    ],
    "ppe_net": ["PropertyPlantAndEquipmentNet", "ifrs-full:PropertyPlantAndEquipment"],
    "goodwill": ["Goodwill", "ifrs-full:Goodwill"],
    "intangibles": ["IntangibleAssetsNetExcludingGoodwill", "ifrs-full:IntangibleAssetsOtherThanGoodwill"],
    "equity": [
        "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "ifrs-full:EquityAttributableToOwnersOfParent", "ifrs-full:Equity",
    ],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "shares_outstanding": [
        "dei:EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding",
        "WeightedAverageNumberOfDilutedSharesOutstanding", "ifrs-full:NumberOfSharesOutstanding",
    ],
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends", "ifrs-full:DividendsPaidClassifiedAsFinancingActivities"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock", "ifrs-full:PaymentsForPurchaseOfTreasuryShares"],
    "interest_expense": [
        "InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestPaidNet",
        "ifrs-full:InterestExpense", "ifrs-full:FinanceCosts",
    ],
    "income_tax": ["IncomeTaxExpenseBenefit", "ifrs-full:IncomeTaxExpenseContinuingOperations"],
    "eps_diluted": ["EarningsPerShareDiluted", "ifrs-full:DilutedEarningsLossPerShare"],
    "interest_income": ["InterestAndDividendIncomeOperating", "InterestIncomeExpenseNet"],
    "deposits": ["Deposits"],
}

INSTANT_FIELDS = frozenset({
    "total_assets", "current_assets", "current_liabilities", "total_liabilities", "cash",
    "short_term_investments", "debt_current", "debt_noncurrent", "ppe_net", "goodwill", "intangibles",
    "equity", "retained_earnings", "shares_outstanding", "deposits",
})
SHARE_FIELDS = frozenset({"shares_outstanding"})
PER_SHARE_FIELDS = frozenset({"eps_diluted"})
MONETARY_UNITS = ("USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "SEK", "DKK", "NOK", "HKD", "CNY", "KRW", "INR", "BRL", "MXN")
HISTORY_FIELDS = ("revenue", "net_income", "operating_income", "cfo", "capex", "gross_profit", "cost_of_revenue",
                  "shares_outstanding", "total_assets", "equity", "dividends", "buybacks", "depreciation",
                  "income_tax", "pretax_income", "interest_expense", "debt_noncurrent", "debt_current", "cash",
                  "current_assets", "current_liabilities", "retained_earnings", "total_liabilities", "eps_diluted")

FY_DAYS = (340, 380)
YTD_TOLERANCE_DAYS = 12


def split_tag(spec: str) -> tuple[str, str]:
    if ":" in spec:
        taxonomy, tag = spec.split(":", 1)
        return taxonomy, tag
    return "us-gaap", spec


def canonical_tags() -> dict[str, set[str]]:
    """``{taxonomy: {tag, ...}}`` of every tag any canonical field can use."""
    out: dict[str, set[str]] = {}
    for specs in FIELDS.values():
        for spec in specs:
            taxonomy, tag = split_tag(spec)
            out.setdefault(taxonomy, set()).add(tag)
    return out


def facts_as_of(facts: pd.DataFrame, as_of: date | None) -> pd.DataFrame:
    """Only what had been filed on or before ``as_of``."""
    if as_of is None or facts.empty:
        return facts
    filed = pd.to_datetime(facts["filed_at"]).dt.date
    return facts[filed <= as_of]


def _normalise(facts: pd.DataFrame) -> pd.DataFrame:
    out = facts.copy()
    for col in ("start_date", "end_date", "filed_at"):
        out[col] = pd.to_datetime(out[col])
    return out


def _unit_ok(unit: str, field_name: str) -> bool:
    if field_name in SHARE_FIELDS:
        return unit == "shares"
    if field_name in PER_SHARE_FIELDS:
        return "/shares" in unit
    return unit in MONETARY_UNITS


def field_rows(facts: pd.DataFrame, field_name: str) -> pd.DataFrame:
    """Rows of every fallback tag for a field, with a ``priority`` column (0 is best)."""
    frames = []
    for priority, spec in enumerate(FIELDS[field_name]):
        taxonomy, tag = split_tag(spec)
        rows = facts[(facts["taxonomy"] == taxonomy) & (facts["tag"] == tag)]
        rows = rows[rows["unit"].map(lambda u: _unit_ok(u, field_name))]
        if rows.empty:
            continue
        rows = rows.copy()
        rows["priority"] = priority
        rows["spec"] = spec
        frames.append(rows)
    if not frames:
        return pd.DataFrame(columns=list(facts.columns) + ["priority", "spec"])
    return pd.concat(frames, ignore_index=True)


def _best_per_end(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per end date: best tag first, then the most recently filed value."""
    if rows.empty:
        return rows
    ordered = rows.sort_values(["end_date", "priority", "filed_at"], ascending=[True, True, False])
    return ordered.drop_duplicates("end_date", keep="first")


def _is_fy(rows: pd.DataFrame) -> pd.Series:
    days = (rows["end_date"] - rows["start_date"]).dt.days
    return days.between(*FY_DAYS)


def annual_series(facts: pd.DataFrame, field_name: str) -> tuple[pd.Series, dict[pd.Timestamp, str]]:
    """Fiscal-year values indexed by fiscal year end, and the tag used for each.

    Flows: rows spanning a full year. Instants: balance-sheet values at the end
    of a fiscal year (rows tagged ``FY`` or filed in an annual report).
    """
    rows = field_rows(facts, field_name)
    if rows.empty:
        return pd.Series(dtype=float), {}
    if field_name in INSTANT_FIELDS:
        rows = rows[rows["start_date"].isna()]
        annual = rows[(rows["fp"] == "FY") | rows["form"].isin(["10-K", "10-K/A", "20-F", "20-F/A", "40-F"])]
        if field_name == "shares_outstanding":
            # cover-page share counts are dated after the fiscal year end; keep the FY rows only
            annual = annual[annual["fp"] == "FY"]
    else:
        rows = rows[rows["start_date"].notna()]
        annual = rows[_is_fy(rows)]
    best = _best_per_end(annual)
    series = pd.Series(best["value"].astype(float).to_numpy(), index=pd.DatetimeIndex(best["end_date"]), name=field_name)
    tags = dict(zip(best["end_date"], best["spec"]))
    return series.sort_index(), tags


def latest_instant(facts: pd.DataFrame, field_name: str) -> tuple[float | None, date | None, str | None]:
    rows = field_rows(facts, field_name)
    rows = rows[rows["start_date"].isna()] if not rows.empty else rows
    if rows.empty:
        return None, None, None
    best = _best_per_end(rows).sort_values("end_date")
    row = best.iloc[-1]
    return float(row["value"]), row["end_date"].date(), row["spec"]


@dataclass(frozen=True)
class FlowValue:
    value: float | None
    period_end: date | None
    basis: str  # ttm | annual | none
    tag: str | None
    note: str = ""


def _ttm_one_tag(rows: pd.DataFrame, spec: str) -> FlowValue | None:
    """TTM from the rows of a single tag, so a fiscal year and a year-to-date figure
    are never mixed across tags that define the concept differently."""
    rows = rows.assign(days=(rows["end_date"] - rows["start_date"]).dt.days)
    fy_rows = _best_per_end(rows[rows["days"].between(*FY_DAYS)]).sort_values("end_date")
    latest_end = rows["end_date"].max()
    latest_fy = fy_rows.iloc[-1] if not fy_rows.empty else None

    if latest_fy is None:
        return FlowValue(None, latest_end.date(), "none", spec, "no full fiscal year filed yet")
    if latest_fy["end_date"] >= latest_end:
        return FlowValue(float(latest_fy["value"]), latest_fy["end_date"].date(), "annual", spec, "latest fiscal year")

    # the newest data is a year-to-date figure: FY(prev) + YTD(now) - YTD(a year ago)
    ytd = rows[(rows["end_date"] == latest_end) & (rows["start_date"] > latest_fy["end_date"])
               & (rows["start_date"] <= latest_fy["end_date"] + timedelta(days=5))]
    ytd = _best_per_end(ytd)
    if ytd.empty:
        return FlowValue(float(latest_fy["value"]), latest_fy["end_date"].date(), "annual", spec,
                         "latest fiscal year (newer rows are not year-to-date)")
    ytd_row = ytd.iloc[0]
    target_end = latest_end - pd.DateOffset(years=1)
    prior = rows[(rows["days"].between(ytd_row["days"] - YTD_TOLERANCE_DAYS, ytd_row["days"] + YTD_TOLERANCE_DAYS))
                 & ((rows["end_date"] - target_end).abs() <= pd.Timedelta(days=15))]
    prior = _best_per_end(prior)
    if prior.empty:
        return FlowValue(float(latest_fy["value"]), latest_fy["end_date"].date(), "annual", spec,
                         "latest fiscal year (no comparable year-to-date a year earlier)")
    value = float(latest_fy["value"]) + float(ytd_row["value"]) - float(prior.iloc[0]["value"])
    return FlowValue(value, latest_end.date(), "ttm", spec,
                     f"FY {latest_fy['end_date'].date()} + YTD {latest_end.date()} - YTD {prior.iloc[0]['end_date'].date()}")


def ttm(facts: pd.DataFrame, field_name: str) -> FlowValue:
    """Trailing twelve months of a flow field, or the latest fiscal year with a note.

    Each fallback tag is evaluated on its own; a complete TTM beats an annual
    figure, a later period beats an earlier one, and the fallback order decides
    the rest. The tag used travels with the value.
    """
    rows = field_rows(facts, field_name)
    rows = rows[rows["start_date"].notna()] if not rows.empty else rows
    if rows.empty:
        return FlowValue(None, None, "none", None, "no rows for any known tag")
    candidates = []
    for spec, group in rows.groupby("spec", sort=False):
        result = _ttm_one_tag(group, spec)
        if result is not None and result.value is not None:
            candidates.append((result, int(group["priority"].iloc[0])))
    if not candidates:
        latest = rows.sort_values("end_date").iloc[-1]
        return FlowValue(None, latest["end_date"].date(), "none", latest["spec"], "no full fiscal year filed yet")
    candidates.sort(key=lambda item: (item[0].basis != "ttm", -item[0].period_end.toordinal(), item[1]))
    return candidates[0][0]


@dataclass
class Fundamentals:
    cik: int
    as_of: date | None
    currency: str | None
    latest: dict = field(default_factory=dict)      # TTM flows and latest instants
    fy_latest: dict = field(default_factory=dict)   # latest fiscal year flows and FY-end instants
    fy_previous: dict = field(default_factory=dict)
    history: dict = field(default_factory=dict)     # field -> {fiscal year: value}
    tags: dict = field(default_factory=dict)
    flow_basis: dict = field(default_factory=dict)  # field -> FlowValue
    fiscal_years: list = field(default_factory=list)
    period_end: date | None = None
    fy_end: date | None = None
    is_financial: bool = False
    notes: list = field(default_factory=list)

    def history_frame(self) -> pd.DataFrame:
        """Fiscal-year table for the detail view: one row per year, one column per field."""
        years = sorted({y for series in self.history.values() for y in series})
        return pd.DataFrame({f: [self.history[f].get(y) for y in years] for f in self.history}, index=years)


def _fiscal_year(stamp: pd.Timestamp) -> int:
    """Fiscal years are labelled by the calendar year in which they end."""
    return int(stamp.year)


def fundamentals_as_of(facts: pd.DataFrame, as_of: date | None = None, *, sic: str | None = None) -> Fundamentals:
    """Everything the metrics need, using only facts filed on or before ``as_of``."""
    cik = int(facts["cik"].iloc[0]) if len(facts) else 0
    known = _normalise(facts_as_of(facts, as_of))
    fund = Fundamentals(cik=cik, as_of=as_of, currency=None)
    if known.empty:
        fund.notes.append("no facts filed by this date")
        return fund

    fy_ends: list[pd.Timestamp] = []
    for name in FIELDS:
        series, tags = annual_series(known, name)
        annual_tag = None
        if not series.empty:
            fund.history[name] = {_fiscal_year(stamp): float(v) for stamp, v in series.items()}
            annual_tag = tags.get(series.index[-1])
            if name in ("revenue", "net_income", "total_assets"):
                fy_ends.append(series.index[-1])
        if name in INSTANT_FIELDS:
            value, end, tag = latest_instant(known, name)
            fund.latest[name] = value
            chosen = tag or annual_tag
        else:
            flow = ttm(known, name)
            fund.flow_basis[name] = flow
            fund.latest[name] = flow.value
            chosen = flow.tag or annual_tag
        if chosen:
            fund.tags[name] = chosen

    # derived history: margins and free cash flow by fiscal year
    revenue = fund.history.get("revenue", {})
    gp, cogs = fund.history.get("gross_profit", {}), fund.history.get("cost_of_revenue", {})
    oi = fund.history.get("operating_income", {})
    gm_hist, om_hist, fcf_hist = {}, {}, {}
    for year, rev in revenue.items():
        if rev:
            if year in gp:
                gm_hist[year] = gp[year] / rev
            elif year in cogs:
                gm_hist[year] = (rev - cogs[year]) / rev
            if year in oi:
                om_hist[year] = oi[year] / rev
    cfo_hist, capex_hist = fund.history.get("cfo", {}), fund.history.get("capex", {})
    for year, cfo in cfo_hist.items():
        if year in capex_hist:
            fcf_hist[year] = cfo - abs(capex_hist[year])
    if gm_hist:
        fund.history["gross_margin"] = gm_hist
    if om_hist:
        fund.history["operating_margin"] = om_hist
    if fcf_hist:
        fund.history["fcf"] = fcf_hist

    years = sorted({y for f in ("revenue", "net_income", "total_assets") for y in fund.history.get(f, {})})
    fund.fiscal_years = years
    if years:
        latest_year, previous_year = years[-1], (years[-2] if len(years) > 1 else None)
        fund.fy_latest = {f: fund.history[f].get(latest_year) for f in fund.history}
        fund.fy_previous = {f: fund.history[f].get(previous_year) for f in fund.history} if previous_year else {}
        if previous_year and len(years) > 2:
            fund.fy_previous["total_assets_prev"] = fund.history.get("total_assets", {}).get(years[-3])

    revenue_rows = field_rows(known, "revenue")
    if not revenue_rows.empty:
        fund.currency = revenue_rows.sort_values("end_date").iloc[-1]["unit"]
    elif not (ni := field_rows(known, "net_income")).empty:
        fund.currency = ni.sort_values("end_date").iloc[-1]["unit"]

    flow_ends = [fv.period_end for fv in fund.flow_basis.values() if fv.period_end]
    fund.period_end = max(flow_ends) if flow_ends else None
    fund.fy_end = max(fy_ends).date() if fy_ends else None

    sic_code = int(sic) if sic and str(sic).isdigit() else None
    has_bank_shape = fund.latest.get("operating_income") is None and (
        fund.latest.get("deposits") is not None or fund.latest.get("interest_income") is not None)
    fund.is_financial = bool(has_bank_shape or (sic_code is not None and 6000 <= sic_code <= 6799))
    if fund.is_financial:
        fund.notes.append("financial company: EBIT-based metrics do not apply; ranked on book value and payout only")
    missing = [f for f in ("revenue", "net_income", "total_assets", "shares_outstanding") if fund.latest.get(f) is None]
    if missing:
        fund.notes.append("missing core fields: " + ", ".join(missing))
    return fund
