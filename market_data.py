"""Every network call and every cache lives here.

Keeping this separate from ``database.py`` and ``portfolio_math.py`` is deliberate:
an FX helper that silently returned 1.0 on failure hid inside ``database.py`` for a
long time precisely because nobody looks for market-data behaviour in a file called
"database".

Cached functions take **tuples**, not lists, so the cache key is immutable.
"""

from __future__ import annotations

import csv
import io
import urllib.request

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

from portfolio_math import BASE_CCY

# Benchmark and risk-free are separated from the spot-price fetch on purpose: one
# fetch serving both current prices and the returns series is how a change made for
# price freshness silently destroyed the analytics.
BENCHMARK_TICKER = "^GSPC"
BENCHMARK_CCY = "USD"
CAPM_PERIOD = "2y"
SPOT_PERIOD = "7d"

#: US 10Y, shown for reference. Not used in CAPM -- it is the wrong currency for a
#: EUR investor, and beta is insensitive to it anyway (a constant shift cancels out
#: of the covariance), so only alpha would be affected.
US_10Y_TICKER = "^TNX"

#: Explicit euro-area risk-free assumption. A stated constant is more honest than a
#: wrong-currency series fetched with false precision.
RF_ANNUAL_DEFAULT = 0.02


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


# ---------------------------------------------------------------------------
# Spot prices and FX
# ---------------------------------------------------------------------------

@st.cache_data(ttl=900, show_spinner=False)
def fetch_spot_prices(tickers: tuple[str, ...]) -> dict[str, float]:
    """Latest close per ticker.

    Forward-filling is *correct here* -- tolerating a stale print is the whole point
    of a spot lookup for slow-moving funds. It is only wrong when the same frame is
    then differenced into a returns series, which is why that now lives elsewhere.
    """
    if not tickers:
        return {}
    raw = yf.download(list(tickers), period=SPOT_PERIOD, progress=False)
    if raw.empty:
        return {}
    close = _flatten(raw["Close"]).ffill()
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])
    last = close.iloc[-1]
    return {
        str(k): float(v)
        for k, v in last.items()
        if v is not None and np.isfinite(v)
    }


@st.cache_data(ttl=900, show_spinner=False)
def fetch_spot_fx(currencies: tuple[str, ...]) -> dict[str, float]:
    """``{ccy: units per 1 EUR}``. Unavailable legs are omitted, never faked."""
    rates: dict[str, float] = {BASE_CCY: 1.0}
    wanted = [c for c in {c.upper() for c in currencies} if c and c != BASE_CCY]
    if not wanted:
        return rates
    spot = fetch_spot_prices(tuple(f"{BASE_CCY}{c}=X" for c in wanted))
    for ccy in wanted:
        value = spot.get(f"{BASE_CCY}{ccy}=X")
        if value and np.isfinite(value) and value > 0:
            rates[ccy] = value
    return rates


@st.cache_data(ttl=2_592_000, show_spinner=False)  # 30 days; historical rates are immutable
def fetch_historical_fx(date_iso: str, quote_ccy: str) -> float | None:
    """Rate on `date_iso`, as *units of quote_ccy per 1 EUR*.

    Returns ``None`` when no rate can be established. It must never fall back to
    1.0: that silently books a USD trade at par and freezes a ~15% understated cost
    basis into the database forever.

    The window looks *backwards* and takes the last bar at or before the date, so a
    weekend or holiday resolves to the prior close rather than the next day's rate.
    """
    ccy = (quote_ccy or "").upper()
    if ccy == BASE_CCY:
        return 1.0

    target = pd.Timestamp(date_iso).normalize()
    try:
        raw = yf.download(
            f"{BASE_CCY}{ccy}=X",
            start=(target - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
            end=(target + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),  # end is exclusive
            progress=False,
        )
    except Exception:
        return None
    if raw.empty:
        return None

    close = _flatten(raw)["Close"].dropna()
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    index = close.index
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    close = close[index <= target]
    if close.empty:
        return None
    value = float(close.iloc[-1])
    return value if np.isfinite(value) and value > 0 else None


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86_400, show_spinner=False)
def fetch_listing_info(tickers: tuple[str, ...]) -> dict[str, dict]:
    """Listing currency and display name per ticker.

    The listing currency is what the quoted price is denominated in, and is the only
    currency valuation may key off.
    """
    out: dict[str, dict] = {}
    for symbol in tickers:
        if "^" in symbol or "=" in symbol:
            continue
        try:
            info = yf.Ticker(symbol).info or {}
            out[symbol] = {
                "currency": (info.get("currency") or "").upper() or None,
                "name": info.get("displayName")
                or info.get("shortName")
                or info.get("longName"),
            }
        except Exception:
            out[symbol] = {"currency": None, "name": None}
    return out


