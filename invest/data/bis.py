"""BIS data portal: credit-to-GDP gaps and total credit, quarterly.

Keys are ``FLOW/KEY`` as in the portal's API link, for example
``WS_CREDIT_GAP/Q.US.P.A.C`` for the United States credit-to-GDP gap
(private non-financial sector, all lenders, gap in percentage points).
"""

from __future__ import annotations

import pandas as pd

from invest.data.ecb import parse_sdmx_csv, split_key
from invest.data.http import fetch_text

BIS_URL = "https://stats.bis.org/api/v1/data/{flow}/{key}/all?format=csv"


def fetch_series(key: str, *, start: str | None = "1990-01-01") -> pd.DataFrame:
    flow, series_key = split_key(key)
    url = BIS_URL.format(flow=flow, key=series_key)
    if start:
        url += f"&startPeriod={start}"
    return parse_sdmx_csv(fetch_text(url))
