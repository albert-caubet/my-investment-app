"""Onvista's instrument search: where a Yahoo symbol's ISIN comes from.

Yahoo finds a symbol by its ISIN but never says what a symbol's ISIN is. Onvista's search
does, so ``market_data.fetch_isin`` takes its candidates from here and checks them against
Yahoo. This is the search box of Onvista's website, not a published API, and it can change
without notice: what depends on it must carry on without an ISIN when it fails.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from invest.data.http import fetch_json

#: One search, up to this many results for each kind of instrument it lists.
PER_KIND = 4
SEARCH_URL = "https://api.onvista.de/api/v1/instruments/search/facet?perType={per_kind}&searchValue={text}"

#: The kinds Yahoo quotes too. Onvista also lists bonds, derivatives and futures, which fill
#: a search for any company name with instruments that only mention it.
KINDS = ("STOCK", "ETF", "FUND")


def parse_instruments(payload: dict) -> list[dict]:
    """The stocks, ETFs and funds of a search response that have an ISIN, as ``{isin, symbol, name}``.

    ``symbol`` is the code of Onvista's main listing, mostly a German one (Apple is APC), or None.
    """
    rows = []
    for facet in payload.get("facets") or []:
        if facet.get("type") not in KINDS:
            continue
        for row in facet.get("results") or []:  # a kind with no results has null here
            if row.get("isin"):
                rows.append({"isin": row["isin"], "symbol": row.get("symbol"), "name": row.get("name") or ""})
    return rows


def search_instruments(text: str) -> list[dict]:
    """Search by name, code or ISIN. One quick try, as a click waits for it. Raises ``FetchError`` on failure."""
    url = SEARCH_URL.format(per_kind=PER_KIND, text=quote_plus(text))
    return parse_instruments(fetch_json(url, timeout=10, retries=0))
