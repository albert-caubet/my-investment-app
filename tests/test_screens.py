"""Screens on a small synthetic metrics table with hand-checkable ranks."""

import numpy as np
import pandas as pd
import pytest

from invest.fundamentals import screens as sc


def _table():
    return pd.DataFrame(
        [
            # ticker, sector, fin, mcap, ey, roic, fcf_y, f, accruals, gm_slope, pb, sh_yield, traps
            ("A", "retail", False, 10e9, 0.12, 0.30, 0.08, 8, -0.02, 0.01, 2.0, 0.06, []),
            ("B", "retail", False, 20e9, 0.08, 0.40, 0.05, 7, 0.00, 0.02, 3.0, 0.04, []),
            ("C", "software", False, 5e9, 0.15, 0.10, 0.10, 6, 0.01, -0.01, 0.9, 0.02, []),
            ("D", "software", False, 8e9, 0.04, 0.50, 0.02, 9, -0.05, 0.03, 8.0, 0.01, []),
            ("E", "banks", True, 50e9, None, None, None, 7, None, None, 0.8, 0.07, []),
            ("F", "retail", False, 1e9, 0.20, 0.35, 0.12, 3, 0.10, -0.02, 0.5, 0.00, ["Piotroski F-score 3 at or below 3"]),
            ("G", "retail", False, 3e9, 0.06, 0.20, 0.04, 7, 0.02, 0.00, 0.95, 0.03, []),
        ],
        columns=["ticker", "sector", "is_financial", "market_cap", "earnings_yield", "roic", "fcf_yield", "f_score",
                 "accruals", "gross_margin_slope", "pb", "shareholder_yield", "value_trap_flags"],
    )


def test_value_trap_filter_returns_reasons():
    kept, excluded = sc.apply_value_trap_filter(_table())
    assert excluded["ticker"].tolist() == ["F"]
    assert "F-score" in excluded["reason"].iloc[0]
    assert "F" not in kept["ticker"].tolist()


def test_magic_formula_ranks_by_sum_of_ranks_and_skips_banks():
    ranked, excluded = sc.run_screen("magic_formula", _table())
    assert "E" not in ranked["ticker"].tolist()  # financial
    assert "F" not in ranked["ticker"].tolist()  # value trap
    # EY ranks among A, B, C, D, G: C(0.15)=1, A(0.12)=2, B(0.08)=3, G(0.06)=4, D(0.04)=5
    # ROIC ranks: D(0.5)=1, B(0.4)=2, A(0.3)=3, G(0.2)=4, C(0.1)=5
    # sums: A=5, B=5, C=6, D=6, G=8 -> ties broken by EY rank: A before B, C before D
    assert ranked["ticker"].tolist() == ["A", "B", "C", "D", "G"]
    assert ranked["magic_rank"].tolist() == [5, 5, 6, 6, 8]
    assert ranked["rank"].tolist() == [1, 2, 3, 4, 5]


def test_magic_formula_min_market_cap():
    ranked = sc.magic_formula(_table(), min_market_cap=6e9)
    assert set(ranked["ticker"]) == {"A", "B", "D"}


def test_quality_value_composite_is_mean_of_signed_z():
    ranked, _ = sc.run_screen("quality_value", _table())
    assert "E" not in ranked["ticker"].tolist()
    kept = _table()[~_table()["is_financial"] & (_table()["ticker"] != "F")]
    ey = kept["earnings_yield"]
    expected_z_ey = ((ey - ey.mean()) / ey.std(ddof=1)).clip(-3, 3)
    row = ranked.set_index("ticker")
    assert row.loc["A", "z_earnings_yield"] == pytest.approx(expected_z_ey[kept["ticker"] == "A"].iloc[0])
    # accruals enter with a negative sign: the lowest accruals (D, -0.05) get the highest z
    assert row["z_accruals"].idxmax() == "D"
    z_cols = [c for c in ranked.columns if c.startswith("z_") and c != "z_n"]
    assert row.loc["A", "composite"] == pytest.approx(row.loc["A", z_cols].mean())
    assert ranked["composite"].is_monotonic_decreasing


def test_quality_value_sector_neutral_falls_back_for_small_sectors():
    table = _table()
    ranked = sc.quality_value(table, sector_neutral=True)
    # every sector here has fewer than five members, so global z-scores are used and flagged
    assert ranked["small_sector"].all()
    big = pd.concat([table.assign(ticker=table["ticker"] + str(i), sector="retail") for i in range(3)], ignore_index=True)
    big = big[~big["is_financial"]]
    neutral = sc.quality_value(big, sector_neutral=True)
    assert not neutral["small_sector"].any()
    # within one sector, z-scores have mean zero
    assert neutral["z_earnings_yield"].mean() == pytest.approx(0.0, abs=1e-9)


def test_deep_value_and_shareholder_yield():
    deep, _ = sc.run_screen("deep_value", _table())
    assert deep["ticker"].tolist() == ["E", "G"]  # P/B below 1 with F >= 7; F (0.5) is a trap, C has F = 6
    sy, _ = sc.run_screen("shareholder_yield", _table())
    assert sy["ticker"].iloc[0] == "A"  # E has the highest yield but no FCF yield recorded
    sy_all, _ = sc.run_screen("shareholder_yield", _table(), min_fcf_yield=-1.0)
    assert sy_all["ticker"].iloc[0] == "E"


def test_unknown_screen_and_entrants():
    with pytest.raises(KeyError):
        sc.run_screen("alpha", _table())
    entrants, dropouts = sc.entrants_and_dropouts(["A", "B", "C"], ["B", "C", "D"], top_n=3)
    assert entrants == ["A"] and dropouts == ["D"]
    assert sc.entrants_and_dropouts(["A"], None) == ([], [])
