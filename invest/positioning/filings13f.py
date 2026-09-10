"""13F-HR holdings: parse the information table, diff quarters, summarise.

A 13F lists long positions in US-listed securities, up to 45 days after quarter
end, so every use of it says two things: the positions are at least six weeks
old, and shorts, foreign listings and cash are invisible. The parser reads the
XML information table that filers have submitted since 2013; the primary
document gives the period and the filer.

Values: filings for periods ending after 2022 report market value in dollars;
earlier filings reported thousands. ``holdings_frame`` converts the old ones so
``value_usd`` always means dollars.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

import pandas as pd

INFO_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"
FILER_NS = "http://www.sec.gov/edgar/thirteenffiler"
COMMON_NS = "http://www.sec.gov/edgar/common"
#: Periods ending before this reported values in thousands of dollars.
DOLLARS_FROM = date(2023, 1, 1)
#: Below this median implied price per share (value / shares) a filing is taken to
#: report thousands whatever its period: some filers kept the old convention after
#: 2023 (Baupost, Q2 2026), and a portfolio whose median share price is under one
#: dollar does not exist.
THOUSANDS_MEDIAN_PRICE = 1.0
BIG_CHANGE_PCT = 25.0


def _text(node, path: str, ns: str) -> str | None:
    found = node.find("/".join(f"{{{ns}}}{part}" for part in path.split("/")))
    return found.text.strip() if found is not None and found.text else None


def _num(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def parse_info_table(data: bytes) -> pd.DataFrame:
    """One row per ``infoTable`` entry, values as filed (see :data:`DOLLARS_FROM`)."""
    root = ET.fromstring(data)
    rows = []
    for entry in root.iter(f"{{{INFO_NS}}}infoTable"):
        rows.append(
            {
                "issuer": _text(entry, "nameOfIssuer", INFO_NS),
                "title_class": _text(entry, "titleOfClass", INFO_NS),
                "cusip": (_text(entry, "cusip", INFO_NS) or "").upper(),
                "value": _num(_text(entry, "value", INFO_NS)),
                "shares": _num(_text(entry, "shrsOrPrnAmt/sshPrnamt", INFO_NS)),
                "sh_prn": _text(entry, "shrsOrPrnAmt/sshPrnamtType", INFO_NS),
                "put_call": _text(entry, "putCall", INFO_NS) or "",
                "discretion": _text(entry, "investmentDiscretion", INFO_NS),
                "other_managers": _text(entry, "otherManager", INFO_NS) or "",
                "voting_sole": _num(_text(entry, "votingAuthority/Sole", INFO_NS)),
                "voting_shared": _num(_text(entry, "votingAuthority/Shared", INFO_NS)),
                "voting_none": _num(_text(entry, "votingAuthority/None", INFO_NS)),
            }
        )
    if not rows:
        raise ValueError("no infoTable entries: not a 13F information table")
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class FilingHeader:
    filer_cik: int | None
    filer_name: str | None
    period_of_report: date | None
    report_type: str | None
    is_amendment: bool
    table_entry_total: int | None
    table_value_total: float | None


def parse_primary_doc(data: bytes) -> FilingHeader:
    root = ET.fromstring(data)

    def find(path: str) -> str | None:
        for node in root.iter(f"{{{FILER_NS}}}{path.split('/')[-1]}"):
            if node.text and node.text.strip():
                return node.text.strip()
        return None

    period = find("periodOfReport")
    cik = find("cik")
    return FilingHeader(
        filer_cik=int(cik) if cik else None,
        filer_name=find("name"),
        period_of_report=pd.Timestamp(period).date() if period else None,
        report_type=find("reportType"),
        is_amendment=(find("submissionType") or "").endswith("/A"),
        table_entry_total=int(find("tableEntryTotal")) if find("tableEntryTotal") else None,
        table_value_total=_num(find("tableValueTotal")),
    )


def holdings_frame(
    table: pd.DataFrame,
    *,
    manager_cik: int,
    manager: str,
    period_end: date,
    filed_at: date,
    accession: str,
) -> pd.DataFrame:
    """The store's ``holdings13f`` shape, with values in dollars.

    Positions the same manager lists several times (one per sub-manager) are
    summed by security, so a diff between quarters compares like with like.
    """
    frame = table.copy()
    factor = 1000.0 if reports_thousands(table, period_end) else 1.0
    frame["value_usd"] = frame["value"] * factor
    grouped = (
        frame.groupby(["cusip", "title_class", "put_call"], as_index=False, dropna=False)
        .agg(issuer=("issuer", "first"), value_usd=("value_usd", "sum"), shares=("shares", "sum"),
             sh_prn=("sh_prn", "first"), discretion=("discretion", "first"))
    )
    grouped["manager_cik"] = int(manager_cik)
    grouped["manager"] = manager
    grouped["period_end"] = period_end
    grouped["filed_at"] = filed_at
    grouped["accession"] = accession
    grouped["row_id"] = [
        hashlib.sha1(f"{manager_cik}|{accession}|{r.cusip}|{r.title_class}|{r.put_call}".encode()).hexdigest()
        for r in grouped.itertuples()
    ]
    out = grouped[
        ["row_id", "manager_cik", "manager", "period_end", "filed_at", "accession", "cusip", "issuer",
         "title_class", "value_usd", "shares", "sh_prn", "put_call", "discretion"]
    ]
    out.attrs["value_factor"] = factor
    return out


def reports_thousands(table: pd.DataFrame, period_end: date) -> bool:
    """Whether the value column is in thousands: by period before 2023, by implied price after."""
    if period_end < DOLLARS_FROM:
        return True
    rows = table[(table["shares"] > 0) & (table["sh_prn"].fillna("SH") == "SH") & table["value"].notna()]
    if rows.empty:
        return False
    implied = float((rows["value"] / rows["shares"]).median())
    return implied < THOUSANDS_MEDIAN_PRICE


def diff_quarters(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """Position changes between two quarters of the same manager.

    Keyed by (cusip, class, put/call). ``status`` is new, exit, increased,
    reduced or unchanged; ``big`` marks a share-count change beyond 25%. Sorted
    by absolute dollar change so the table reads top-down.
    """
    key = ["cusip", "title_class", "put_call"]
    prev = previous[key + ["issuer", "shares", "value_usd"]].rename(
        columns={"shares": "shares_prev", "value_usd": "value_prev", "issuer": "issuer_prev"})
    curr = current[key + ["issuer", "shares", "value_usd"]].rename(
        columns={"shares": "shares_curr", "value_usd": "value_curr"})
    merged = prev.merge(curr, on=key, how="outer")
    merged["issuer"] = merged["issuer"].fillna(merged["issuer_prev"])
    merged = merged.drop(columns=["issuer_prev"])
    for col in ("shares_prev", "shares_curr", "value_prev", "value_curr"):
        merged[col] = merged[col].fillna(0.0)
    merged["change_shares"] = merged["shares_curr"] - merged["shares_prev"]
    merged["change_value"] = merged["value_curr"] - merged["value_prev"]

    def pct(row):
        if row["shares_prev"] == 0:
            return None
        return (row["shares_curr"] / row["shares_prev"] - 1.0) * 100.0

    merged["change_pct"] = merged.apply(pct, axis=1)

    def status(row):
        if row["shares_prev"] == 0 and row["shares_curr"] > 0:
            return "new"
        if row["shares_curr"] == 0 and row["shares_prev"] > 0:
            return "exit"
        if row["shares_curr"] > row["shares_prev"]:
            return "increased"
        if row["shares_curr"] < row["shares_prev"]:
            return "reduced"
        return "unchanged"

    merged["status"] = merged.apply(status, axis=1)
    merged["big"] = merged["status"].isin(["new", "exit"]) | (merged["change_pct"].abs() > BIG_CHANGE_PCT)
    merged["abs_change"] = merged["change_value"].abs()
    return merged.sort_values("abs_change", ascending=False).drop(columns="abs_change").reset_index(drop=True)


@dataclass(frozen=True)
class Concentration:
    n_positions: int
    total_value_usd: float
    top1_pct: float
    top5_pct: float
    top10_pct: float
    hhi: float

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def concentration(current: pd.DataFrame) -> Concentration:
    values = current["value_usd"].fillna(0.0).sort_values(ascending=False)
    total = float(values.sum())
    if total <= 0:
        return Concentration(int(len(values)), 0.0, 0.0, 0.0, 0.0, 0.0)
    weights = values / total
    return Concentration(
        n_positions=int(len(values)),
        total_value_usd=total,
        top1_pct=float(weights.iloc[:1].sum() * 100),
        top5_pct=float(weights.iloc[:5].sum() * 100),
        top10_pct=float(weights.iloc[:10].sum() * 100),
        hhi=float((weights ** 2).sum()),
    )


def _money(value: float) -> str:
    if abs(value) >= 1e9:
        return f"${value / 1e9:,.1f}bn"
    if abs(value) >= 1e6:
        return f"${value / 1e6:,.0f}m"
    return f"${value:,.0f}"


def summarise(
    manager: str,
    period_end: date,
    filed_at: date,
    diff: pd.DataFrame,
    conc: Concentration,
    *,
    max_items: int = 5,
) -> str:
    """A paragraph with every number taken from the parsed tables, lag stated first."""
    lag = (filed_at - period_end).days
    lines = [
        f"{manager}: positions as of {period_end.isoformat()}, filed {filed_at.isoformat()} "
        f"({lag} days later; long US positions only, no shorts, cash or foreign listings). "
        f"{conc.n_positions} positions worth {_money(conc.total_value_usd)}; the top position is "
        f"{conc.top1_pct:.0f}% of the portfolio, the top five {conc.top5_pct:.0f}%, the top ten {conc.top10_pct:.0f}%."
    ]
    new = diff[diff["status"] == "new"]
    exits = diff[diff["status"] == "exit"]
    increased = diff[(diff["status"] == "increased") & diff["big"]]
    reduced = diff[(diff["status"] == "reduced") & diff["big"]]

    def items(rows, with_pct=False):
        parts = []
        for _, r in rows.head(max_items).iterrows():
            text = f"{r['issuer']} ({_money(r['change_value'])})"
            if with_pct and r["change_pct"] is not None and pd.notna(r["change_pct"]):
                text = f"{r['issuer']} ({r['change_pct']:+.0f}%, {_money(r['change_value'])})"
            parts.append(text)
        more = len(rows) - min(len(rows), max_items)
        return ", ".join(parts) + (f" and {more} more" if more > 0 else "")

    if len(new):
        lines.append(f"New: {items(new)}.")
    if len(exits):
        lines.append(f"Exited: {items(exits)}.")
    if len(increased):
        lines.append(f"Added more than {BIG_CHANGE_PCT:.0f}%: {items(increased, with_pct=True)}.")
    if len(reduced):
        lines.append(f"Cut more than {BIG_CHANGE_PCT:.0f}%: {items(reduced, with_pct=True)}.")
    if not any(len(x) for x in (new, exits, increased, reduced)):
        lines.append("No new positions, exits or changes above 25% versus the previous quarter.")
    return " ".join(lines)


def with_dates(holdings: pd.DataFrame) -> pd.DataFrame:
    """DuckDB returns DATE columns as timestamps; the rest of the module wants dates."""
    out = holdings.copy()
    for col in ("period_end", "filed_at"):
        if col in out:
            out[col] = pd.to_datetime(out[col]).dt.date
    return out


def latest_two_quarters(holdings: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """The two most recent period_end frames of one manager, oldest first."""
    if holdings.empty:
        return None
    holdings = with_dates(holdings)
    periods = sorted(holdings["period_end"].unique())
    if len(periods) < 2:
        return None
    prev, curr = periods[-2], periods[-1]
    return holdings[holdings["period_end"] == prev].copy(), holdings[holdings["period_end"] == curr].copy()


def fetch_manager_holdings(client, cik: int, *, name_hint: str = "", n_quarters: int = 2) -> list[pd.DataFrame]:
    """Download the latest ``n_quarters`` 13F-HR information tables for one manager.

    Amendments (13F-HR/A) are skipped: they are partial restatements and would
    read as exits of everything they omit.
    """
    from invest.data.edgar import filings_frame, select_filings

    submissions = client.submissions(cik)
    edgar_name = submissions.get("name") or name_hint
    refs = select_filings(filings_frame(submissions), cik, ("13F-HR",), limit=n_quarters)
    frames = []
    for ref in refs:
        names = client.filing_index(cik, ref.accession)
        tables = [n for n in names if n.lower().endswith(".xml") and "primary_doc" not in n.lower()]
        if not tables:
            continue
        table = parse_info_table(client.filing_file(cik, ref.accession, tables[0]))
        period = ref.report_date
        if period is None:
            header = parse_primary_doc(client.filing_file(cik, ref.accession, "primary_doc.xml"))
            period = header.period_of_report
        frames.append(
            holdings_frame(table, manager_cik=cik, manager=edgar_name, period_end=period,
                           filed_at=ref.filing_date, accession=ref.accession)
        )
    return frames
