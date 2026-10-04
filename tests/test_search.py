"""The search behind the custom charts page: references, the catalog search, Yahoo's and
FRED's answers as hits, and one ranking over all three. Pure; no network."""

import json
from pathlib import Path

import pytest

from invest.data import search
from invest.data.fred import parse_series_list
from invest.data.search import Hit
from invest.macro.catalog import load_catalog

FIX = Path(__file__).parent / "fixtures"
CATALOG = load_catalog()


def _refs(hits):
    return [h.ref for h in hits]


def _yahoo(symbol, kind="EQUITY", name=None, exchange="NASDAQ", **extra):
    return {"symbol": symbol, "quoteType": kind, "longname": name or symbol, "exchDisp": exchange, **extra}


# --- references ----------------------------------------------------------------


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("AAPL", ("yahoo", "AAPL")),
        ("^GSPC", ("yahoo", "^GSPC")),
        ("EURUSD=X", ("yahoo", "EURUSD=X")),
        ("macro:US_CPI", ("macro", "US_CPI")),
        ("fred:UNRATE", ("fred", "UNRATE")),
    ],
)
def test_a_reference_names_its_source(ref, expected):
    assert search.parse_ref(ref) == expected


def test_words_are_letters_and_digits():
    assert search.tokens("S&P 500") == ["s", "p", "500"]
    assert search.tokens("EUR/USD") == ["eur", "usd"]


# --- the catalog ---------------------------------------------------------------


def test_the_catalog_is_searched_by_label():
    refs = _refs(search.catalog_hits("unemployment", CATALOG))
    assert {"macro:US_UNRATE", "macro:EA_UNRATE", "macro:ES_UNRATE"} <= set(refs)


def test_a_word_begun_is_enough():
    assert "macro:US_UNRATE" in _refs(search.catalog_hits("us unemploy", CATALOG))


def test_every_word_must_match():
    refs = _refs(search.catalog_hits("spain unemployment", CATALOG))
    assert "macro:ES_UNRATE" in refs
    assert "macro:US_UNRATE" not in refs


def test_the_closest_label_comes_first():
    """Of the labels that hold every word, the shortest is the closest: "S&P 500" itself."""
    assert search.catalog_hits("s&p 500", CATALOG)[0].ref == "macro:SPX"


def test_a_fred_id_or_yahoo_symbol_finds_its_catalog_series():
    """Neither is in the label: US_CPI is "US CPI, YoY", NIKKEI is "Nikkei 225"."""
    assert "macro:US_CPI" in _refs(search.catalog_hits("CPIAUCSL", CATALOG))
    assert "macro:NIKKEI" in _refs(search.catalog_hits("^N225", CATALOG))


def test_sdmx_keys_are_not_searched():
    """Spain's unemployment comes from Eurostat with key une_rt_m?geo=ES&s_adj=SA...: no word of it should match."""
    assert "macro:ES_UNRATE" not in _refs(search.catalog_hits("geo", CATALOG))


def test_a_catalog_hit_says_what_its_values_are_in():
    hit = next(h for h in search.catalog_hits("us cpi", CATALOG) if h.ref == "macro:US_CPI")
    assert (hit.name, hit.kind, hit.detail, hit.unit) == ("US CPI, YoY", "Macro", "Inflation", "%")


# --- Yahoo and FRED ----------------------------------------------------------------


def test_yahoo_quotes_become_hits_in_yahoos_order():
    hits = search.yahoo_hits([
        _yahoo("AAPL", name="Apple Inc."),
        _yahoo("IWDA.AS", kind="ETF", name="iShares Core MSCI World UCITS ETF USD (Acc)", exchange="Amsterdam"),
        {"symbol": "0P0001EI1P.F", "quoteType": "MUTUALFUND", "shortname": "Candriam Bds", "exchange": "FRA"},
    ])
    assert hits == [
        Hit("AAPL", "Apple Inc.", "Stock", "NASDAQ"),
        Hit("IWDA.AS", "iShares Core MSCI World UCITS ETF USD (Acc)", "ETF", "Amsterdam"),
        Hit("0P0001EI1P.F", "Candriam Bds", "Fund", "FRA"),  # the short name, and the exchange code, when that is all
    ]


