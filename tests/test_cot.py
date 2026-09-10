"""CFTC Commitments of Traders parser on a real yearly file subset."""

from datetime import date
from pathlib import Path

import pytest

from invest.positioning.cot import net_speculative, parse_cot_csv

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def cot():
    return parse_cot_csv((FIX / "cftc_deacot_subset.csv").read_text(encoding="utf-8"))


def test_parse_has_one_row_per_market_and_week(cot):
    assert {"market", "obs_date", "noncomm_long", "noncomm_short", "net_spec", "open_interest"} <= set(cot.columns)
    assert not cot.duplicated(["market", "obs_date"]).any()
    assert all(isinstance(x, date) for x in cot["obs_date"])


def test_net_speculative_is_long_minus_short(cot):
    sp = net_speculative(cot, "S&P 500 Consolidated - CHICAGO MERCANTILE EXCHANGE")
    row = cot[cot["market"].str.startswith("S&P 500 Consolidated")].iloc[0]
    assert sp.iloc[0]["value"] == row["noncomm_long"] - row["noncomm_short"]
    assert sp["obs_date"].is_monotonic_increasing


def test_unknown_market_names_similar_ones(cot):
    with pytest.raises(ValueError, match="similar"):
        net_speculative(cot, "S&P 500 Micro - NOWHERE")


def test_parse_rejects_a_file_without_the_position_columns():
    with pytest.raises(ValueError):
        parse_cot_csv("a,b\n1,2\n")
