"""A series by the reference the Stock explorer and the Watchlist page keep for it.

``invest.data.search`` defines the references: a Yahoo symbol as it is, ``macro:`` and a
catalog id, or ``fred:`` and a FRED id. A catalog series is read from the market database
and transformed the way the scorecard reads it, as on the Macro page. A Yahoo or FRED one
is asked of the network through ``market_data``, which keeps what arrives for hours.

A request that fails is kept as failed for five minutes, rather than asked again on every
rerun of a page while the series sits on it.
"""

from __future__ import annotations

import os

import duckdb
import pandas as pd
import streamlit as st

import market_data as md
from invest.data import search
from invest.data.cache import Store
from invest.macro.catalog import Catalog
from invest.macro.derived import apply_transform
from invest.paths import db_path


def _db_version() -> float:
    """Cache key: the file's modification time, so a refresh invalidates what was read."""
    try:
        return os.path.getmtime(db_path())
    except OSError:
        return 0.0


@st.cache_data(ttl=600, show_spinner=False)
def _stored(db: str, version: float, series_id: str, fallback: str | None, transform: str) -> pd.Series:
    """A catalog series as the Macro page charts it: stored, its fallback's history when it has none, transformed."""
    with Store(db, read_only=True) as store:
        raw = store.read_series(series_id)
        if raw.empty and fallback:
            raw = store.read_series(fallback)
    if raw.empty:
        raise LookupError("not stored yet; the refresh on the Macro page fetches it")
    return apply_transform(raw, transform)


@st.cache_data(ttl=300, show_spinner=False)
def _online(ref: str) -> pd.Series | str:
    """The history of a Yahoo or FRED series, or why there is none."""
    source, key = search.parse_ref(ref)
    try:
        return md.fetch_fred_series(key) if source == "fred" else md.fetch_close_history(key)
    except Exception as exc:
        return md.reason(exc)


@st.cache_data(ttl=300, show_spinner=False)
def online_about(ref: str) -> tuple[str, str] | None:
    """Name and unit of a Yahoo or FRED series as its source gives them; None when it does not."""
    source, key = search.parse_ref(ref)
    try:
        if source == "fred":
            meta = md.fetch_fred_meta(key)
            return meta["title"], meta["units"]
        meta = md.fetch_symbol_meta(key)
        return meta["name"], meta["currency"] or ""
    except Exception:  # no FRED key, or the source did not answer
        return None


def history(ref: str, catalog: Catalog) -> pd.Series:
    """The whole history of one series. Raises ``LookupError`` with a reason a reader can act on when there is none."""
    source, key = search.parse_ref(ref)
    if source != "macro":
        got = _online(ref)
        if isinstance(got, str):
            raise LookupError(got)
        return got
    spec = catalog.by_id.get(key)
    if spec is None:
        raise LookupError("not in the catalog, config/series.toml")
    db = db_path()
    if not db.exists():
        raise LookupError("no market database yet; the refresh on the Macro page builds it")
    try:
        return _stored(str(db), _db_version(), key, spec.fallback, spec.transform)
    except duckdb.Error as exc:
        raise LookupError(f"the market database could not be read; if a job is writing it, wait for it ({exc})")


def about(ref: str, catalog: Catalog) -> tuple[str, str]:
    """``(name, unit)``: a catalog series' label and unit, else what Yahoo or FRED says, else its symbol or id alone."""
    source, key = search.parse_ref(ref)
    if source == "macro":
        spec = catalog.by_id.get(key)
        return (spec.label, spec.units) if spec else (key, "")
    return online_about(ref) or (key, "")
