"""The watchlist: stocks and macro series put on it, charted on the Watchlist page.

Stocks come from the Screener or the Stock explorer, and are the market database's
``watchlist`` table, the one the weekly report reads. Macro series come from the Stock
explorer or the Watchlist page itself, and are its ``watched_series`` table, by the
reference the Stock explorer keeps for a series: ``macro:`` and a catalog id, or
``fred:`` and a FRED id. The pages read both here, cached until the file changes, and
change them here, each change opening the file for writing only for as long as it
takes. DuckDB lets one process write the file at a time, so while a job holds it a
change fails, and the page says why.
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from invest.data.cache import Store
from invest.paths import db_path

COLUMNS = ["symbol", "cik", "note", "added_at"]


def _version() -> float:
    """Cache key: the file's modification time, so a change from outside the app shows too."""
    try:
        return os.path.getmtime(db_path())
    except OSError:
        return 0.0


@st.cache_data(ttl=600, show_spinner=False)
def _read(db: str, version: float) -> pd.DataFrame:
    with Store(db, read_only=True) as store:
        return store.watchlist()


def entries() -> pd.DataFrame:
    """The stocks on the watchlist, first added first: their ``symbol``, ``cik``, ``note`` and ``added_at``.

    Empty while there is no market database. Raises when the file cannot be read, as
    while a job writes it.
    """
    db = db_path()
    if not db.exists():
        return pd.DataFrame(columns=COLUMNS)
    return _read(str(db), _version())


def symbols() -> list[str]:
    """The symbols on the watchlist, upper case, as the table keeps them."""
    return entries()["symbol"].tolist()


def add(symbol: str, *, cik: int | None = None, note: str = "") -> None:
    """Put ``symbol`` on the watchlist. The market database is created if there is none yet.

    Without a ``cik``, the one the screener build stored for the ticker, if any: the
    weekly report finds the insider trades of the watchlist's companies by it.
    """
    with Store(db_path()) as store:
        store.add_watch(symbol, cik=store.cik_of(symbol) if cik is None else cik, note=note)
    _read.clear()


def remove(symbol: str) -> None:
    with Store(db_path()) as store:
        store.remove_watch(symbol)
    _read.clear()


# ==========================================================================================
# Macro series
# ==========================================================================================

SERIES_COLUMNS = ["ref", "note", "added_at"]


@st.cache_data(ttl=600, show_spinner=False)
def _read_series(db: str, version: float) -> pd.DataFrame:
    with Store(db, read_only=True) as store:
        return store.watched_series()


def series_entries() -> pd.DataFrame:
    """The macro series on the watchlist, first added first: their ``ref``, ``note`` and ``added_at``.

    Empty while there is no market database. Raises when the file cannot be read, as
    while a job writes it.
    """
    db = db_path()
    if not db.exists():
        return pd.DataFrame(columns=SERIES_COLUMNS)
    return _read_series(str(db), _version())


def series_refs() -> list[str]:
    """The references of the macro series on the watchlist: ``macro:US_NFCI``, ``fred:NFCI``."""
    return series_entries()["ref"].tolist()


def add_series(ref: str, *, note: str = "") -> None:
    """Put a macro series on the watchlist by its reference. The market database is created if there is none yet."""
    with Store(db_path()) as store:
        store.add_watched_series(ref, note=note)
    _read_series.clear()


def remove_series(ref: str) -> None:
    with Store(db_path()) as store:
        store.remove_watched_series(ref)
    _read_series.clear()
