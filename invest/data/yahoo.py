"""Yahoo Finance through yfinance, for the jobs.

Unlike ``market_data.py`` this module has no Streamlit cache: the refresh job
runs once and writes to DuckDB, and the pages read from there. Both close and
adjusted close are kept, because charts want the former and returns the latter.

Company accounts, for the screener's European universe, come from Yahoo's
fundamentals time series, one request per company: four or five fiscal years of
each statement, the latest balance sheets by quarter (or half-year), and Yahoo's
own trailing twelve months of income and cash flow. They carry no filing date and
Yahoo revises them in place, so they are never point-in-time. Parsing is separated
from fetching, as for EDGAR: ``fundamentals_facts`` turns a response into the
store's ``facts`` rows and is tested on a saved one.
"""

from __future__ import annotations

import hashlib
import time
from datetime import date, datetime, timezone
from typing import Callable, Mapping, Sequence
from urllib.parse import quote

import pandas as pd
import yfinance as yf
from yfinance.data import YfData
from yfinance.exceptions import YFRateLimitError

TIMESERIES_URL = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"
#: Yahoo answers with its last four or five fiscal years, whatever the start asked for.
TIMESERIES_START = datetime(2016, 12, 31, tzinfo=timezone.utc)
#: Each series' prefix in a response, and the ``fp`` its rows are stored with.
PERIODS = {"annual": "FY", "quarterly": "Q", "trailing": "TTM"}
#: The period a flow must cover, by prefix. Yahoo labels a half-year balance sheet "3M",
#: so the label is trusted for flows only, where it is checked.
FLOW_PERIOD_TYPES = {"annual": "12M", "trailing": "TTM"}
#: Cash-flow items Yahoo signs as outflows (negative). The canonical fields read payments
#: as positive amounts, as XBRL's ``PaymentsTo...`` tags give them, so the sign is turned.
OUTFLOWS = frozenset({
    "CapitalExpenditure", "PurchaseOfPPE", "CashDividendsPaid", "CommonStockDividendPaid",
    "RepurchaseOfCapitalStock", "CommonStockPayments",
})
#: Quotes in a currency's minor unit, as (currency, minor units per unit): London quotes
#: most shares in pence.
MINOR_UNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}
FACT_COLUMNS = ["fact_id", "cik", "taxonomy", "tag", "unit", "start_date", "end_date", "value", "fy", "fp",
                "form", "filed_at", "accession", "frame"]


