"""FRED and ALFRED.

Two routes to the same numbers:

- ``fredgraph.csv`` needs no key and returns the full current-vintage history of
  a series. It is the default, so the tool works out of the box.
- The JSON API needs a free key (``FRED_API_KEY``) and adds what the CSV cannot
  give: **vintages**. ``fetch_vintages`` asks for every real-time period, which
  is what a point-in-time backtest of CPI, unemployment or claims needs. The key
  also opens FRED's own **search** over every series it publishes, which the
  custom charts page offers next to Yahoo's.

The parsers are pure and tested on saved responses. A blank or ``"."`` value is
a holiday or a not-yet-published observation, and is dropped rather than filled.
"""

from __future__ import annotations

import csv
import io
from urllib.parse import quote_plus

import pandas as pd

from invest.data.http import fetch_json, fetch_text

FREDGRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
API_URL = (
    "https://api.stlouisfed.org/fred/series/observations"
    "?series_id={series_id}&api_key={api_key}&file_type=json"
)
SEARCH_URL = (
    "https://api.stlouisfed.org/fred/series/search"
    "?search_text={text}&api_key={api_key}&file_type=json&limit={limit}"
)
SERIES_URL = "https://api.stlouisfed.org/fred/series?series_id={series_id}&api_key={api_key}&file_type=json"


def parse_fredgraph_csv(text: str) -> pd.DataFrame:
    """``observation_date,<ID>`` (older exports say ``DATE``) into ``(obs_date, value)``."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("empty FRED response")
    if len(header) < 2 or header[0].strip().lower() not in ("observation_date", "date"):
        raise ValueError(f"unexpected FRED header: {header[:3]}")
    dates, values = [], []
    for row in reader:
        if len(row) < 2:
            continue
        raw = row[1].strip()
        if raw in ("", "."):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        dates.append(row[0].strip())
        values.append(value)
    frame = pd.DataFrame({"obs_date": pd.to_datetime(dates).date, "value": values})
    return frame.sort_values("obs_date").reset_index(drop=True)


def parse_api_observations(payload: dict) -> pd.DataFrame:
    """JSON observations into ``(obs_date, value, vintage_date)``.

    ``vintage_date`` is ``realtime_start``: the first day this value was the
    published one. Without a realtime range in the request every row carries
    today's date, which is the right answer for a current-vintage fetch.
    """
    rows = payload.get("observations")
    if rows is None:
        raise ValueError(f"unexpected FRED API payload: {str(payload)[:200]}")
    dates, values, vintages = [], [], []
    for row in rows:
        raw = (row.get("value") or "").strip()
        if raw in ("", "."):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        dates.append(row["date"])
        values.append(value)
        vintages.append(row.get("realtime_start") or row["date"])
    frame = pd.DataFrame(
        {
            "obs_date": pd.to_datetime(dates).date,
            "value": values,
            "vintage_date": pd.to_datetime(vintages).date,
        }
    )
    return frame.sort_values(["obs_date", "vintage_date"]).reset_index(drop=True)


def fetch_series(series_id: str, *, api_key: str | None = None) -> pd.DataFrame:
    """Current-vintage history as ``(obs_date, value)``."""
    if api_key:
        payload = fetch_json(API_URL.format(series_id=series_id, api_key=api_key))
        return parse_api_observations(payload)[["obs_date", "value"]]
    return parse_fredgraph_csv(fetch_text(FREDGRAPH_URL.format(series_id=series_id)))


def fetch_vintages(series_id: str, *, api_key: str) -> pd.DataFrame:
    """Every vintage of every observation (ALFRED). Needs an API key."""
    url = API_URL.format(series_id=series_id, api_key=api_key)
    url += "&realtime_start=1776-07-04&realtime_end=9999-12-31"
    return parse_api_observations(fetch_json(url))


def parse_series_list(payload: dict) -> list[dict]:
    """The series of a search or series response, with what a reader picks one by.

    FRED lists them under ``seriess``, with a double s. The frequency is its first
    word ("Weekly, Ending Friday" is "Weekly"); the units and the seasonal adjustment
    are FRED's short forms where it gives them ("%", "SA"): they label a chart without
    crowding it.
    """
    rows = payload.get("seriess")
    if rows is None:
        raise ValueError(f"unexpected FRED API payload: {str(payload)[:200]}")
    return [
        {
            "id": str(row["id"]),
            "title": row.get("title") or str(row["id"]),
            "frequency": (row.get("frequency") or row.get("frequency_short") or "").split(",")[0].strip(),
            "units": row.get("units_short") or row.get("units") or "",
            "seasonal_adjustment": row.get("seasonal_adjustment_short") or "",
            "observation_start": row.get("observation_start"),
            "observation_end": row.get("observation_end"),
        }
        for row in rows
        if row.get("id")
    ]


def search_series(text: str, *, api_key: str, limit: int = 10) -> list[dict]:
    """FRED's full-text search over every series it publishes, most relevant first. Needs an API key."""
    return parse_series_list(fetch_json(SEARCH_URL.format(text=quote_plus(text), api_key=api_key, limit=int(limit))))


def series_info(series_id: str, *, api_key: str) -> dict:
    """Title, frequency and units of one series. Needs an API key."""
    rows = parse_series_list(fetch_json(SERIES_URL.format(series_id=quote_plus(series_id), api_key=api_key)))
    if not rows:
        raise ValueError(f"FRED has no series {series_id!r}")
    return rows[0]
