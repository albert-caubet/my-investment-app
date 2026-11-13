"""Ingest a screener universe from EDGAR or Yahoo, build the metrics table, store breadth.

    python -m invest.jobs.fundamentals [--db PATH] [--universe NAME] [--limit N] [--as-of DATE] [--skip-prices]

Steps for a US universe (source ``edgar``): resolve tickers to CIKs, store company
facts and SIC-based sectors, store constituent prices. For a European one (source
``yahoo``): store each company's Yahoo accounts, name and sector, its prices in the
currency it is quoted in, and the exchange rates between those currencies. Then for
either: compute every metric and the two intrinsic values per company as of a date,
save the table as the region's screener snapshot, and, for the US, write two breadth
series into the macro store. A company that fails is reported and the rest continue.

``look_up_company`` takes the same steps for one company in no universe, into a
throwaway in-memory store, for the Company financials page.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from invest.data import yahoo
from invest.data.cache import Store, utcnow
from invest.data.edgar import SEC_USER_AGENT_HELP, EdgarClient, EdgarConfigError, facts_frame
from invest.fundamentals import metrics as M
from invest.fundamentals import screens
from invest.fundamentals.facts import canonical_tags, fundamentals_as_of, is_money, tag_kinds
from invest.fundamentals.universe import (
    Universe,
    is_financial_sector,
    load_universe,
    resolve_ciks,
    resolve_yahoo_ids,
    screener_kind,
    sector_from_sic,
)
from invest.fundamentals.valuation import value_summary

#: The universe the Screener page builds for each region, and the command line by default.
DEFAULT_UNIVERSES = {"US": "sp500_core", "EU": "stoxx600"}
DEFAULT_UNIVERSE = DEFAULT_UNIVERSES["US"]
#: The currency a region's table also states market caps in, where the accounts are in many.
COMPARE_CURRENCY = {"EU": "EUR"}
PRICE_YEARS = 12
PRICE_BATCH = 100  # symbols per price download
BREADTH_WINDOW = 200
NEW_HIGH_WINDOW = 252
MIN_BREADTH_NAMES = 20
#: A share count older than this, against the price date, does not make a market cap.
MAX_SHARE_COUNT_AGE_DAYS = 550
#: Market cap must sit within these multiples of the reported public float, else it is refused.
FLOAT_RATIO_BOUNDS = (0.25, 4.0)


def refresh_universe(
    store: Store, client, universe: Universe, *, limit: int | None = None, skip_prices: bool = False, log=print,
    tickers: pd.DataFrame | None = None,
) -> list[str]:
    """Facts, sectors and prices for every ticker the SEC list resolves. ``tickers`` is that list
    (``client.company_tickers()``), asked of the client when not given."""
    problems: list[str] = []
    tickers_frame = client.company_tickers() if tickers is None else tickers
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
        problems += store_prices(store, {t: "USD" for t, _ in items}, fetched_at, log=log)
    return problems


def refresh_yahoo_universe(
    store: Store, client, universe: Universe, *, limit: int | None = None, skip_prices: bool = False, log=print
) -> list[str]:
    """Accounts, names, sectors and prices from Yahoo Finance for every symbol of the universe,
    and the exchange rates the table needs. A symbol Yahoo does not know is reported, never dropped
    silently. Each value keeps the day it was first fetched (``Store.insert_new_facts``)."""
    problems: list[str] = []
    ids, clashes = resolve_yahoo_ids(universe.tickers)
    if clashes:
        problems.append(f"{len(clashes)} symbol(s) share an id with an earlier one: {', '.join(clashes[:12])}")
    items = list(ids.items())[:limit] if limit else list(ids.items())
    kinds = tag_kinds("yahoo")
    fetched_at = utcnow()
    quoted: dict[str, str] = {}  # symbol -> the currency Yahoo quotes it in, GBp for most of London
    currencies = set(COMPARE_CURRENCY.values())
    for symbol, entity in items:
        try:
            profile = client.profile(symbol)
            if not profile.get("currency"):
                raise ValueError("Yahoo has no quote for this symbol")
            facts = yahoo.fundamentals_facts(client.fundamentals(symbol, kinds), entity, kinds,
                                             seen_on=fetched_at.date(), currency=profile.get("financial_currency"))
            if facts.empty:
                raise ValueError("Yahoo has no accounts for this symbol")
            n = store.insert_new_facts(facts, fetched_at=fetched_at)
            sector = (profile.get("sector") or "").strip().lower() or "unknown"
            store.upsert_companies([{"cik": entity, "ticker": symbol, "name": profile.get("name"), "sic": None,
                                     "sector": sector, "exchange": profile.get("exchange")}])
            quoted[symbol] = profile["currency"]
            currencies.add(yahoo.main_unit(profile["currency"])[0])
            currencies.update(unit for unit in facts["unit"].unique() if is_money(unit))
            if log:
                log(f"  {symbol}: {len(facts)} facts, {n} new, {sector}")
        except Exception as exc:
            problems.append(f"{symbol}: {exc}")
            if log:
                log(f"  {symbol}: FAILED {exc}")
    if not skip_prices and quoted:
        problems += store_prices(store, quoted, fetched_at, log=log)
        rates = {yahoo.fx_symbol(code): code for code in sorted(currencies - {"USD"})}
        problems += store_prices(store, rates, fetched_at, log=log, what="exchange rates")
    return problems


def store_prices(store: Store, currencies: dict[str, str], fetched_at, *, log=print, what: str = "prices") -> list[str]:
    """Daily closes over the last ``PRICE_YEARS`` for each symbol of ``{symbol: currency}``, stored
    with that currency, downloaded in batches. Returns the problems."""
    problems: list[str] = []
    symbols = list(currencies)
    stored = 0
    for start in range(0, len(symbols), PRICE_BATCH):
        batch = symbols[start:start + PRICE_BATCH]
        try:
            frames = yahoo.fetch_history(batch, period=f"{PRICE_YEARS}y")
        except Exception as exc:
            problems.append(f"{what} download failed for {len(batch)} symbol(s): {exc}")
            continue
        for symbol in batch:
            frame = frames.get(symbol)
            if frame is None or frame.empty:
                problems.append(f"{symbol}: no price history")
                continue
            store.upsert_prices(symbol, frame, currency=currencies[symbol], fetched_at=fetched_at)
            stored += 1
    if log:
        log(f"  {what}: {stored} of {len(symbols)} symbols")
    return problems


def _price_as_of(prices: pd.Series, as_of: date | None) -> tuple[float | None, date | None]:
    if prices.empty:
        return None, None
    if as_of is not None:
        prices = prices[prices.index <= pd.Timestamp(as_of)]
        if prices.empty:
            return None, None
    return float(prices.iloc[-1]), prices.index[-1].date()


def usd_per_unit(currency: str | None, store: Store, as_of: date | None) -> float | None:
    """US dollars for one unit of ``currency`` at the last close on or before ``as_of``; None without a rate.

    Rates are Yahoo's ``<CCY>=X`` closes (units per dollar), which the European build stores;
    for EUR also the EURUSD series the macro refresh stores, whichever has the later close.
    A quote in pence (``GBp``) is a hundredth of a pound.
    """
    if not currency:
        return None
    code, minor = yahoo.main_unit(currency)
    if code == "USD":
        return 1.0 / minor
    rates = []  # (day, dollars per unit)
    if code == "EUR":
        rate, day = _price_as_of(store.read_series("EURUSD"), as_of)
        if rate:
            rates.append((day, rate))
    per_dollar, day = _price_as_of(store.read_prices(yahoo.fx_symbol(code)), as_of)
    if per_dollar:
        rates.append((day, 1.0 / per_dollar))
    if not rates:
        return None
    return max(rates, key=lambda r: r[0])[1] / minor


def convert(amount: float | None, source: str | None, target: str | None, store: Store,
            as_of: date | None) -> float | None:
    """``amount`` in ``source`` currency, in ``target`` currency, through the dollar; None without a rate."""
    if amount is None or not source or not target:
        return None
    if source == target:
        return float(amount)
    from_usd, to_usd = usd_per_unit(source, store, as_of), usd_per_unit(target, store, as_of)
    return float(amount) * from_usd / to_usd if (from_usd and to_usd) else None


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


def company_row(store: Store, cik: int, ticker: str, name: str | None, sic: str | None, *, as_of: date | None,
                sector: str | None = None, financial: bool = False) -> dict:
    """Every metric and both intrinsic values for one company, as of a date.

    ``sector`` and ``financial`` are the source's, for a company without a SIC code (Yahoo's).
    """
    facts = store.read_facts(cik, as_of=as_of)
    fund = fundamentals_as_of(facts, as_of, sic=sic, financial=financial)
    price, price_date = _price_as_of(store.read_prices(ticker), as_of)
    shares = fund.latest.get("shares_outstanding")
    shares_date = fund.instant_dates.get("shares_outstanding")
    market_cap = None
    notes = list(fund.notes)
    # The price is in the currency of the quote (dollars for a US listing, pence for most of
    # London's); the accounts may be in another.
    quoted_in = store.price_currency(ticker) or "USD"
    accounts_in = fund.currency or "USD"
    price_in_accounts = convert(price, quoted_in, accounts_in, store, as_of) if price is not None else None
    if shares and shares_date and price_date and (price_date - shares_date).days > MAX_SHARE_COUNT_AGE_DAYS:
        notes.append(f"share count as of {shares_date} is too old for a {price_date} price; no market cap")
        shares = None
    if price is not None and shares:
        if price_in_accounts is None:
            missing = quoted_in if usd_per_unit(quoted_in, store, as_of) is None else accounts_in
            notes.append(f"market cap not converted: no {yahoo.main_unit(missing)[0]} rate stored")
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
        "cik": cik, "ticker": ticker, "name": name, "sic": sic, "sector": sector or sector_from_sic(sic),
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
    """A row per stored company of the universe. In a region whose accounts are in many
    currencies, ``market_cap_<currency>`` states every market cap in one, to compare them."""
    companies = store.companies()
    wanted = set(universe.tickers)
    from_yahoo = universe.source == "yahoo"
    compare = COMPARE_CURRENCY.get(universe.region)
    rows = []
    for _, company in companies.iterrows():
        # a company from Yahoo is stored under a negative id, a filer under its CIK
        if company["ticker"] not in wanted or (int(company["cik"]) < 0) != from_yahoo:
            continue
        sector = company["sector"] if from_yahoo else None
        try:
            row = company_row(store, int(company["cik"]), company["ticker"], company["name"], company["sic"],
                              as_of=as_of, sector=sector, financial=from_yahoo and is_financial_sector(sector))
            if compare:
                row[f"market_cap_{compare.lower()}"] = convert(row.get("market_cap"), row.get("currency"), compare,
                                                              store, as_of)
            rows.append(row)
        except Exception as exc:
            rows.append(_clean({"cik": int(company["cik"]), "ticker": company["ticker"], "name": company["name"],
                                "notes": [f"metrics failed: {exc}"]}))
            if log:
                log(f"  {company['ticker']}: metrics FAILED {exc}")
    return pd.DataFrame(rows)


# ==========================================================================================
# One company in no universe, for the Company financials page
# ==========================================================================================


@dataclass
class CompanyData:
    """One company as the company detail shows it: its row of a screener table, the fiscal-year
    history of its accounts and its share price, and where they came from."""

    row: pd.Series  # every measure and both intrinsic values, as a screener table has them
    history: pd.DataFrame  # a row per fiscal year, a column per field
    currency: str | None  # of the accounts
    prices: pd.Series  # daily closes, adjusted for splits, not for dividends
    price_currency: str | None  # of the quote, which can differ from the accounts': GBp for most of London
    source: str  # where the accounts come from: "edgar" or "yahoo"
    stored: bool  # read from the market database; otherwise fetched just now and kept nowhere
    problems: list[str] = field(default_factory=list)  # what could not be had, said under the detail


def _refresh_filer(store: Store, client, universe: Universe, tickers: pd.DataFrame) -> list[str]:
    """``refresh_universe``, its prices downloaded with the dollar rates of the currencies the
    accounts are in: the market database has the euro's from the macro refresh, a throwaway store none."""
    problems = refresh_universe(store, client, universe, skip_prices=True, log=None, tickers=tickers)
    ciks = store.facts_ciks()
    if not ciks:
        return problems
    units = {unit for cik in ciks for unit in store.read_facts(cik)["unit"].unique() if is_money(unit)}
    quotes = {ticker: "USD" for ticker in universe.tickers} | {yahoo.fx_symbol(c): c for c in sorted(units - {"USD"})}
    return problems + store_prices(store, quotes, utcnow(), log=None)


