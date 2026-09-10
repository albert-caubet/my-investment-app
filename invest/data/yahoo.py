"""Yahoo Finance through yfinance, for the jobs.

Unlike ``market_data.py`` this module has no Streamlit cache: the refresh job
runs once and writes to DuckDB, and the pages read from there. Both close and
adjusted close are kept, because charts want the former and returns the latter.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd
import yfinance as yf


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
