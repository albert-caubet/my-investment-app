"""OECD SDMX API (composite leading indicators, confidence indicators).

Keys are the full dataflow-plus-key path as it appears in the OECD data
explorer's API link, for example
``OECD.SDD.STES,DSD_STES@DF_CLI,/USA.M.LI...AA...H`` for the amplitude-adjusted
CLI of the United States. One key must select one series.
"""

from __future__ import annotations

import pandas as pd

from invest.data.ecb import parse_sdmx_csv
from invest.data.http import fetch_text

OECD_URL = "https://sdmx.oecd.org/public/rest/data/{key}?format=csvfilewithlabels"


def fetch_series(key: str, *, start: str | None = "1990-01") -> pd.DataFrame:
    url = OECD_URL.format(key=key)
    if start:
        url += f"&startPeriod={start}"
    return parse_sdmx_csv(fetch_text(url, headers={"Accept": "application/vnd.sdmx.data+csv"}))