def look_up_company(symbol: str, *, edgar_client=None, yahoo_client=None,
                    sec_tickers: Callable[[], pd.DataFrame] | None = None) -> CompanyData:
    """Any company, fetched now by the build's own steps for a universe of one, into a throwaway
    in-memory store: nothing is written to the market database, and the company gets no rank.

    A US filer, a symbol without an exchange suffix on SEC's list, comes from EDGAR through
    ``edgar_client``, as the US table does: point-in-time, ten years and more. Any other company,
    or a filer EDGAR has no accounts for, comes from Yahoo Finance through ``yahoo_client``, as the
    European table does: four or five years, not point-in-time. ``sec_tickers`` returns SEC's list,
    by default the client's ``company_tickers``, so a caller can keep it. A source without a client
    is skipped. Raises ``LookupError`` saying why when no source has the company's accounts.
    """
    symbol = symbol.strip().upper()
    problems: list[str] = []
    sources: list[str] = []
    known = None  # SEC's list
    if edgar_client is not None and "." not in symbol:
        try:
            known = (sec_tickers or edgar_client.company_tickers)()
        except Exception as exc:
            problems.append(f"SEC's list of tickers could not be had: {exc}")
        else:
            if resolve_ciks([symbol], known)[0]:
                sources.append("edgar")
    if yahoo_client is not None:
        sources.append("yahoo")
    for source in sources:
        # A universe's region sets only the currency a table of many compares market caps in.
        universe = Universe("lookup", symbol, (symbol,), point_in_time=source == "edgar", source=source)
        with Store(":memory:") as store:
            if source == "edgar":
                found = _refresh_filer(store, edgar_client, universe, known)
            else:
                found = refresh_yahoo_universe(store, yahoo_client, universe, log=None)
            table = build_metrics_table(store, universe)
            if table.empty:
                problems += found
                continue
            row = table.iloc[0]
            fund = fundamentals_as_of(store.read_facts(int(row["cik"])))
            return CompanyData(row, fund.history_frame(), fund.currency, store.read_prices(symbol),
                               store.price_currency(symbol), source, stored=False, problems=problems + found)
    raise LookupError("; ".join(problems) or f"no source to fetch {symbol} from")


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
    """The table, under its region's snapshot kind (``screener`` for the US, ``screener_eu``)."""
    payload = {
        "universe": universe.name,
        "universe_label": universe.label,
        "region": universe.region,
        "source": universe.source,
        "as_of": as_of.isoformat() if as_of else None,
        "point_in_time": universe.point_in_time,
        "n_companies": int(len(table)),
        "rows": json.loads(table.to_json(orient="records", date_format="iso")) if not table.empty else [],
    }
    store.save_snapshot(run_id, screener_kind(universe.region), payload)


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
    from_edgar = universe.source == "edgar"
    if not args.skip_fetch:
        if from_edgar:
            try:
                client = EdgarClient()  # before the database is touched: no half-started run
            except EdgarConfigError as exc:
                print(f"{exc}.\n\n{SEC_USER_AGENT_HELP}")
                return 2
        else:
            client = yahoo.YahooClient()
    with Store(args.db) as store:
        run_id = store.start_run("fundamentals")
        if client is not None:
            refresh = refresh_universe if from_edgar else refresh_yahoo_universe
            problems += refresh(store, client, universe, limit=args.limit, skip_prices=args.skip_prices)
            print(f"  {client.n_requests} {'EDGAR' if from_edgar else 'Yahoo Finance'} requests")
        table = build_metrics_table(store, universe, as_of=as_of, log=print)
        save_screener_snapshot(store, run_id, universe, table, as_of=as_of)
        if universe.region == "US":  # the macro scorecard's breadth series are of the US market
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
