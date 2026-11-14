"""Deutsche Bundesbank time series database: German government and corporate bond yields.

The Bundesbank publishes, every business day and without a key, the yields of German
federal securities at constant maturities (its Svensson-curve estimates) and the
average yields of bonds outstanding by issuer, companies' among them: the only free
daily series of euro investment-grade corporate yields with a long history.

Series keys are written ``FLOW/KEY`` as for the ECB, for example
``BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A`` (the 10-year Bund yield). The
API answers SDMX-CSV like the ECB's, separated by semicolons and after a byte-order mark,
with a "." on the days without a value (weekends and holidays), which are skipped.
"""

from __future__ import annotations

import pandas as pd

from invest.data.ecb import parse_sdmx_csv, split_key
from invest.data.http import fetch_text

BBK_URL = "https://api.statistiken.bundesbank.de/rest/data/{flow}/{key}?format=sdmx_csv"


def parse_csv(text: str) -> pd.DataFrame:
    """A Bundesbank SDMX-CSV response into ``(obs_date, value)``."""
    return parse_sdmx_csv(text, delimiter=";")


def fetch_series(key: str, *, start: str | None = None) -> pd.DataFrame:
    flow, series_key = split_key(key)
    url = BBK_URL.format(flow=flow, key=series_key)
    if start:
        url += f"&startPeriod={start}"
    return parse_csv(fetch_text(url))
