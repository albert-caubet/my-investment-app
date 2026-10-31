"""Finding a Yahoo symbol's ISIN: Onvista suggests, Yahoo confirms. No network."""

import pytest
import streamlit as st

import market_data as md
from invest.data import onvista
from invest.data.http import FetchError

PORSCHE = {"isin": "DE000PAG9113", "symbol": "P911", "name": "Porsche AG Vz."}
SPACEX = {"isin": "US84615Q1031", "symbol": "SPX", "name": "SpaceX"}


@pytest.fixture(autouse=True)
def fresh_caches():
    st.cache_data.clear()


def _quote(symbol, name="", **extra):
    return {"symbol": symbol, "quoteType": "EQUITY", "longname": name, **extra}


def _sources(monkeypatch, instruments: dict[str, list[dict]], listings: dict[str, list[dict]]) -> dict[str, list[str]]:
    """Canned Onvista answers by search text and Yahoo answers by ISIN, and a record of what each was asked."""
    asked = {"onvista": [], "yahoo": []}

    def search_instruments(text):
        asked["onvista"].append(text)
        return instruments.get(text, [])

    def search_yahoo(query):
        asked["yahoo"].append(query)
        return listings.get(query, [])

    monkeypatch.setattr(onvista, "search_instruments", search_instruments)
    monkeypatch.setattr(md, "search_yahoo", search_yahoo)
    return asked


def test_yahoo_naming_the_symbol_settles_it_and_ends_the_search(monkeypatch):
    asked = _sources(
        monkeypatch, {"P911": [SPACEX, PORSCHE, {"isin": "XX1", "symbol": "X", "name": "Next"}]},
        {"US84615Q1031": [_quote("SPCX", "SpaceX")], "DE000PAG9113": [_quote("P911.DE"), _quote("P911.F")]},
    )
    assert md.fetch_isin("P911.DE", "Dr. Ing. h.c. F. Porsche AG") == "DE000PAG9113"
    assert asked["onvista"] == ["P911"]  # not its name as well
    assert asked["yahoo"] == ["US84615Q1031", "DE000PAG9113"]  # not the row after it


def test_another_listing_of_it_counts(monkeypatch):
    # Yahoo's search for an ISIN names a couple of its listings, not all seven.
    _sources(monkeypatch, {"P911": [PORSCHE]}, {"DE000PAG9113": [_quote("P911.DE", "Dr. Ing. h.c. F. Porsche AG")]})
    assert md.fetch_isin("P911.MU", "Porsche AG") == "DE000PAG9113"


def test_the_same_code_abroad_can_be_another_company(monkeypatch):
    santander = {"isin": "ES0113900J37", "symbol": "BSD2", "name": "Santander"}
    _sources(monkeypatch, {"SAN": [santander]}, {"ES0113900J37": [_quote("SAN.MC", "Banco Santander, S.A.")]})
    with pytest.raises(LookupError, match="SAN.PA"):
        md.fetch_isin("SAN.PA", "Sanofi")


def test_a_us_listing_is_not_the_share_abroad_with_its_code(monkeypatch):
    # SAN on the NYSE is an ADR, with an ISIN of its own.
    santander = {"isin": "ES0113900J37", "symbol": "SAN", "name": "Banco Santander, S.A."}
    _sources(monkeypatch, {"SAN": [santander]}, {"ES0113900J37": [_quote("SAN.MC", "Banco Santander, S.A.")]})
    with pytest.raises(LookupError):
        md.fetch_isin("SAN", "Banco Santander, S.A.")


def test_naming_the_symbol_beats_another_listing_found_first(monkeypatch):
    share = {"isin": "ES0113900J37", "symbol": "BSD2", "name": "Santander"}
    adr = {"isin": "US05964H1059", "symbol": "BSDA", "name": "Santander (ADR)"}
    _sources(
        monkeypatch, {"SAN": [share, adr]},
        {"ES0113900J37": [_quote("SAN.MC", "Banco Santander, S.A.")], "US05964H1059": [_quote("SAN.F", "Banco Santander")]},
    )
    assert md.fetch_isin("SAN.F", "Banco Santander, S.A.") == "US05964H1059"


def test_onvista_listing_the_code_under_a_like_name_will_do_where_yahoo_knows_nothing(monkeypatch):
    vwce = {"isin": "IE00BK5BQT80", "symbol": "VWCE", "name": "Vanguard FTSE All-World UCITS ETF USD Acc."}
    _sources(monkeypatch, {"VWCE": [vwce]}, {})
    assert md.fetch_isin("VWCE.DE", "Vanguard FTSE All-World UCITS ETF USD Accumulation") == "IE00BK5BQT80"


def test_exactly_its_name_will_do_too(monkeypatch):
    eunl = {"isin": "IE00B4L5Y983", "symbol": "EUNL", "name": "iShares Core MSCI World UCITS ETF USD Acc."}
    name = "iShares Core MSCI World UCITS ETF USD (Acc)"
    _sources(monkeypatch, {name: [eunl]}, {})
    assert md.fetch_isin("SWDA.L", name) == "IE00B4L5Y983"