def _flatten(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        return frame
    return frame


def fetch_history(
    symbols: Sequence[str], *, start: str | None = None, period: str = "max"
) -> dict[str, pd.DataFrame]:
    """``{symbol: DataFrame(index=date, close, adj_close)}`` for every symbol that returned data.

    Symbols with no data are simply absent from the result; the caller reports
    them as failed. Timezones are stripped so the index is a plain calendar date.
    """
    if not symbols:
        return {}
    kwargs = dict(progress=False, auto_adjust=False, group_by="column", threads=True)
    if start:
        raw = yf.download(list(symbols), start=start, **kwargs)
    else:
        raw = yf.download(list(symbols), period=period, **kwargs)
    if raw is None or raw.empty:
        return {}

    out: dict[str, pd.DataFrame] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for symbol in symbols:
            try:
                close = raw[("Close", symbol)]
            except KeyError:
                continue
            adj = raw[("Adj Close", symbol)] if ("Adj Close", symbol) in raw.columns else close
            frame = pd.DataFrame({"close": close, "adj_close": adj}).dropna(subset=["close"])
            if not frame.empty:
                out[symbol] = _naive_index(frame)
    else:
        close = raw["Close"]
        adj = raw["Adj Close"] if "Adj Close" in raw.columns else close
        frame = pd.DataFrame({"close": close, "adj_close": adj}).dropna(subset=["close"])
        if not frame.empty:
            out[symbols[0]] = _naive_index(frame)
    return out


def _naive_index(frame: pd.DataFrame) -> pd.DataFrame:
    index = pd.DatetimeIndex(frame.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    frame = frame.copy()
    frame.index = index.normalize()
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def fetch_currency(symbol: str) -> str | None:
    """Listing currency from the light chart metadata; ``None`` if unavailable."""
    try:
        code = yf.Ticker(symbol).fast_info["currency"]
    except Exception:
        return None
    return (code or "").upper() or None


# --- currencies -------------------------------------------------------------------------

def main_unit(code: str) -> tuple[str, int]:
    """``(currency, minor units per unit)`` of a quote currency: London's ``GBp`` is ``("GBP", 100)``."""
    return MINOR_UNITS.get(code, (code.upper(), 1))


def fx_symbol(currency: str) -> str:
    """Yahoo's symbol for the US dollar in ``currency``: ``GBP=X`` closes in pounds per dollar."""
    return f"{currency}=X"


# --- company profiles and accounts ------------------------------------------------------

def fetch_profile(symbol: str) -> dict:
    """Name, sector, listing and currencies from Yahoo's quote summary; ``None`` where Yahoo has none."""
    info = yf.Ticker(symbol).get_info() or {}
    return {
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "country": info.get("country"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "currency": info.get("currency"),  # of the quote: GBp for most London listings
        "financial_currency": info.get("financialCurrency"),  # of the accounts
        "quote_type": info.get("quoteType"),
    }


def fundamentals_types(kinds: Mapping[str, str]) -> list[str]:
    """The series to ask for: flows by fiscal year and over the trailing twelve months, balances
    and share counts by fiscal year and at the latest quarter or half-year."""
    out = []
    for key, kind in kinds.items():
        prefixes = ("annual", "trailing") if kind in ("flow", "per_share") else ("annual", "quarterly")
        out += [prefix + key for prefix in prefixes]
    return out


def fetch_fundamentals(symbol: str, kinds: Mapping[str, str]) -> dict:
    """Yahoo's fundamentals time series for ``symbol``, every item of ``kinds`` in one request."""
    params = {
        "symbol": symbol,
        "type": ",".join(fundamentals_types(kinds)),
        "period1": int(TIMESERIES_START.timestamp()),
        "period2": int(time.time()) + 86_400,
    }
    return YfData().get_raw_json(TIMESERIES_URL.format(symbol=quote(symbol)), params=params)


def _fact_id(entity: int, tag: str, unit: str, start: date | None, end: date, fp: str, value: float) -> str:
    """The value is part of the id: fetched again, the same value is the same fact, and a revised
    one a new fact."""
    raw = f"{entity}|yahoo|{tag}|{unit}|{start or ''}|{end}|{fp}|{value!r}"
    return hashlib.sha1(raw.encode()).hexdigest()


def fundamentals_facts(payload: Mapping, entity: int, kinds: Mapping[str, str], *, seen_on: date,
                       currency: str | None = None) -> pd.DataFrame:
    """A fundamentals time-series response as the store's ``facts`` rows, taxonomy ``yahoo``.

    ``kinds`` says what each item is (see ``facts.tag_kinds``); items not in it are skipped.
    A flow spans the year to its date: a fiscal year (``fp`` "FY") or Yahoo's trailing twelve
    months ("TTM"). Balances and share counts have no start; a quarterly one ("Q") at a fiscal
    year end defers to the annual one. Money is in the currency Yahoo gives each value, or
    ``currency`` where it gives none, and a value in no stated currency is left out. Yahoo
    has no filing date: ``filed_at`` is ``seen_on``, the day of the fetch.
    """
    rows = []
    for item in (payload.get("timeseries") or {}).get("result") or []:
        names = (item.get("meta") or {}).get("type") or []
        name = names[0] if names else ""
        prefix = next((p for p in PERIODS if name.startswith(p)), None)
        key = name[len(prefix):] if prefix else ""
        kind = kinds.get(key)
        if kind is None:
            continue
        flow = kind in ("flow", "per_share")
        if flow and prefix not in FLOW_PERIOD_TYPES:
            continue
        for point in item.get(name) or []:
            raw = ((point or {}).get("reportedValue") or {}).get("raw")
            if raw is None or not point.get("asOfDate"):
                continue
            if flow and point.get("periodType") != FLOW_PERIOD_TYPES[prefix]:
                continue
            money = point.get("currencyCode") or currency
            if kind == "shares":
                unit = "shares"
            elif not money:
                continue
            else:
                unit = f"{money}/shares" if kind == "per_share" else money
            end = pd.Timestamp(point["asOfDate"]).date()
            start = (pd.Timestamp(end) - pd.DateOffset(years=1) + pd.Timedelta(days=1)).date() if flow else None
            value = -float(raw) if key in OUTFLOWS else float(raw)
            fp = PERIODS[prefix]
            rows.append({
                "fact_id": _fact_id(entity, key, unit, start, end, fp, value), "cik": int(entity),
                "taxonomy": "yahoo", "tag": key, "unit": unit, "start_date": start, "end_date": end,
                "value": value, "fy": end.year, "fp": fp, "form": f"Yahoo {prefix}", "filed_at": seen_on,
                "accession": f"yahoo-{seen_on:%Y%m%d}", "frame": None,
            })
    annual = {(r["tag"], r["end_date"]) for r in rows if r["fp"] == "FY" and r["start_date"] is None}
    rows = [r for r in rows if not (r["fp"] == "Q" and (r["tag"], r["end_date"]) in annual)]
    if not rows:
        return pd.DataFrame(columns=FACT_COLUMNS)
    return pd.DataFrame(rows, columns=FACT_COLUMNS).drop_duplicates("fact_id").reset_index(drop=True)


class YahooClient:
    """Profiles and accounts per symbol, for the fundamentals job.

    It pauses between requests and, when Yahoo answers that it is getting too many,
    waits ``waits`` seconds in turn before trying again, then gives up on the symbol.
    """

    def __init__(self, *, min_interval: float = 0.2, waits: Sequence[float] = (20, 60, 180),
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self.waits = tuple(waits)
        self.sleep = sleep
        self._last = 0.0
        self.n_requests = 0

    def _call(self, fetch, *args):
        for wait in (*self.waits, None):
            pause = self.min_interval - (time.monotonic() - self._last)
            if pause > 0:
                self.sleep(pause)
            self._last = time.monotonic()
            self.n_requests += 1
            try:
                return fetch(*args)
            except YFRateLimitError:
                if wait is None:
                    raise
                self.sleep(wait)

    def profile(self, symbol: str) -> dict:
        return self._call(fetch_profile, symbol)

    def fundamentals(self, symbol: str, kinds: Mapping[str, str]) -> dict:
        return self._call(fetch_fundamentals, symbol, kinds)
