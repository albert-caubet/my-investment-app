"""Ingest a screener universe from EDGAR and Yahoo, build the metrics table, store breadth.

    python -m invest.jobs.fundamentals [--db PATH] [--universe NAME] [--limit N] [--as-of DATE] [--skip-prices]

Steps: resolve tickers to CIKs, store company facts and SIC-based sectors, store
constituent prices, compute every metric and the two intrinsic values per
company as of a date, save the table as a ``screener`` snapshot, and write two
breadth series into the macro store. A company that fails is reported and the
rest continue.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import date

import numpy as np
import pandas as pd

from invest.data import yahoo
from invest.data.cache import Store, utcnow
from invest.data.edgar import SEC_USER_AGENT_HELP, EdgarClient, EdgarConfigError, facts_frame
from invest.fundamentals import metrics as M
from invest.fundamentals import screens
from invest.fundamentals.facts import canonical_tags, fundamentals_as_of
from invest.fundamentals.universe import Universe, load_universe, resolve_ciks, sector_from_sic
from invest.fundamentals.valuation import value_summary

DEFAULT_UNIVERSE = "sp500_core"
PRICE_YEARS = 12
BREADTH_WINDOW = 200
NEW_HIGH_WINDOW = 252
MIN_BREADTH_NAMES = 20
#: A share count older than this, against the price date, does not make a market cap.
MAX_SHARE_COUNT_AGE_DAYS = 550
#: Market cap must sit within these multiples of the reported public float, else it is refused.
FLOAT_RATIO_BOUNDS = (0.25, 4.0)


def refresh_universe(
    store: Store, client, universe: Universe, *, limit: int | None = None, skip_prices: bool = False, log=print
) -> list[str]:
    """Facts, sectors and prices for every ticker the SEC list resolves."""
    problems: list[str] = []
    tickers_frame = client.company_tickers()
    found, missing = resolve_ciks(universe.tickers, tickers_frame)
    if missing:
        problems.append(f"{len(missing)} ticker(s) not in the SEC list: {', '.join(missing[:12])}")
    items = list(found.items())[:limit] if limit else list(found.items())
    names = dict(zip(tickers_frame["ticker"], tickers_frame["name"]))
    fetched_at = utcnow()
    wanted_tags = canonical_tags()
    for ticker, cik in items:
        try:
            facts = facts_frame(client.companyfacts(cik), tags=wanted_tags)
            n = store.upsert_facts(facts, fetched_at=fetched_at)
            submissions = client.submissions(cik)
            sic = str(submissions.get("sic") or "") or None
            exchanges = submissions.get("exchanges") or []
            store.upsert_companies([{
                "cik": cik, "ticker": ticker, "name": submissions.get("name") or names.get(ticker),
                "sic": sic, "sector": sector_from_sic(sic), "exchange": exchanges[0] if exchanges else None,
            }])
            if log:
                log(f"  {ticker} ({cik}): {n} facts, SIC {sic}")
        except Exception as exc:
            problems.append(f"{ticker} ({cik}): {exc}")
            if log:
                log(f"  {ticker}: FAILED {exc}")
    if not skip_prices and items:
        symbols = [t for t, _ in items]
        try:
            frames = yahoo.fetch_history(symbols, period=f"{PRICE_YEARS}y")
        except Exception as exc:
            frames = {}
            problems.append(f"price download failed: {exc}")
        for symbol in symbols:
            frame = frames.get(symbol)
            if frame is None or frame.empty:
                problems.append(f"{symbol}: no price history")
                continue
            store.upsert_prices(symbol, frame, currency="USD", fetched_at=fetched_at)
        if log:
            log(f"  prices: {len(frames)} of {len(symbols)} symbols")
    return problems


def _price_as_of(prices: pd.Series, as_of: date | None) -> tuple[float | None, date | None]:
    if prices.empty:
        return None, None
    if as_of is not None:
        prices = prices[prices.index <= pd.Timestamp(as_of)]
        if prices.empty:
            return None, None
    return float(prices.iloc[-1]), prices.index[-1].date()


def _fx_to_usd_per(currency: str, store: Store, as_of: date | None) -> float | None:
    """USD per one unit of ``currency`` as of a date; only EUR is stored today."""
    if currency == "USD":
        return 1.0
    if currency == "EUR":
        eurusd = store.read_series("EURUSD")
        value, _ = _price_as_of(eurusd, as_of)
        return value
    return None


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if is_dataclass(value):
        return _clean(asdict(value))
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def company_row(store: Store, cik: int, ticker: str, name: str | None, sic: str | None, *, as_of: date | None) -> dict:
    """Every metric and both intrinsic values for one company, as of a date."""
    facts = store.read_facts(cik, as_of=as_of)
    fund = fundamentals_as_of(facts, as_of, sic=sic)
    price, price_date = _price_as_of(store.read_prices(ticker), as_of)
    shares = fund.latest.get("shares_outstanding")
    shares_date = fund.instant_dates.get("shares_outstanding")
    market_cap = None
    notes = list(fund.notes)
    # Yahoo quotes the US listing in dollars; the accounts may be in another currency.
    fx = _fx_to_usd_per(fund.currency or "USD", store, as_of)
    price_in_accounts = price / fx if (price is not None and fx) else None
    if shares and shares_date and price_date and (price_date - shares_date).days > MAX_SHARE_COUNT_AGE_DAYS:
        notes.append(f"share count as of {shares_date} is too old for a {price_date} price; no market cap")
        shares = None
    if price is not None and shares:
        if fx is None:
            notes.append(f"market cap not converted: no {fund.currency} rate stored")
        else:
            market_cap = price_in_accounts * shares  # in the currency of the accounts
            public_float = fund.latest.get("public_float")
            if public_float and public_float > 0:
                ratio = market_cap / public_float
                if not (FLOAT_RATIO_BOUNDS[0] <= ratio <= FLOAT_RATIO_BOUNDS[1]):
                    notes.append(
                        f"market cap {market_cap:,.0f} is {ratio:.2f}x the reported public float {public_float:,.0f} "
                        f"(as of {fund.instant_dates.get('public_float')}); share count and price likely refer to "
                        f"different classes; no market cap"
                    )
                    market_cap = None
    elif price is None:
        notes.append("no price stored")
    elif not shares:
        notes.append("no usable share count")
    metric_set = M.compute_all(fund.latest, fund.fy_previous, fund.history, market_cap=market_cap,
                               annual_latest=fund.fy_latest, annual_previous=fund.fy_previous)
    v = metric_set.values
    valuation = value_summary(
        price=price_in_accounts,
        ebit_by_year=fund.history.get("operating_income", {}),
        income_tax=fund.latest.get("income_tax"), pretax_income=fund.latest.get("pretax_income"),
        net_debt=v.get("net_debt"), shares=shares, fcf=v.get("fcf"), revenue_cagr=v.get("revenue_cagr_3y"),
    )
    row = {
        "cik": cik, "ticker": ticker, "name": name, "sic": sic, "sector": sector_from_sic(sic),
        "is_financial": fund.is_financial, "currency": fund.currency, "price": price,
        "price_date": price_date, "period_end": fund.period_end, "fy_end": fund.fy_end,
        "basis": fund.flow_basis["revenue"].basis if "revenue" in fund.flow_basis else None,
        "shares_outstanding": shares,
    }
    row.update({k: val for k, val in v.items() if k != "f_checks"})
    row["f_checks"] = v.get("f_checks")
    row["epv_per_share"] = valuation["epv"]["value_per_share"]
    row["dcf_per_share"] = valuation["dcf"]["value_per_share"]
    row["value_conservative"] = valuation["value_conservative"]
    row["mos_conservative"] = valuation["mos_conservative"]
    row["valuation_inputs"] = {"epv": valuation["epv"]["inputs"], "dcf": valuation["dcf"]["inputs"],
                               "epv_notes": valuation["epv"]["notes"], "dcf_notes": valuation["dcf"]["notes"]}
    row["tags"] = fund.tags
    row["notes"] = notes + metric_set.notes
    return _clean(row)


def build_metrics_table(store: Store, universe: Universe, *, as_of: date | None = None, log=None) -> pd.DataFrame:
    companies = store.companies()
    wanted = set(universe.tickers)
    rows = []
    for _, company in companies.iterrows():
        if company["ticker"] not in wanted:
            continue
        try:
            rows.append(company_row(store, int(company["cik"]), company["ticker"], company["name"], company["sic"], as_of=as_of))
        except Exception as exc:
            rows.append(_clean({"cik": int(company["cik"]), "ticker": company["ticker"], "name": company["name"],
                                "notes": [f"metrics failed: {exc}"]}))
            if log:
                log(f"  {company['ticker']}: metrics FAILED {exc}")
    return pd.DataFrame(rows)


def compute_breadth(store: Store, symbols: list[str]) -> dict[str, pd.Series]:
    """Share of names above their 200-day average, and new 52-week highs minus lows, daily, in percent."""
    panel = store.read_price_panel(list(symbols), adjusted=True)
    if panel.empty or panel.shape[1] < MIN_BREADTH_NAMES:
        return {}
    ma = panel.rolling(BREADTH_WINDOW, min_periods=BREADTH_WINDOW).mean()
    valid = ma.notna() & panel.notna()
    above = (panel > ma).astype(float).where(valid, 0.0)
    count = valid.sum(axis=1).astype(float)
    enough = count >= MIN_BREADTH_NAMES
    pct_above = (above.sum(axis=1)[enough] / count[enough] * 100.0)
    high = panel.rolling(NEW_HIGH_WINDOW, min_periods=NEW_HIGH_WINDOW).max()
    low = panel.rolling(NEW_HIGH_WINDOW, min_periods=NEW_HIGH_WINDOW).min()
    valid_hl = high.notna() & panel.notna()
    new_high = (panel >= high).astype(float).where(valid_hl, 0.0)
    new_low = (panel <= low).astype(float).where(valid_hl, 0.0)
    count_hl = valid_hl.sum(axis=1).astype(float)
    enough_hl = count_hl >= MIN_BREADTH_NAMES
    nh_nl = ((new_high.sum(axis=1) - new_low.sum(axis=1))[enough_hl] / count_hl[enough_hl] * 100.0)
    return {"BREADTH_ABOVE_200D_PCT": pct_above.dropna(), "BREADTH_NH_NL_PCT": nh_nl.dropna()}


def store_breadth(store: Store, symbols: list[str], *, log=None) -> int:
    series = compute_breadth(store, symbols)
    added = 0
    for sid, values in series.items():
        n = store.upsert_series(sid, values, source="computed")
        added += n
        if log:
            log(f"  {sid}: {len(values)} days, {n} new")
    return added


def save_screener_snapshot(store: Store, run_id: str, universe: Universe, table: pd.DataFrame, *, as_of: date | None) -> None:
    payload = {
        "universe": universe.name,
        "universe_label": universe.label,
        "as_of": as_of.isoformat() if as_of else None,
        "point_in_time": universe.point_in_time,
        "n_companies": int(len(table)),
        "rows": json.loads(table.to_json(orient="records", date_format="iso")) if not table.empty else [],
    }
    store.save_snapshot(run_id, "screener", payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest fundamentals for a universe and build the screener table.")
    parser.add_argument("--db", default=None)
    parser.add_argument("--universe", default=DEFAULT_UNIVERSE)
    parser.add_argument("--limit", type=int, default=None, help="only the first N resolved tickers")
    parser.add_argument("--as-of", default=None, help="build the table as of this date (YYYY-MM-DD)")
    parser.add_argument("--skip-prices", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true", help="rebuild the table from stored facts only")
    args = parser.parse_args(argv)

    universes = load_universe()
    if args.universe not in universes:
        print(f"unknown universe {args.universe!r}; known: {sorted(universes)}")
        return 2
    universe = universes[args.universe]
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    problems: list[str] = []
    client = None
    if not args.skip_fetch:
        try:
            client = EdgarClient()  # before the database is touched: no half-started run
        except EdgarConfigError as exc:
            print(f"{exc}.\n\n{SEC_USER_AGENT_HELP}")
            return 2
    with Store(args.db) as store:
        run_id = store.start_run("fundamentals")
        if client is not None:
            problems += refresh_universe(store, client, universe, limit=args.limit, skip_prices=args.skip_prices)
            print(f"  {client.n_requests} EDGAR requests")
        table = build_metrics_table(store, universe, as_of=as_of, log=print)
        save_screener_snapshot(store, run_id, universe, table, as_of=as_of)
        store_breadth(store, list(universe.tickers), log=print)
        store.finish_run(run_id, ok=len(table), failed=len(problems), notes="; ".join(problems)[:4000])
        if not table.empty:
            ranked, excluded = screens.run_screen("magic_formula", table)
            cols = [c for c in ("rank", "ticker", "name", "earnings_yield", "roic", "fcf_yield", "f_score", "mos_conservative") if c in ranked.columns]
            print()
            print("Magic formula, top 15 (value traps excluded: %d)" % len(excluded))
            print(ranked[cols].head(15).to_string(index=False))
    print(f"\n{len(table)} companies in the table; {len(problems)} problem(s)")
    for problem in problems:
        print("  " + problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
