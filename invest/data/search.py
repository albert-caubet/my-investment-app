"""Find a series to chart: one search over the macro catalog, Yahoo Finance and FRED.

Every hit carries a *reference*, the one string the custom charts page keeps per
series, in its URL too:

- a Yahoo symbol as it is: ``AAPL``, ``^GSPC``, ``IWDA.AS``, ``EURUSD=X``, ``GC=F``;
- ``macro:`` and a catalog id, for a series the refresh job stores: ``macro:US_CPI``;
- ``fred:`` and a FRED id, for any series FRED publishes: ``fred:UNRATE``.

A Yahoo symbol never contains a colon, so the prefixes cannot be mistaken for one.

The catalog is searched here, offline. Yahoo and FRED are asked live, by
``market_data.py``, which caches what they answer; this module turns their answers
into hits and ranks the lot. Everything here is pure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from invest.macro.catalog import GROUPS, Catalog, SeriesSpec

MACRO = "macro:"
FRED = "fred:"

#: Yahoo's quote types worth charting, by the name a reader knows them by. Options
#: are left out: each expires, and has no history worth a chart.
YAHOO_KINDS = {
    "EQUITY": "Stock",
    "ETF": "ETF",
    "MUTUALFUND": "Fund",
    "INDEX": "Index",
    "FUTURE": "Future",
    "CURRENCY": "Currency",
    "CRYPTOCURRENCY": "Crypto",
    "MONEYMARKET": "Money market",
}

#: Catalog sources whose key a reader might type: a FRED id or a Yahoo symbol. The
#: SDMX keys of the ECB, Eurostat, the OECD and the BIS are dimension strings
#: ("une_rt_m?geo=ES&s_adj=SA..."), and their fragments would match almost anything.
_TYPED_KEYS = ("fred", "yahoo")


@dataclass(frozen=True)
class Hit:
    ref: str
    name: str
    kind: str  # Stock, ETF, Fund, Index, Future, Currency, Crypto, Money market, Macro, FRED
    detail: str = ""  # the exchange, the catalog group, or FRED's frequency and units
    unit: str = ""  # what the values are in, where the search says: catalog and FRED hits


def parse_ref(ref: str) -> tuple[str, str]:
    """``(source, key)``: ``("macro", "US_CPI")``, ``("fred", "UNRATE")`` or ``("yahoo", "AAPL")``."""
    for prefix in (MACRO, FRED):
        if ref.startswith(prefix):
            return prefix[:-1], ref[len(prefix):]
    return "yahoo", ref


def tokens(text: str) -> list[str]:
    """Lower-case words of letters and digits: "S&P 500" is ``["s", "p", "500"]``."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _found(words: list[str], text: str) -> int:
    """How many of ``words`` begin a word of ``text``, so "unemploy" finds "unemployment"."""
    have = tokens(text)
    return sum(any(h.startswith(w) for h in have) for w in words)


def catalog_hit(spec: SeriesSpec) -> Hit:
    return Hit(MACRO + spec.id, spec.label, "Macro", GROUPS.get(spec.group, spec.group), spec.units)


def catalog_hits(query: str, catalog: Catalog) -> list[Hit]:
    """Catalog series with every word of the query in their label, id or key, then in their group or notes.

    Within each of the two, the shorter label first: it is the closer match, "S&P 500"
    before "S&P 500 drawdown from high".
    """
    words = tokens(query)
    if not words:
        return []
    ranked = []
    for spec in catalog:
        key = spec.key if spec.source in _TYPED_KEYS and spec.key else ""
        name = f"{spec.label} {spec.id} {key}"
        if _found(words, name) == len(words):
            tier = 0
        elif _found(words, f"{name} {GROUPS.get(spec.group, spec.group)} {spec.notes}") == len(words):
            tier = 1
        else:
            continue
        ranked.append((tier, len(spec.label), catalog_hit(spec)))
    return [hit for *_, hit in sorted(ranked, key=lambda r: r[:2])]


def yahoo_hits(quotes: list[dict]) -> list[Hit]:
    """Yahoo's search quotes as hits, in Yahoo's order, leaving out what cannot be charted."""
    hits = []
    for quote in quotes:
        symbol = str(quote.get("symbol") or "").strip()
        kind = YAHOO_KINDS.get(str(quote.get("quoteType") or "").upper())
        # Not a Yahoo Finance quote (a private company from another source), or a symbol the
        # page could not keep in its URL: a reference is split on commas and read by its colon.
        if not symbol or kind is None or quote.get("isYahooFinance") is False or re.search(r"[\s,:]", symbol):
            continue
        name = quote.get("longname") or quote.get("shortname") or symbol
        hits.append(Hit(symbol, str(name).strip(), kind, str(quote.get("exchDisp") or quote.get("exchange") or "")))
    return hits


def fred_hits(rows: list[dict]) -> list[Hit]:
    """FRED search rows (``invest.data.fred.parse_series_list``) as hits, in FRED's order."""
    hits = []
    for row in rows:
        adjustment = row.get("seasonal_adjustment") or ""
        detail = ", ".join(p for p in (row.get("frequency"), row.get("units"), adjustment) if p and p != "NSA")
        hits.append(Hit(FRED + row["id"], row["title"], "FRED", detail, row.get("units") or ""))
    return hits


def typed_hit(query: str, catalog: Catalog) -> Hit | None:
    """A reference typed out in full: ``fred:`` and a FRED id, or ``macro:`` and a catalog id.

    FRED serves any series by id without a key, so ``fred:CPIAUCSL`` charts even
    where FRED's search, which needs one, is off.
    """
    text = query.strip()
    if text.lower().startswith(FRED):
        series_id = text[len(FRED):].strip().upper()
        if re.fullmatch(r"[A-Z0-9_]+", series_id):
            return Hit(FRED + series_id, series_id, "FRED", "series id typed in")
    elif text.lower().startswith(MACRO):
        spec = catalog.by_id.get(text[len(MACRO):].strip())
        if spec is not None:
            return catalog_hit(spec)
    return None


def _score(query: str, hit: Hit) -> int:
    """3: the query is the hit's symbol or id. 2: every word of it is in the name. 1: some are. 0: none."""
    code = parse_ref(hit.ref)[1]
    if query.strip().lower() in (code.lower(), hit.ref.lower()):
        return 3
    words = tokens(query)
    found = _found(words, f"{hit.name} {code}")
    return 2 if words and found == len(words) else int(found > 0)


def rank(query: str, groups: list[list[Hit]], *, limit: int = 10) -> list[Hit]:
    """Hits from several sources in one list, best first, each reference once.

    Scores across sources come from the same yardstick (:func:`_score`); within a
    score the sources take turns in their own order, so a long run of catalog
    series cannot push Yahoo's best match off the list, nor the reverse. A hit
    that scores 0 still shows, below the rest: Yahoo finds a fund by its ISIN,
    which is in no name.
    """
    scored = sorted(
        ((-_score(query, hit), position, source, hit)
         for source, hits in enumerate(groups) for position, hit in enumerate(hits)),
        key=lambda row: row[:3],
    )
    out: dict[str, Hit] = {}
    for *_, hit in scored:
        out.setdefault(hit.ref, hit)
    return list(out.values())[:limit]
