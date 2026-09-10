"""Form 4 insider transactions: parse the ownership XML, aggregate buys and sells.

Only open-market purchases (transaction code ``P``) and sales (``S``) say
anything about what an insider thinks; grants (``A``), option exercises (``M``),
tax withholding (``F``) and gifts (``G``) are recorded but not counted. Clusters
of buying by several insiders are a known modest positive; selling says little,
and the summary text says so every time.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

import pandas as pd

OPEN_MARKET_BUY = ("P",)
OPEN_MARKET_SELL = ("S",)
CLUSTER_MIN_BUYERS = 3
CLUSTER_WINDOW_DAYS = 90

COLUMNS = [
    "row_id", "issuer_cik", "accession", "filed_at", "trans_date", "owner", "is_director",
    "is_officer", "code", "shares", "price", "acquired", "security", "derivative",
]


def _value(node, path: str) -> str | None:
    found = node.find(path)
    if found is None:
        return None
    inner = found.find("value")
    text = inner.text if inner is not None else found.text
    return text.strip() if text else None


def _flag(node, path: str) -> bool:
    text = _value(node, path)
    return (text or "").strip().lower() in ("1", "true")


def _num(text: str | None) -> float | None:
    if text in (None, ""):
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def parse_form4(data: bytes, *, accession: str, filed_at: date) -> pd.DataFrame:
    """Every transaction in one Form 4 (or 4/A), one row each; empty frame if none."""
    root = ET.fromstring(data)
    issuer_text = _value(root, "issuer/issuerCik")
    issuer_cik = int(issuer_text) if issuer_text else None
    owners = []
    for owner in root.findall("reportingOwner"):
        owners.append(
            {
                "owner": _value(owner, "reportingOwnerId/rptOwnerName"),
                "is_director": _flag(owner, "reportingOwnerRelationship/isDirector"),
                "is_officer": _flag(owner, "reportingOwnerRelationship/isOfficer"),
            }
        )
    if not owners:
        owners = [{"owner": None, "is_director": False, "is_officer": False}]
    primary = owners[0]

    rows = []
    for table, derivative in (("nonDerivativeTable/nonDerivativeTransaction", False),
                              ("derivativeTable/derivativeTransaction", True)):
        for i, tx in enumerate(root.findall(table)):
            trans_date = _value(tx, "transactionDate")
            code = _value(tx, "transactionCoding/transactionCode")
            shares = _num(_value(tx, "transactionAmounts/transactionShares"))
            price = _num(_value(tx, "transactionAmounts/transactionPricePerShare"))
            acquired = (_value(tx, "transactionAmounts/transactionAcquiredDisposedCode") or "").upper() == "A"
            raw = f"{accession}|{table}|{i}|{trans_date}|{code}|{shares}"
            rows.append(
                {
                    "row_id": hashlib.sha1(raw.encode()).hexdigest(),
                    "issuer_cik": issuer_cik,
                    "accession": accession,
                    "filed_at": filed_at,
                    "trans_date": pd.Timestamp(trans_date).date() if trans_date else None,
                    "owner": primary["owner"],
                    "is_director": primary["is_director"],
                    "is_officer": primary["is_officer"],
                    "code": code,
                    "shares": shares,
                    "price": price,
                    "acquired": acquired,
                    "security": _value(tx, "securityTitle"),
                    "derivative": derivative,
                }
            )
    return pd.DataFrame(rows, columns=COLUMNS)


@dataclass(frozen=True)
class InsiderSummary:
    issuer_cik: int | None
    window_start: date | None
    window_end: date | None
    n_filings: int
    n_buys: int
    n_sells: int
    buy_shares: float
    sell_shares: float
    buy_value: float
    sell_value: float
    distinct_buyers: int
    distinct_sellers: int
    cluster_buying: bool
    note: str

    def as_dict(self) -> dict:
        out = self.__dict__.copy()
        for key in ("window_start", "window_end"):
            out[key] = out[key].isoformat() if out[key] else None
        return out


def aggregate(transactions: pd.DataFrame, *, since: date | None = None, until: date | None = None) -> InsiderSummary:
    """Open-market buys versus sells over a window, with the cluster flag.

    A cluster is at least three distinct insiders buying in the open market
    within ninety days of each other. Only non-derivative ``P`` and ``S`` rows
    count; everything else is compensation plumbing.
    """
    tx = transactions.copy()
    issuer = int(tx["issuer_cik"].iloc[0]) if len(tx) and pd.notna(tx["issuer_cik"].iloc[0]) else None
    if since is not None:
        tx = tx[pd.to_datetime(tx["trans_date"]).dt.date >= since]
    if until is not None:
        tx = tx[pd.to_datetime(tx["trans_date"]).dt.date <= until]
    tx = tx[~tx["derivative"].astype(bool)]
    buys = tx[tx["code"].isin(OPEN_MARKET_BUY)]
    sells = tx[tx["code"].isin(OPEN_MARKET_SELL)]

    def value(rows):
        return float((rows["shares"].fillna(0) * rows["price"].fillna(0)).sum())

    cluster = False
    if len(buys):
        dates = sorted(zip(pd.to_datetime(buys["trans_date"]), buys["owner"]))
        for i, (start, _) in enumerate(dates):
            window = {owner for stamp, owner in dates[i:] if (stamp - start).days <= CLUSTER_WINDOW_DAYS}
            if len(window) >= CLUSTER_MIN_BUYERS:
                cluster = True
                break

    if len(tx) == 0:
        note = "No Form 4 transactions in the window."
    else:
        note = (
            f"{len(buys)} open-market purchase(s) by {buys['owner'].nunique()} insider(s) and "
            f"{len(sells)} sale(s) by {sells['owner'].nunique()}; grants, exercises and tax withholding are not counted. "
            + ("Cluster of buying: a known modest positive." if cluster else "No buying cluster. Selling says little on its own.")
        )
    all_dates = pd.to_datetime(tx["trans_date"]).dropna()
    return InsiderSummary(
        issuer_cik=issuer,
        window_start=all_dates.min().date() if len(all_dates) else since,
        window_end=all_dates.max().date() if len(all_dates) else until,
        n_filings=int(tx["accession"].nunique()) if len(tx) else 0,
        n_buys=int(len(buys)),
        n_sells=int(len(sells)),
        buy_shares=float(buys["shares"].fillna(0).sum()),
        sell_shares=float(sells["shares"].fillna(0).sum()),
        buy_value=value(buys),
        sell_value=value(sells),
        distinct_buyers=int(buys["owner"].nunique()),
        distinct_sellers=int(sells["owner"].nunique()),
        cluster_buying=cluster,
        note=note,
    )


def fetch_form4s(client, issuer_cik: int, *, since: date, max_filings: int = 80) -> pd.DataFrame:
    """Every Form 4 filed for an issuer since ``since`` (at most ``max_filings``), parsed."""
    from invest.data.edgar import filings_frame, select_filings

    submissions = client.submissions(issuer_cik)
    refs = select_filings(filings_frame(submissions), issuer_cik, ("4", "4/A"), since=since, limit=max_filings)
    frames = []
    for ref in refs:
        names = client.filing_index(issuer_cik, ref.accession)
        xmls = [n for n in names if n.lower().endswith(".xml")]
        if not xmls:
            continue
        frames.append(parse_form4(client.filing_file(issuer_cik, ref.accession, xmls[0]),
                                  accession=ref.accession, filed_at=ref.filing_date))
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(frames, ignore_index=True)
