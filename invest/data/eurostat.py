"""Eurostat dissemination API (JSON-stat 2.0).

A catalog key is the dataset plus its filters in URL form, for example
``une_rt_m?geo=EA21&s_adj=SA&age=TOTAL&unit=PC_ACT&sex=T``. The filters must pin
every dimension except time to one category; the parser refuses anything else,
because a two-country response flattened into one column is wrong in a way that
no downstream check would catch.

Euro area codes move with membership: ``EA21`` since Bulgaria joined in 2026,
``EA20`` before. The catalog names a fallback series for that case.
"""

from __future__ import annotations

import pandas as pd

from invest.data.ecb import parse_period
from invest.data.http import fetch_json

EUROSTAT_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{query}"


def parse_jsonstat(payload: dict) -> pd.DataFrame:
    """A single-series JSON-stat dataset into ``(obs_date, value)``."""
    if payload.get("class") != "dataset" or "dimension" not in payload:
        raise ValueError(f"not a JSON-stat dataset: {str(payload)[:200]}")
    ids = payload["id"]
    sizes = payload["size"]
    if "time" not in ids:
        raise ValueError("dataset has no time dimension")
    for dim, size in zip(ids, sizes):
        if dim != "time" and size != 1:
            labels = list(payload["dimension"][dim]["category"]["index"])
            raise ValueError(f"dimension {dim!r} is not pinned to one category: {labels[:5]}")
    time_index = payload["dimension"]["time"]["category"]["index"]
    # index maps period -> position; positions are the flat value indices because
    # every other dimension has size 1.
    position_to_period = {int(pos): period for period, pos in time_index.items()}
    values = payload.get("value") or {}
    dates, obs = [], []
    for pos, value in values.items():
        if value is None:
            continue
        period = position_to_period.get(int(pos))
        if period is None:
            continue
        dates.append(parse_period(period))
        obs.append(float(value))
    frame = pd.DataFrame({"obs_date": dates, "value": obs})
    return frame.sort_values("obs_date").reset_index(drop=True)


def fetch_series(query: str, *, since: str | None = None) -> pd.DataFrame:
    url = EUROSTAT_URL.format(query=query)
    url += ("&" if "?" in url else "?") + "format=JSON&lang=EN"
    if since:
        url += f"&sinceTimePeriod={since}"
    return parse_jsonstat(fetch_json(url))