@st.cache_data(ttl=86_400, show_spinner=False)
def resolve_isin(isin: str) -> str | None:
    """Resolve an ISIN to a Yahoo symbol, once.

    yfinance resolves ISINs by issuing a live *search*, which can return a different
    exchange listing than the one actually traded -- in a different currency. Doing
    it at entry time and storing the result keeps that search off the pricing path.
    """
    try:
        from yfinance import utils as yf_utils

        symbol = yf_utils.get_ticker_by_isin(isin)
        return symbol or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# History (for charts and CAPM)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=43_200, show_spinner=False)
def fetch_price_history(
    tickers: tuple[str, ...], period: str, *, adjusted: bool = True
) -> pd.DataFrame:
    """Close-price panel.

    ``adjusted=True`` (split/dividend adjusted) is right for return series.
    ``adjusted=False`` is right for charts that plot stored raw trade prices, since
    an adjusted series and a raw trade price are not on the same scale.
    """
    if not tickers:
        return pd.DataFrame()
    raw = yf.download(
        list(tickers), period=period, progress=False, auto_adjust=adjusted
    )
    if raw.empty:
        return pd.DataFrame()
    close = _flatten(raw["Close"])
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])
    return close


@st.cache_data(ttl=43_200, show_spinner=False)
def fetch_fx_history(currencies: tuple[str, ...], period: str) -> pd.DataFrame:
    """FX panel as *units per 1 EUR*, one column per currency."""
    wanted = [c for c in {c.upper() for c in currencies} if c and c != BASE_CCY]
    if not wanted:
        return pd.DataFrame()
    panel = fetch_price_history(
        tuple(f"{BASE_CCY}{c}=X" for c in wanted), period, adjusted=True
    )
    if panel.empty:
        return panel
    return panel.rename(columns={f"{BASE_CCY}{c}=X": c for c in wanted})


def to_eur_panel(
    prices: pd.DataFrame,
    listing_ccy: dict[str, str | None],
    fx_panel: pd.DataFrame,
) -> pd.DataFrame:
    """Convert a local-currency price panel into EUR.

    FX is forward-filled *onto the equity calendar*, never the reverse. Reindexing
    equities onto an FX calendar would manufacture flat days wherever a market was
    closed but FX still traded, which quietly deflates covariance.
    """
    if prices.empty:
        return prices
    out = prices.copy()
    for column in out.columns:
        ccy = (listing_ccy.get(column) or BASE_CCY).upper()
        if ccy == BASE_CCY:
            continue
        if fx_panel.empty or ccy not in fx_panel.columns:
            out[column] = np.nan
            continue
        aligned = fx_panel[ccy].reindex(out.index).ffill().bfill()
        out[column] = out[column] / aligned
    return out


@st.cache_data(ttl=3_600, show_spinner=False)
def fetch_us_10y() -> float | None:
    """US 10Y yield as a decimal. Displayed for reference only."""
    spot = fetch_spot_prices((US_10Y_TICKER,))
    value = spot.get(US_10Y_TICKER)
    return value / 100.0 if value else None


# ---------------------------------------------------------------------------
# Inflation (ECB Data Portal, no API key required)
# ---------------------------------------------------------------------------

#: Harmonised Index of Consumer Prices. "ES" is Spain, "U2" the euro area.
HICP_AREA = "ES"
HICP_AREA_LABEL = {"ES": "Spain", "U2": "euro area"}

_ECB_ICP = "https://data-api.ecb.europa.eu/service/data/ICP"


def _fetch_icp(key: str) -> dict[str, float]:
    """One ICP series as ``{"YYYY-MM": value}``. Empty dict on any failure.

    Note the series does not necessarily reach the present day: at the time of
    writing it ends in 2025-12, and the data carries a notice about
    methodological changes from February 2026. Callers must treat the coverage as
    a fact to be checked, not assumed -- presenting a months-old figure as though
    it were current is the whole failure mode this is guarding against.
    """
    url = f"{_ECB_ICP}/{key}?startPeriod=2000-01&format=csvdata"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "my-investment-app"})
        with urllib.request.urlopen(request, timeout=20) as response:
            text = response.read().decode("utf-8", "replace")
    except Exception:
        return {}

    series: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        period = (row.get("TIME_PERIOD") or "").strip()
        try:
            series[period] = float(row.get("OBS_VALUE"))
        except (TypeError, ValueError):
            continue
    return series


@st.cache_data(ttl=86_400, show_spinner=False)
def fetch_hicp_index(area: str = HICP_AREA) -> dict[str, float]:
    """Monthly HICP index levels, for restating past amounts in later euros."""
    return _fetch_icp(f"M.{area}.N.000000.4.INX")


@st.cache_data(ttl=86_400, show_spinner=False)
def fetch_hicp_annual_rate(area: str = HICP_AREA) -> tuple[float, str] | None:
    """Latest annual inflation as a decimal, with the month it refers to.

    The month is returned alongside deliberately: this figure is only honest when
    displayed with its date.
    """
    series = _fetch_icp(f"M.{area}.N.000000.4.ANR")
    if not series:
        return None
    month = max(series)
    return series[month] / 100.0, month
