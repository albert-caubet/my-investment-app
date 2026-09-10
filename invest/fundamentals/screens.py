"""Screens over a table of company metrics (PLAN.md section 6.3).

Each screen takes the metrics table produced by the fundamentals job (one row
per company, every metric of section 6.2 as a column) and returns ranked rows
with every input still visible: the row is the explanation. Value-trap filters
run first and the excluded rows are returned with their reasons rather than
dropped silently.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

Z_CLIP = 3.0
MIN_SECTOR_SIZE = 5
QUALITY_VALUE_INPUTS = {
    # column: sign (+1 higher is better, -1 lower is better)
    "earnings_yield": 1,
    "fcf_yield": 1,
    "roic": 1,
    "f_score": 1,
    "accruals": -1,
    "gross_margin_slope": 1,
}
MIN_QUALITY_VALUE_INPUTS = 4
DEEP_VALUE_PB = 1.0
DEEP_VALUE_F = 7


def apply_value_trap_filter(table: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split into (kept, excluded); ``excluded`` carries a ``reason`` column."""
    if "value_trap_flags" not in table.columns:
        return table.copy(), table.iloc[0:0].assign(reason=pd.Series(dtype=str))
    flagged = table["value_trap_flags"].map(lambda f: bool(f) if isinstance(f, (list, tuple)) else False)
    excluded = table[flagged].copy()
    excluded["reason"] = excluded["value_trap_flags"].map(lambda f: "; ".join(f))
    return table[~flagged].copy(), excluded


def _numeric(table: pd.DataFrame, column: str) -> pd.Series:
    if column not in table.columns:
        return pd.Series(np.nan, index=table.index, dtype=float)
    return pd.to_numeric(table[column], errors="coerce").astype(float)


def rank_desc(values: pd.Series) -> pd.Series:
    """1 is best (highest). Missing values rank last."""
    return values.rank(ascending=False, method="min", na_option="bottom")


def magic_formula(table: pd.DataFrame, *, min_market_cap: float | None = None, exclude_financials: bool = True) -> pd.DataFrame:
    """Greenblatt: rank on earnings yield and on ROIC, sum the ranks, lowest sum first.

    Financials are excluded by default, as in the book: EBIT and invested capital
    do not describe a bank.
    """
    rows = table.copy()
    if exclude_financials and "is_financial" in rows.columns:
        rows = rows[~rows["is_financial"].fillna(False).astype(bool)]
    if min_market_cap is not None:
        rows = rows[_numeric(rows, "market_cap") >= min_market_cap]
    rows = rows[_numeric(rows, "earnings_yield").notna() & _numeric(rows, "roic").notna()].copy()
    rows["ey_rank"] = rank_desc(_numeric(rows, "earnings_yield"))
    rows["roic_rank"] = rank_desc(_numeric(rows, "roic"))
    rows["magic_rank"] = rows["ey_rank"] + rows["roic_rank"]
    rows = rows.sort_values(["magic_rank", "ey_rank"]).reset_index(drop=True)
    rows.insert(0, "rank", np.arange(1, len(rows) + 1))
    return rows


def zscore_columns(rows: pd.DataFrame, *, sector_neutral: bool = False) -> pd.DataFrame:
    """Signed, clipped z-scores of the quality-value inputs, globally or within sector."""
    out = pd.DataFrame(index=rows.index)
    groups = rows["sector"] if sector_neutral and "sector" in rows.columns else pd.Series("all", index=rows.index)
    sizes = groups.map(groups.value_counts())
    for column, sign in QUALITY_VALUE_INPUTS.items():
        values = _numeric(rows, column) * sign
        z_global = (values - values.mean()) / values.std(ddof=1) if values.notna().sum() > 1 else values * np.nan
        z = z_global.copy()
        if sector_neutral:
            for sector, idx in groups.groupby(groups).groups.items():
                subset = values.loc[idx]
                if subset.notna().sum() >= MIN_SECTOR_SIZE and subset.std(ddof=1) > 0:
                    z.loc[idx] = (subset - subset.mean()) / subset.std(ddof=1)
        out[f"z_{column}"] = z.clip(-Z_CLIP, Z_CLIP)
    out["z_n"] = out.notna().sum(axis=1)
    out["small_sector"] = sizes < MIN_SECTOR_SIZE if sector_neutral else False
    return out


def quality_value(table: pd.DataFrame, *, sector_neutral: bool = False, exclude_financials: bool = True) -> pd.DataFrame:
    """Composite of z-scores: earnings yield, FCF yield, ROIC, F-score, accruals (negative), gross-margin slope."""
    rows = table.copy()
    if exclude_financials and "is_financial" in rows.columns:
        rows = rows[~rows["is_financial"].fillna(False).astype(bool)]
    z = zscore_columns(rows, sector_neutral=sector_neutral)
    rows = pd.concat([rows, z], axis=1)
    z_cols = [f"z_{c}" for c in QUALITY_VALUE_INPUTS]
    rows["composite"] = rows[z_cols].mean(axis=1)
    rows = rows[rows["z_n"] >= MIN_QUALITY_VALUE_INPUTS].sort_values("composite", ascending=False).reset_index(drop=True)
    rows.insert(0, "rank", np.arange(1, len(rows) + 1))
    return rows


def deep_value(table: pd.DataFrame) -> pd.DataFrame:
    """Piotroski's original setting: price to book below 1 with an F-score of 7 or more."""
    rows = table[(_numeric(table, "pb") < DEEP_VALUE_PB) & (_numeric(table, "f_score") >= DEEP_VALUE_F)].copy()
    rows = rows.sort_values(["f_score", "pb"], ascending=[False, True]).reset_index(drop=True)
    rows.insert(0, "rank", np.arange(1, len(rows) + 1))
    return rows


def shareholder_yield_screen(table: pd.DataFrame, *, min_fcf_yield: float = 0.0) -> pd.DataFrame:
    """Dividends plus net buybacks over market cap, highest first, with positive free cash flow."""
    rows = table[_numeric(table, "shareholder_yield").notna()].copy()
    rows = rows[_numeric(rows, "fcf_yield").fillna(-1) >= min_fcf_yield]
    rows = rows.sort_values("shareholder_yield", ascending=False).reset_index(drop=True)
    rows.insert(0, "rank", np.arange(1, len(rows) + 1))
    return rows


SCREENS = {
    "magic_formula": ("Magic formula (earnings yield + ROIC)", magic_formula),
    "quality_value": ("Quality-value composite", quality_value),
    "deep_value": ("Deep value (P/B < 1, F-score >= 7)", deep_value),
    "shareholder_yield": ("Shareholder yield", shareholder_yield_screen),
}


def run_screen(name: str, table: pd.DataFrame, *, filter_traps: bool = True, **options) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(ranked, excluded)`` for a named screen."""
    if name not in SCREENS:
        raise KeyError(f"unknown screen {name!r}; known: {sorted(SCREENS)}")
    kept, excluded = apply_value_trap_filter(table) if filter_traps else (table.copy(), table.iloc[0:0])
    _, fn = SCREENS[name]
    return fn(kept, **options), excluded


def entrants_and_dropouts(current: list[str], previous: list[str] | None, top_n: int = 20) -> tuple[list[str], list[str]]:
    """Ids that entered or left the top ``top_n`` since the previous run."""
    now = list(current)[:top_n]
    if previous is None:
        return [], []
    before = list(previous)[:top_n]
    return [x for x in now if x not in before], [x for x in before if x not in now]
