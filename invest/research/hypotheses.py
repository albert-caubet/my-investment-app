"""Scripts that measure the hypotheses of PLAN.md section 9 on the stored data.

    python -m invest.research.hypotheses [H1|H5|H6|H7|H8|H11|rules] [--db PATH]

Each script reads the store, returns a dictionary with its numbers, n and the
code version, prints it, and saves it as a ``research`` snapshot. Nothing here
is a trading rule; these are measurements to update ``config/hypotheses.md``
by hand once read.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date

import numpy as np
import pandas as pd

from invest.data.cache import Store
from invest.macro.catalog import load_catalog
from invest.macro.derived import yoy
from invest.research import events, walkforward


def code_version() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _stamp(result: dict, name: str) -> dict:
    result.update({"hypothesis": name, "computed": date.today().isoformat(), "code_version": code_version()})
    return result


# ---------------------------------------------------------------------------
# H1: inflation disguised as growth
# ---------------------------------------------------------------------------

def h1_real_earnings(store: Store) -> dict:
    earnings, cpi = store.read_series("SHILLER_EARNINGS"), store.read_series("SHILLER_CPI")
    if earnings.empty or cpi.empty:
        return {"error": "SHILLER_EARNINGS or SHILLER_CPI not stored"}
    frame = pd.concat([earnings.rename("e"), cpi.rename("cpi")], axis=1, sort=True).dropna()
    annual = frame.resample("YE").last()
    decades = []
    for decade, block in annual.groupby(annual.index.year // 10 * 10):
        if len(block) < 5:
            continue
        years = len(block) - 1
        nominal = (block["e"].iloc[-1] / block["e"].iloc[0]) ** (1 / years) - 1 if block["e"].iloc[0] > 0 and years > 0 else np.nan
        inflation = (block["cpi"].iloc[-1] / block["cpi"].iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
        real = (1 + nominal) / (1 + inflation) - 1 if pd.notna(nominal) else np.nan
        decades.append({"decade": int(decade), "years": years, "nominal_eps_growth": float(nominal),
                        "inflation": float(inflation), "real_eps_growth": float(real),
                        "share_of_nominal_that_is_inflation": float(inflation / nominal) if nominal else None})
    dxy = store.read_series("DXY")
    dollar = None
    if not dxy.empty:
        eps_yoy = yoy(earnings).resample("YE").last()
        dxy_yoy = yoy(dxy).resample("YE").last()
        pair = pd.concat([eps_yoy.rename("eps"), dxy_yoy.rename("dxy")], axis=1, sort=True).dropna()
        if len(pair) >= 10:
            slope, intercept = np.polyfit(pair["dxy"], pair["eps"], 1)
            dollar = {"n_years": int(len(pair)), "correlation": float(pair.corr().iloc[0, 1]),
                      "eps_growth_per_pct_dxy": float(slope), "note": "annual EPS growth regressed on annual DXY change; negative slope means a weaker dollar lifts reported earnings"}
    return _stamp({"decades": decades, "dollar_translation": dollar,
                   "n_months": int(len(frame)), "first": frame.index[0].date().isoformat(), "last": frame.index[-1].date().isoformat()}, "H1")


# ---------------------------------------------------------------------------
# H5 and rule hit rates
# ---------------------------------------------------------------------------

RULE_INPUTS = ["US_CURVE_10Y3M", "US_SAHM", "US_CLAIMS_4WK_YOY", "US_CLAIMS_OFF_LOW", "US_HY_OAS", "US_HY_OAS_3M_CHG",
               "SP500_CAPE", "SPX_VS_200D", "SPX_200D_SLOPE", "US_CFNAI_MA3", "US_SLOOS_TIGHTENING", "US_EBP",
               "US_PERMITS", "US_POLICY_STANCE", "US_ISM_MFG_PMI", "US_ISM_NEW_ORDERS_MINUS_INVENTORIES",
               "US_ISM_SERVICES_PMI", "US_LEI_6M_ANN", "US_LEI_DIFFUSION", "AAII_BULL_BEAR", "NAAIM_EXPOSURE"]


def rule_scoreboard(store: Store, *, rules: list[str] | None = None, start: str = "1970-01-01") -> dict:
    catalog = load_catalog()
    usrec = store.read_series("US_RECESSION")
    if usrec.empty:
        return {"error": "US_RECESSION not stored"}
    series = {sid: store.read_series(sid) for sid in RULE_INPUTS}
    series = {k: v for k, v in series.items() if not v.empty}
    lags = {sid: catalog.stale_after(catalog[sid]) or 0 for sid in series if sid in catalog.by_id}
    # the staleness limit includes two periods and a grace; the publication lag alone is the catalog field
    lags = {sid: catalog[sid].lag_days for sid in series if sid in catalog.by_id}
    board = walkforward.scoreboard(series, usrec, lag_days=lags, rules=rules, start=start)
    return _stamp({"rows": board.to_dict(orient="records"), "recession_starts": [d.date().isoformat() for d in walkforward.recession_starts(usrec)],
                   "lead_months": walkforward.DEFAULT_LEAD_MONTHS, "publication_lags_days": lags}, "rules")


def h5_claims_lead(store: Store) -> dict:
    result = rule_scoreboard(store, rules=["claims_yoy", "claims_off_low", "sahm"])
    if "rows" in result:
        result["hypothesis"] = "H5"
        result["note"] = "claims rules against the Sahm rule as recession calls; n is the number of recession starts in the sample"
    return result


# ---------------------------------------------------------------------------
# H6: liquidity drives prices
# ---------------------------------------------------------------------------

def h6_liquidity(store: Store) -> dict:
    liquidity, spx = store.read_series("US_NET_LIQUIDITY_13W"), store.read_series("SPX")
    if liquidity.empty or spx.empty:
        return {"error": "US_NET_LIQUIDITY_13W or SPX not stored"}
    weekly_spx = spx.resample("W-WED").last().dropna()
    forward = (weekly_spx.shift(-13) / weekly_spx - 1.0).rename("fwd_13w")
    frame = pd.concat([liquidity.resample("W-WED").last().rename("liq"), forward], axis=1, sort=True).dropna()

    def block(rows: pd.DataFrame, label: str) -> dict:
        if len(rows) < 20:
            return {"period": label, "n_weeks": int(len(rows)), "correlation": None}
        return {"period": label, "n_weeks": int(len(rows)), "correlation": float(rows.corr().iloc[0, 1]),
                "fwd_return_when_liquidity_rising": float(rows.loc[rows["liq"] > 0, "fwd_13w"].mean()) if (rows["liq"] > 0).any() else None,
                "fwd_return_when_liquidity_falling": float(rows.loc[rows["liq"] <= 0, "fwd_13w"].mean()) if (rows["liq"] <= 0).any() else None}

    periods = [block(frame, "all"), block(frame[frame.index < "2020-01-01"], "before 2020"),
               block(frame[(frame.index >= "2020-01-01") & (frame.index < "2023-01-01")], "2020 to 2022"),
               block(frame[frame.index >= "2023-01-01"], "since 2023")]
    return _stamp({"periods": periods, "note": "13-week change in net liquidity against the following 13-week S&P 500 return; overlapping windows, so n overstates independence"}, "H6")


# ---------------------------------------------------------------------------
# H7: stress events
# ---------------------------------------------------------------------------

def h7_stress_events(store: Store) -> dict:
    spx = store.read_series("SPX")
    if spx.empty:
        return {"error": "SPX not stored"}
    out = {}
    hy = store.read_series("US_HY_OAS")
    if not hy.empty:
        out["hy_oas_spike_z2"] = events.event_study(spx, events.spike_events(hy, z=2.0)).as_dict()
    baa = store.read_series("US_BAA_10Y")
    if not baa.empty:
        # the ICE BofA spread on FRED covers three years; the Baa spread reaches back to 1986
        out["baa_spread_spike_z2"] = events.event_study(spx, events.spike_events(baa, z=2.0)).as_dict()
    vix = store.read_series("VIX")
    if not vix.empty:
        out["vix_above_30"] = events.event_study(spx, events.crossing_events(vix, above=30.0)).as_dict()
    curve = store.read_series("US_CURVE_10Y3M")
    if not curve.empty:
        out["curve_uninversion"] = events.event_study(spx, events.uninversion_events(curve)).as_dict()
    return _stamp({"studies": out, "horizons_days": list(events.DEFAULT_HORIZONS),
                   "note": "forward S&P 500 returns after each event against an unconditional baseline sampled every 30 days; events closer than the longest horizon collapsed"}, "H7")


# ---------------------------------------------------------------------------
# H8: the longer the crash is delayed, the bigger it is
# ---------------------------------------------------------------------------

def drawdown_episodes(real_price: pd.Series, *, threshold: float = 0.20) -> pd.DataFrame:
    """Peak-to-trough episodes deeper than ``threshold``, with the gap since the previous episode ended."""
    s = real_price.dropna().sort_index()
    peak, peak_date = -np.inf, None
    episodes = []
    in_drawdown, trough, trough_date = False, None, None
    for stamp, value in s.items():
        if value >= peak:
            if in_drawdown:
                episodes.append({"peak": peak_date, "trough": trough_date, "recovered": stamp, "depth": trough / peak - 1.0})
                in_drawdown = False
            peak, peak_date = value, stamp
            trough, trough_date = value, stamp
            continue
        if value < trough:
            trough, trough_date = value, stamp
        if not in_drawdown and value / peak - 1.0 <= -threshold:
            in_drawdown = True
    frame = pd.DataFrame(episodes)
    if frame.empty:
        return frame
    frame["years_since_previous_trough"] = (frame["peak"] - frame["trough"].shift(1)).dt.days / 365.25
    return frame


def h8_drawdown_gaps(store: Store) -> dict:
    price, cpi = store.read_series("SHILLER_PRICE"), store.read_series("SHILLER_CPI")
    if price.empty or cpi.empty:
        return {"error": "SHILLER_PRICE or SHILLER_CPI not stored"}
    frame = pd.concat([price.rename("p"), cpi.rename("cpi")], axis=1, sort=True).dropna()
    real = frame["p"] / frame["cpi"] * frame["cpi"].iloc[-1]
    episodes = drawdown_episodes(real)
    if episodes.empty:
        return _stamp({"n": 0}, "H8")
    pairs = episodes.dropna(subset=["years_since_previous_trough"])
    result = {"n_episodes": int(len(episodes)), "n_pairs": int(len(pairs)),
              "episodes": [{"peak": r.peak.date().isoformat(), "trough": r.trough.date().isoformat(), "depth": float(r.depth),
                            "years_since_previous_trough": None if pd.isna(r.years_since_previous_trough) else float(r.years_since_previous_trough)}
                           for r in episodes.itertuples()]}
    if len(pairs) >= 4:
        slope, intercept = np.polyfit(pairs["years_since_previous_trough"], pairs["depth"], 1)
        result.update({"correlation": float(pairs[["years_since_previous_trough", "depth"]].corr().iloc[0, 1]),
                       "depth_change_per_year_of_delay": float(slope),
                       "note": "depth is negative; a negative slope means longer gaps came with deeper drawdowns"})
    return _stamp(result, "H8")


# ---------------------------------------------------------------------------
# H11: buying dips versus holding
# ---------------------------------------------------------------------------

def simulate_contributions(price: pd.Series, *, rule: str, start: str | None = None) -> dict:
    """One unit of cash each month; deployed at once (hold), or held until a dip signal fires.

    Rules: ``hold``; ``dip10`` and ``dip20`` deploy the accumulated cash when price is
    10% or 20% below its running high; ``below200d`` deploys when price sits under its
    200-day (10-month) average. Cash earns nothing, which favours the hold rule.
    """
    s = price.dropna().sort_index()
    if start:
        s = s[s.index >= pd.Timestamp(start)]
    monthly = s.resample("MS").first().dropna()
    high = monthly.cummax()
    ma = monthly.rolling(10).mean()
    units, cash, invested = 0.0, 0.0, 0.0
    for stamp, p in monthly.items():
        cash += 1.0
        invested += 1.0
        deploy = rule == "hold" or (rule == "dip10" and p <= 0.9 * high[stamp]) or (rule == "dip20" and p <= 0.8 * high[stamp]) \
            or (rule == "below200d" and pd.notna(ma[stamp]) and p < ma[stamp])
        if deploy and cash > 0:
            units += cash / p
            cash = 0.0
    terminal = units * monthly.iloc[-1] + cash
    years = (monthly.index[-1] - monthly.index[0]).days / 365.25
    return {"rule": rule, "n_months": int(len(monthly)), "invested": invested, "terminal": float(terminal),
            "multiple": float(terminal / invested) if invested else None, "years": float(years),
            "cash_left_uninvested": float(cash)}


def h11_dip_buying(store: Store) -> dict:
    price, cpi = store.read_series("SHILLER_PRICE"), store.read_series("SHILLER_CPI")
    if price.empty:
        return {"error": "SHILLER_PRICE not stored"}
    real = price
    if not cpi.empty:
        frame = pd.concat([price.rename("p"), cpi.rename("cpi")], axis=1, sort=True).dropna()
        real = frame["p"] / frame["cpi"] * frame["cpi"].iloc[-1]
    out = {}
    for start in ("1928-01-01", "1990-01-01"):
        out[f"since {start[:4]}"] = [simulate_contributions(real, rule=r, start=start) for r in ("hold", "dip10", "dip20", "below200d")]
    return _stamp({"results": out, "note": "real S&P Composite (Shiller), monthly contributions of one unit, dividends excluded on both sides, cash earns nothing; walk-forward by construction since every decision uses only past prices"}, "H11")


SCRIPTS = {"H1": h1_real_earnings, "H5": h5_claims_lead, "H6": h6_liquidity, "H7": h7_stress_events,
           "H8": h8_drawdown_gaps, "H11": h11_dip_buying, "rules": rule_scoreboard}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure a hypothesis on the stored data.")
    parser.add_argument("name", choices=sorted(SCRIPTS))
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)
    with Store(args.db) as store:
        result = SCRIPTS[args.name](store)
        if "error" not in result:
            run = store.start_run("research")
            store.save_snapshot(run, "research", result)
            store.finish_run(run, ok=1, failed=0, notes=args.name)
    print(json.dumps(result, indent=2, default=str))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