def test_a_share_class_differing_by_a_short_word_will_not(monkeypatch):
    dist = {"isin": "IE000OHHIBC6", "symbol": "91W0", "name": "iShares Core MSCI World UCITS ETF - USD DIS"}
    name = "iShares Core MSCI World UCITS ETF USD (Acc)"
    _sources(monkeypatch, {name: [dist]}, {})
    with pytest.raises(LookupError):
        md.fetch_isin("SWDA.L", name)


def test_onvista_alone_cannot_vouch_for_a_us_listing(monkeypatch):
    sap = {"isin": "DE0007164600", "symbol": "SAP", "name": "SAP SE"}
    _sources(monkeypatch, {"SAP": [sap]}, {})
    with pytest.raises(LookupError):
        md.fetch_isin("SAP", "SAP SE")


def test_the_first_word_of_the_name_is_searched_last(monkeypatch):
    ford = {"isin": "US3453708600", "symbol": "FMC1", "name": "Ford Motor"}
    asked = _sources(monkeypatch, {"Ford": [ford]}, {"US3453708600": [_quote("F", "Ford Motor Company")]})
    assert md.fetch_isin("F", "Ford Motor Company") == "US3453708600"
    assert asked["onvista"] == ["F", "Ford Motor Company", "Ford"]


def test_a_lookup_checks_a_bounded_number_of_candidates(monkeypatch):
    rows = [{"isin": f"XX{n:010d}", "symbol": f"S{n}", "name": "Nothing alike"} for n in range(md.ISIN_CANDIDATES + 5)]
    asked = _sources(monkeypatch, {"ABC": rows}, {})
    with pytest.raises(LookupError):
        md.fetch_isin("ABC", "ABC Corporation")
    assert len(asked["yahoo"]) == md.ISIN_CANDIDATES


def test_a_failure_is_not_remembered(monkeypatch):
    def down(text):
        raise FetchError("https://api.onvista.de: timed out")

    monkeypatch.setattr(onvista, "search_instruments", down)
    with pytest.raises(FetchError):
        md.fetch_isin("P911.DE", "Porsche AG")

    _sources(monkeypatch, {"P911": [PORSCHE]}, {"DE000PAG9113": [_quote("P911.DE")]})
    assert md.fetch_isin("P911.DE", "Porsche AG") == "DE000PAG9113"


# --- Onvista's answer --------------------------------------------------------


# The shape of a real response, shortened: Onvista lists every kind, and an empty one has null results.
PAYLOAD = {
    "expires": 1793480245011,
    "searchValue": "Ford Motor",
    "facets": [
        {"type": "BOND", "label": "Anleihen", "results": [
            {"entityType": "BOND", "isin": "US345370CQ17", "symbol": "FMCA", "name": "Ford Motor Co. DL-Notes 2013(43)"},
        ]},
        {"type": "STOCK", "label": "Aktien", "results": [
            {"entityType": "STOCK", "isin": "US3453708600", "wkn": "502391", "symbol": "FMC1", "name": "Ford Motor"},
            {"entityType": "STOCK", "isin": "CA34539A1066", "name": None},
            {"entityType": "STOCK", "symbol": "NOISIN", "name": "No ISIN"},
        ]},
        {"type": "INDEX", "label": "Indizes", "results": None},
        {"type": "FUND", "label": "Fonds", "results": None},
        {"type": "ETF", "label": "ETFs", "results": [
            {"entityType": "FUND", "isin": "IE00BK5BQT80", "symbol": "VWCE", "name": "Vanguard FTSE All-World"},
        ]},
        {"type": "FUTURE", "label": "Futures", "results": [{"isin": "DE000FUT0001", "name": "Ford future"}]},
    ],
}


def test_only_stocks_funds_and_etfs_with_an_isin_are_read():
    assert onvista.parse_instruments(PAYLOAD) == [
        {"isin": "US3453708600", "symbol": "FMC1", "name": "Ford Motor"},
        {"isin": "CA34539A1066", "symbol": None, "name": ""},
        {"isin": "IE00BK5BQT80", "symbol": "VWCE", "name": "Vanguard FTSE All-World"},
    ]


@pytest.mark.parametrize("payload", [{}, {"facets": None}, {"facets": []}, {"facets": [{"type": "STOCK"}]}])
def test_an_answer_with_nothing_in_it_is_an_empty_list(payload):
    assert onvista.parse_instruments(payload) == []


def test_a_search_escapes_its_text_and_does_not_keep_a_click_waiting(monkeypatch):
    seen = {}

    def fetch_json(url, **kwargs):
        seen.update(url=url, **kwargs)
        return PAYLOAD

    monkeypatch.setattr(onvista, "fetch_json", fetch_json)
    assert onvista.search_instruments("AT&T Inc.")[0]["isin"] == "US3453708600"
    assert seen["url"].endswith("searchValue=AT%26T+Inc.")
    assert seen["timeout"] <= 15 and seen["retries"] == 0
