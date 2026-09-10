"""CFTC Commitments of Traders, legacy futures-only report.

The CFTC publishes one zip per year (``deacot{YYYY}.zip``) holding ``annual.txt``,
a CSV with one row per market and week. "Large speculators" is the
non-commercial category; the indicator is their net position, long minus short,
in contracts. Tuesday positions are released on Friday afternoon.

Market names are the CFTC's own strings, for example
``S&P 500 Consolidated - CHICAGO MERCANTILE EXCHANGE`` (the combined full-size
and E-mini position) and ``USD INDEX - ICE FUTURES U.S.``.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import pandas as pd

from invest.data.http import fetch_bytes

COT_URL = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"

COL_MARKET = "Market and Exchange Names"
COL_DATE = "As of Date in Form YYYY-MM-DD"
COL_OI = "Open Interest (All)"
COL_NC_LONG = "Noncommercial Positions-Long (All)"
COL_NC_SHORT = "Noncommercial Positions-Short (All)"
COL_C_LONG = "Commercial Positions-Long (All)"
COL_C_SHORT = "Commercial Positions-Short (All)"


def parse_cot_csv(text: str) -> pd.DataFrame:
    """The ``annual.txt`` CSV into one row per (market, date) with the position columns."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("empty COT file")
    fields = {f.strip(): f for f in reader.fieldnames}
    for needed in (COL_MARKET, COL_DATE, COL_NC_LONG, COL_NC_SHORT, COL_OI):
        if needed not in fields:
            raise ValueError(f"COT file lacks column {needed!r}")

    def num(row, name):
        raw = (row.get(fields[name]) or "").strip()
        return float(raw) if raw not in ("", ".") else None

    records = []
    for row in reader:
        market = (row.get(fields[COL_MARKET]) or "").strip()
        raw_date = (row.get(fields[COL_DATE]) or "").strip()
        if not market or not raw_date:
            continue
        records.append(
            {
                "market": market,
                "obs_date": pd.Timestamp(raw_date).date(),
                "open_interest": num(row, COL_OI),
                "noncomm_long": num(row, COL_NC_LONG),
                "noncomm_short": num(row, COL_NC_SHORT),
                "comm_long": num(row, COL_C_LONG) if COL_C_LONG in fields else None,
                "comm_short": num(row, COL_C_SHORT) if COL_C_SHORT in fields else None,
            }
        )
    frame = pd.DataFrame(records)
    if frame.empty:
        raise ValueError("COT file has no data rows")
    frame["net_spec"] = frame["noncomm_long"] - frame["noncomm_short"]
    return frame.sort_values(["market", "obs_date"]).reset_index(drop=True)


def read_cot_zip(data: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".txt")]
        if not names:
            raise ValueError(f"no .txt in COT zip: {archive.namelist()}")
        text = archive.read(names[0]).decode("latin-1")
    return parse_cot_csv(text)


def net_speculative(frame: pd.DataFrame, market: str) -> pd.DataFrame:
    """``(obs_date, value)`` of the net non-commercial position for one market."""
    rows = frame[frame["market"] == market]
    if rows.empty:
        known = sorted(frame["market"].unique())
        hint = [m for m in known if market.split(" - ")[0].split()[0].lower() in m.lower()][:5]
        raise ValueError(f"market {market!r} not in COT data; similar: {hint}")
    out = rows[["obs_date", "net_spec"]].rename(columns={"net_spec": "value"})
    return out.dropna().drop_duplicates("obs_date", keep="last").sort_values("obs_date").reset_index(drop=True)


def fetch_years(years: list[int]) -> pd.DataFrame:
    frames = [read_cot_zip(fetch_bytes(COT_URL.format(year=year))) for year in years]
    return pd.concat(frames, ignore_index=True).sort_values(["market", "obs_date"]).reset_index(drop=True)


def fetch_market(market: str, *, years: list[int] | None = None) -> pd.DataFrame:
    """Net speculative position for one market over the given years (default: last ten)."""
    if years is None:
        this_year = date.today().year
        years = list(range(this_year - 9, this_year + 1))
    return net_speculative(fetch_years(years), market)
