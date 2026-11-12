"""Universe config, CIK resolution and the SIC sector map."""

import json
from pathlib import Path

import pytest

from invest.data.edgar import tickers_frame
from invest.fundamentals import universe as U

FIX = Path(__file__).parent / "fixtures"


def test_shipped_universe_loads():
    universes = U.load_universe()
    core = universes["sp500_core"]
    assert len(core.tickers) > 100
    assert "AAPL" in core.tickers and "BRK-B" in core.tickers
    assert len(set(core.tickers)) == len(core.tickers)
    assert universes["watch_examples"].tickers == ("MMM", "JPM", "SAP")


def test_parse_universe_validates():
    with pytest.raises(ValueError):
        U.parse_universe({"universe": {"x": {"label": "empty"}}})
    with pytest.raises(ValueError):
        U.parse_universe({})


def test_csv_tickers_are_merged(tmp_path):
    csv = tmp_path / "list.csv"
    csv.write_text("Ticker,Name\naapl,Apple\nxyz,Some Co\n", encoding="utf-8")
    toml = tmp_path / "u.toml"
    toml.write_text(f'[universe.t]\nlabel = "t"\ntickers = ["MSFT"]\ncsv = "{csv.as_posix()}"\n', encoding="utf-8")
    uni = U.load_universe(toml)["t"]
    assert uni.tickers == ("MSFT", "AAPL", "XYZ")


def test_resolve_ciks_handles_share_class_spelling():
    frame = tickers_frame(json.loads((FIX / "edgar_company_tickers.json").read_text(encoding="utf-8")))
    found, missing = U.resolve_ciks(["AAPL", "brk.b", "NOPE"], frame)
    assert found["AAPL"] == 320193
    assert "NOPE" in missing
    # the SEC lists Berkshire class B as BRK-B; the dotted spelling must still resolve when present
    if "BRK-B" in set(frame["ticker"]):
        assert found.get("brk.b") == frame.loc[frame["ticker"] == "BRK-B", "cik"].iloc[0]


@pytest.mark.parametrize(
    "sic,sector",
    [("3571", "technology hardware"), ("6021", "banks"), ("7372", "software and services"), ("2834", "pharmaceuticals and biotech"),
     ("6798", "real estate"), ("4911", "utilities"), ("5311", "retail"), (None, "unknown"), ("abc", "unknown"), ("3990", "manufacturing")],
)
def test_sector_from_sic(sic, sector):
    assert U.sector_from_sic(sic) == sector


def test_is_financial_sic():
    assert U.is_financial_sic("6021") and U.is_financial_sic(6331)
    assert not U.is_financial_sic("3571") and not U.is_financial_sic(None)


# --- regions and sources ---------------------------------------------------------------------

YAHOO_SUFFIXES = {"L", "PA", "DE", "SW", "AS", "ST", "CO", "HE", "OL", "MI", "MC", "BR", "IR", "VI", "LS", "WA", "AT"}


def test_the_shipped_european_universe_is_yahoo_symbols_and_not_point_in_time():
    universes = U.load_universe()
    eu = universes["stoxx600"]
    assert (eu.region, eu.source, eu.point_in_time) == ("EU", "yahoo", False)
    assert 550 <= len(eu.tickers) <= 650
    assert len(set(eu.tickers)) == len(eu.tickers)
    assert all("." in t and t.rsplit(".", 1)[1] in YAHOO_SUFFIXES for t in eu.tickers)
    ids, clashes = U.resolve_yahoo_ids(eu.tickers)
    assert not clashes and len(set(ids.values())) == len(eu.tickers) and max(ids.values()) < 0
    core = universes["sp500_core"]
    assert (core.region, core.source, core.point_in_time) == ("US", "edgar", True)


def test_parse_universe_reads_region_and_source():
    base = {"label": "x", "tickers": ["SAP.DE"]}
    eu = U.parse_universe({"universe": {"eu": {**base, "region": "eu", "source": "yahoo", "point_in_time": False}}})["eu"]
    assert (eu.region, eu.source, eu.point_in_time) == ("EU", "yahoo", False)
    assert U.parse_universe({"universe": {"y": {**base, "source": "yahoo"}}})["y"].point_in_time is False
    assert U.parse_universe({"universe": {"u": base}})["u"].region == "US"
    with pytest.raises(ValueError, match="not point-in-time"):
        U.parse_universe({"universe": {"y": {**base, "source": "yahoo", "point_in_time": True}}})
    with pytest.raises(ValueError, match="source"):
        U.parse_universe({"universe": {"z": {**base, "source": "bloomberg"}}})
    with pytest.raises(ValueError, match="region"):
        U.parse_universe({"universe": {"z": {**base, "region": "ASIA"}}})


def test_a_yahoo_companys_id_is_negative_and_the_same_everywhere():
    assert U.yahoo_id("SAP.DE") == U.yahoo_id("sap.de") < 0
    assert U.yahoo_id("SAP.DE") != U.yahoo_id("SAP")
    assert -2**31 <= U.yahoo_id("ZZZZ.L") <= -1  # within DuckDB's INTEGER, like the CIKs it sits beside
    ids, clashes = U.resolve_yahoo_ids(["SAP.DE", "SAP.DE", "ASML.AS"])
    assert set(ids) == {"SAP.DE", "ASML.AS"} and clashes == []


def test_each_region_keeps_its_own_screener_table():
    assert U.screener_kind("US") == "screener"  # as before there were regions
    assert U.screener_kind("EU") == "screener_eu"


def test_yahoos_finance_and_real_estate_sectors_are_financial():
    assert U.is_financial_sector("Financial Services") and U.is_financial_sector("real estate")
    assert not U.is_financial_sector("technology") and not U.is_financial_sector(None)