def test_what_cannot_be_charted_is_left_out():
    hits = search.yahoo_hits([
        _yahoo("AAPL261218C00200000", kind="OPTION"),  # expires
        _yahoo("OPENAI", kind="PRIVATE_COMPANY"),  # no quote type a chart knows
        _yahoo("XYZ", isYahooFinance=False),  # from another source
        _yahoo("A,B"),  # a comma would split it in the page's URL
        _yahoo(""),
        _yahoo("^IBEX", kind="INDEX"),
    ])
    assert _refs(hits) == ["^IBEX"]


def test_fred_rows_become_hits_with_their_units():
    hits = search.fred_hits(parse_series_list(json.loads((FIX / "fred_series_search.json").read_text("utf-8"))))
    assert hits[0] == Hit("fred:UNRATE", "Unemployment Rate", "FRED", "Monthly, %, SA", "%")
    assert hits[2].detail == "Monthly, %"  # "not seasonally adjusted" goes without saying


@pytest.mark.parametrize("typed", ["fred:CPIAUCSL", "fred:cpiaucsl", " FRED:CPIAUCSL "])
def test_a_fred_id_typed_in_full_is_a_hit_without_any_search(typed):
    assert search.typed_hit(typed, CATALOG) == Hit("fred:CPIAUCSL", "CPIAUCSL", "FRED", "series id typed in")


def test_a_catalog_id_typed_in_full_is_its_series():
    assert search.typed_hit("macro:US_CPI", CATALOG).name == "US CPI, YoY"


@pytest.mark.parametrize("typed", ["fred:", "fred:CPI AUCSL", "macro:NOT_A_SERIES", "apple"])
def test_anything_else_is_searched(typed):
    assert search.typed_hit(typed, CATALOG) is None


# --- ranking -----------------------------------------------------------------------


def test_the_exact_symbol_comes_first():
    yahoo = search.yahoo_hits([_yahoo("APLE", name="Apple Hospitality REIT"), _yahoo("AAPL", name="Apple Inc.")])
    assert _refs(search.rank("aapl", [yahoo]))[0] == "AAPL"


def test_a_name_with_every_word_comes_before_one_without():
    yahoo = search.yahoo_hits([_yahoo("JUNK", name="Something Else"), _yahoo("URTH", kind="ETF", name="iShares MSCI World ETF")])
    assert _refs(search.rank("msci world", [yahoo])) == ["URTH", "JUNK"]


def test_sources_take_turns_so_none_crowds_out_the_others():
    """The catalog holds many S&P 500 series; Yahoo's index must still make the list."""
    catalog = search.catalog_hits("s&p 500", CATALOG)
    assert len(catalog) > 5
    yahoo = search.yahoo_hits([_yahoo("^GSPC", kind="INDEX", name="S&P 500", exchange="SNP")])
    ranked = _refs(search.rank("s&p 500", [catalog, yahoo], limit=5))
    assert ranked[:2] == ["macro:SPX", "^GSPC"]


def test_a_hit_matching_nothing_by_name_still_shows():
    """Yahoo finds a fund by its ISIN, which is in no name."""
    yahoo = search.yahoo_hits([_yahoo("IWDA.L", kind="ETF", name="iShares Core MSCI World UCITS ETF")])
    assert _refs(search.rank("IE00B4L5Y983", [search.catalog_hits("IE00B4L5Y983", CATALOG), yahoo])) == ["IWDA.L"]


def test_each_reference_once_and_no_more_than_the_limit():
    yahoo = search.yahoo_hits([_yahoo(f"S{n}", name="Apple") for n in range(20)])
    ranked = search.rank("apple", [yahoo, yahoo], limit=10)
    assert _refs(ranked) == [f"S{n}" for n in range(10)]
