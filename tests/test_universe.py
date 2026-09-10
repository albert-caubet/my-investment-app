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
