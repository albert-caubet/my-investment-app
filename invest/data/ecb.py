"""ECB Data Portal, and the SDMX-CSV parser it shares with the OECD and the BIS.

The ECB, the OECD and the BIS all answer SDMX requests with a CSV that has a
``TIME_PERIOD`` and an ``OBS_VALUE`` column and a set of dimension columns. One
parser covers all three; the wrappers only know the URL shape and the key format.

Series keys are written ``FLOW/KEY``, exactly as they appear in the portal URL,
for example ``ICP/M.U2.N.000000.4.ANR`` or ``FM/B.U2.EUR.4F.KR.DFR.LEV``.

Periods are stored as the **first day** of the period they describe (a monthly
value for 2026-08 is dated 2026-08-01, a quarter 2026-Q3 is 2026-07-01), which is
also FRED's convention, so series from different sources align without guesswork.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date

import pandas as pd

from invest.data.http import fetch_text

ECB_URL = "https://data-api.ecb.europa.eu/service/data/{flow}/{key}?format=csvdata"

_QUARTER = re.compile(r"^(\d{4})-?Q([1-4])$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR = re.compile(r"^(\d{4})$")
_WEEK = re.compile(r"^(\d{4})-W(\d{2})$")
_HALF = re.compile(r"^(\d{4})-?[HS]([12])$")


def parse_period(text: str) -> date:
    """An SDMX period string into the first day it covers."""
    text = text.strip()
    if m := _QUARTER.match(text):
        return date(int(m.group(1)), 3 * (int(m.group(2)) - 1) + 1, 1)
    if m := _HALF.match(text):
        return date(int(m.group(1)), 6 * (int(m.group(2)) - 1) + 1, 1)
    if m := _MONTH.match(text):
        return date(int(m.group(1)), int(m.group(2)), 1)
    if m := _YEAR.match(text):
        return date(int(m.group(1)), 1, 1)
    if m := _WEEK.match(text):
        return pd.Timestamp.fromisocalendar(int(m.group(1)), int(m.group(2)), 1).date()
    return pd.Timestamp(text).date()


def parse_sdmx_csv(text: str, *, time_col: str = "TIME_PERIOD", value_col: str = "OBS_VALUE") -> pd.DataFrame:
    """One SDMX-CSV response into ``(obs_date, value)``.

    Refuses a response that contains more than one series (more than one
    distinct ``KEY`` or ``REF_AREA``): merging two countries into one column by
    accident is exactly the kind of silent error the catalog must not allow.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("empty SDMX response")
    fields = [f.strip() for f in reader.fieldnames]
    if time_col not in fields or value_col not in fields:
        raise ValueError(f"SDMX response lacks {time_col}/{value_col}; columns: {fields[:8]}")

    rows = list(reader)
    for dimension in ("KEY", "REF_AREA", "BORROWERS_CTY"):
        if dimension in fields:
            distinct = {(r.get(dimension) or "").strip() for r in rows} - {""}
            if len(distinct) > 1:
                raise ValueError(f"response mixes {len(distinct)} series on {dimension}: {sorted(distinct)[:4]}")
            break

    dates, values = [], []
    for row in rows:
        raw = (row.get(value_col) or "").strip()
        if raw == "":
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        period = (row.get(time_col) or "").strip()
        if not period:
            continue
        dates.append(parse_period(period))
        values.append(value)
    frame = pd.DataFrame({"obs_date": dates, "value": values})
    return frame.drop_duplicates("obs_date", keep="last").sort_values("obs_date").reset_index(drop=True)


def split_key(key: str) -> tuple[str, str]:
    """``"ICP/M.U2.N.000000.4.ANR"`` or ``"ICP.M.U2..."`` into ``(flow, key)``."""
    if "/" in key:
        flow, rest = key.split("/", 1)
        return flow.strip(), rest.strip()
    flow, rest = key.split(".", 1)
    return flow.strip(), rest.strip()


def fetch_series(key: str, *, start: str | None = "1990-01") -> pd.DataFrame:
    flow, series_key = split_key(key)
    url = ECB_URL.format(flow=flow, key=series_key)
    if start:
        url += f"&startPeriod={start}"
    return parse_sdmx_csv(fetch_text(url))


def fetch_series_dict(key: str, *, start: str | None = "2000-01") -> dict[str, float]:
    """``{"YYYY-MM": value}`` for monthly series; the shape the portfolio page expects.

    Empty on any failure, matching the contract of the fetcher it replaces.
    """
    try:
        frame = fetch_series(key, start=start)
    except Exception:
        return {}
    return {f"{d.year:04d}-{d.month:02d}": float(v) for d, v in zip(frame["obs_date"], frame["value"])}
