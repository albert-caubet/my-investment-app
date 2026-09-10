"""The weekly job: refresh, build, render, archive, deliver.

    python -m invest.jobs.weekly [--db PATH] [--skip-refresh] [--skip-filings] [--fundamentals]
                                 [--no-deliver] [--transactions FILE] [--today YYYY-MM-DD]

Each step runs inside a guard. A step that fails is recorded under "Missing and
failed" in the report and the run continues, so one blocked source never
prevents the report from arriving. The exit status is non-zero when anything
failed, so a scheduler can notice.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from invest.data.cache import Store
from invest.data.releases import load_releases
from invest.fundamentals import screens
from invest.macro import scorecard as sc
from invest.macro.catalog import load_catalog
from invest.macro.derived import apply_transform
from invest.portfolio import lots as lots_mod
from invest.portfolio.rebalance import Targets, load_targets, rebalance
from invest.portfolio.snapshot import PortfolioSnapshot, load_transaction_docs, value_portfolio
from invest.positioning import filings13f, insiders
from invest.positioning.managers import load_managers
from invest.report import deliver as deliver_mod
from invest.report.build import ReportContext, build_markdown
from invest.report.hypotheses import due, load_hypotheses
from invest.report.render import markdown_to_html

SPARKLINE_YEARS = 2
SPARKLINE_POINTS = 60
INSIDER_LOOKBACK_DAYS = 180


@dataclass
class WeeklyResult:
    run_id: str
    markdown: str
    html: str
    paths: dict = field(default_factory=dict)
    statuses: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return 1 if self.failures else 0


def _guard(ctx: ReportContext, name: str, fn, log=None):
    try:
        return fn()
    except Exception as exc:
        ctx.failures[name] = f"{type(exc).__name__}: {exc}"
        if log:
            log(f"  {name}: FAILED {exc}")
        return None


def _sparklines(store: Store, catalog, readings, today: date) -> dict[str, list[float]]:
    out = {}
    start = pd.Timestamp(today) - pd.DateOffset(years=SPARKLINE_YEARS)
    for r in readings:
        if r.value is None:
            continue
        sid = r.note.split()[-1] if r.note.startswith("via fallback") else r.id
        raw = store.read_series(sid)
        if raw.empty:
            continue
        transformed = apply_transform(raw, catalog[r.id].transform)
        recent = transformed[transformed.index >= start]
        if len(recent) > SPARKLINE_POINTS:
            recent = recent.iloc[:: max(1, len(recent) // SPARKLINE_POINTS)]
        out[r.id] = [float(v) for v in recent.to_numpy()]
    return out


def _sale_estimates(snapshot: PortfolioSnapshot, plan) -> list[dict]:
    """FIFO taxable gain for each proposed sell, taken from the largest holding of the bucket."""
    out = []
    if plan is None or snapshot is None:
        return out
    by_asset = {}
    for tx in snapshot.transactions:
        by_asset.setdefault(tx.asset_id, []).append(tx)
    for trade in plan.trades:
        if trade.action != "sell":
            continue
        candidates = sorted((h for h in snapshot.holdings if h.bucket == trade.bucket), key=lambda h: -h.value_eur)
        remaining = trade.amount_eur
        for holding in candidates:
            if remaining <= 0:
                break
            price = snapshot.prices_eur.get(holding.asset_id)
            if not price:
                continue
            state = lots_mod.fifo_lots(by_asset[holding.asset_id])
            units = lots_mod.units_for_amount(state, min(remaining, holding.value_eur), price)
            if units <= 0:
                continue
            estimate = lots_mod.taxable_gain(state, units, price)
            out.append({"bucket": trade.bucket, "holding": holding.name, "quantity": estimate.quantity,
                        "proceeds_eur": estimate.proceeds_eur, "gain_eur": estimate.gain_eur,
                        "note": f"{len(estimate.lots_used)} lot(s), oldest {estimate.lots_used[0][0]}" if estimate.lots_used else ""})
            remaining -= estimate.proceeds_eur
    return out


def run_weekly(
    store: Store,
    *,
    today: date | None = None,
    skip_refresh: bool = False,
    skip_filings: bool = True,
    run_fundamentals: bool = False,
    send: bool = True,
    transaction_docs: list[dict] | None = None,
    portfolio_kwargs: dict | None = None,
    targets: Targets | None = None,
    reports_directory: Path | None = None,
    log=print,
) -> WeeklyResult:
    today = today or date.today()
    now = datetime.now()
    run_id = store.start_run("weekly")
    ctx = ReportContext(run_date=today, run_id=run_id)
    catalog = load_catalog()
    ctx.catalog = catalog

    # 1. data
    if not skip_refresh:
        def do_refresh():
            from invest.data.sources import Fetcher
            from invest.jobs.refresh import refresh
            from invest.secrets import fred_api_key

            report = refresh(store, catalog, Fetcher(fred_api_key=fred_api_key()), releases=load_releases(), today=today, log=None)
            for row in report.critical_failures:
                ctx.failures[f"series {row.id}"] = f"{row.status}: {row.message}"
            return report

        _guard(ctx, "refresh", do_refresh, log)
    if not skip_filings:
        def do_filings():
            from invest.data.edgar import EdgarClient
            from invest.jobs.filings import refresh_holdings

            problems = refresh_holdings(store, EdgarClient(), load_managers(), log=None)
            for p in problems:
                ctx.failures[f"13F {p.split(':')[0]}"] = p
            return problems

        _guard(ctx, "13F holdings", do_filings, log)
    if run_fundamentals:
        def do_fundamentals():
            from invest.data.edgar import EdgarClient
            from invest.fundamentals.universe import load_universe
            from invest.jobs import fundamentals as fj

            universe = load_universe()["sp500_core"]
            problems = fj.refresh_universe(store, EdgarClient(), universe, log=None)
            table = fj.build_metrics_table(store, universe)
            fj.save_screener_snapshot(store, run_id, universe, table, as_of=None)
            fj.store_breadth(store, list(universe.tickers))
            for p in problems[:20]:
                ctx.failures[f"fundamentals {p.split(':')[0]}"] = p
            return table

        _guard(ctx, "fundamentals", do_fundamentals, log)

    # 2. portfolio
    def do_portfolio():
        targets_used = targets or load_targets()
        ctx.targets = targets_used
        docs, source = (transaction_docs, "provided") if transaction_docs is not None else load_transaction_docs()
        snapshot = value_portfolio(docs, targets_used, source=source, today=today, **(portfolio_kwargs or {}))
        ctx.portfolio = snapshot
        if snapshot.totals is None:
            raise RuntimeError("no usable transactions")
        ctx.plan = rebalance(snapshot.holdings, targets_used)
        ctx.sale_estimates = _sale_estimates(snapshot, ctx.plan)
        return snapshot

    _guard(ctx, "portfolio", do_portfolio, log)

    # 3. macro
    def do_macro():
        ctx.readings = sc.build_readings(store, catalog, today=today)
        ctx.regime, ctx.results = sc.build_regime(store, catalog)
        previous = sc.previous_snapshot(store, "scorecard", before=now)
        ctx.previous_at = previous[1] if previous else None
        ctx.changes = sc.changes_since(ctx.readings, ctx.results, previous[2] if previous else None)
        ctx.freshness = sc.freshness_table(store, catalog, today=today)
        ctx.sparklines = _sparklines(store, catalog, ctx.readings, today)
        return ctx.regime

    _guard(ctx, "macro scorecard", do_macro, log)

    # 4. positioning
    def do_positioning():
        summaries = []
        for manager in load_managers():
            pair = filings13f.latest_two_quarters(store.read_holdings(manager.cik))
            if pair is None:
                continue
            prev, curr = pair
            summaries.append(filings13f.summarise(curr["manager"].iloc[0], curr["period_end"].iloc[0], curr["filed_at"].iloc[0],
                                                  filings13f.diff_quarters(prev, curr), filings13f.concentration(curr)))
        ctx.holdings_summaries = summaries
        watch = store.watchlist()
        since = today - timedelta(days=INSIDER_LOOKBACK_DAYS)
        for _, row in watch.iterrows():
            if pd.isna(row.get("cik")):
                continue
            tx = store.read_insider_tx(int(row["cik"]), since=since)
            if not tx.empty:
                ctx.insider_summaries.append(insiders.aggregate(tx, since=since))
        return summaries

    _guard(ctx, "positioning", do_positioning, log)

    # 5. opportunities
    def do_opportunities():
        snap = store.load_snapshot("screener")
        if snap is None:
            raise RuntimeError("no screener table stored; run python -m invest.jobs.fundamentals")
        _, created, payload = snap
        payload = dict(payload)
        payload["built"] = str(created)[:16]
        ctx.screener = payload
        table = pd.DataFrame(payload.get("rows", []))
        previous = store.load_snapshot("screener", offset=1)
        prev_table = pd.DataFrame(previous[2].get("rows", [])) if previous else None
        for name in ("magic_formula", "quality_value"):
            ranked, excluded = screens.run_screen(name, table)
            label = screens.SCREENS[name][0]
            ctx.screen_top[label] = ranked
            if ctx.excluded is None:
                ctx.excluded = excluded
            if prev_table is not None and not prev_table.empty:
                prev_ranked, _ = screens.run_screen(name, prev_table)
                ctx.entrants[label] = screens.entrants_and_dropouts(ranked["ticker"].tolist(), prev_ranked["ticker"].tolist(), top_n=10)
        watch = store.watchlist()
        if not watch.empty and not table.empty:
            ctx.watchlist = watch.merge(table[["ticker", "price", "value_conservative", "mos_conservative"]],
                                        left_on="symbol", right_on="ticker", how="left")
        return table

    _guard(ctx, "opportunities", do_opportunities, log)

    # 6. hypotheses
    _guard(ctx, "hypothesis register", lambda: setattr(ctx, "hypotheses_due", due(load_hypotheses(), today)), log)

    # 7. build, render, deliver
    markdown_text = build_markdown(ctx)
    html_text = markdown_to_html(markdown_text, title=f"Weekly report {today.isoformat()}")
    summary = f"Weekly report {today.isoformat()}: regime {ctx.regime.label if ctx.regime else 'n/a'}; " \
              f"{len(ctx.plan.trades) if ctx.plan else 0} trade(s) proposed; {len(ctx.failures)} problem(s)."
    paths, statuses = deliver_mod.deliver(markdown_text, html_text, now, summary=summary, directory=reports_directory, send=send)
    store.save_snapshot(run_id, "report", {"date": today.isoformat(), "paths": {k: str(v) for k, v in paths.items()},
                                          "failures": ctx.failures, "statuses": statuses, "summary": summary})
    store.finish_run(run_id, ok=1 if not ctx.failures else 0, failed=len(ctx.failures),
                     notes="; ".join(f"{k}: {v}" for k, v in ctx.failures.items())[:4000])
    if log:
        for status in statuses:
            log(f"  {status}")
        for name, why in ctx.failures.items():
            log(f"  MISSING {name}: {why}")
    return WeeklyResult(run_id, markdown_text, html_text, paths, statuses, ctx.failures)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh data, build and deliver the weekly report.")
    parser.add_argument("--db", default=None)
    parser.add_argument("--skip-refresh", action="store_true")
    parser.add_argument("--skip-filings", action="store_true", help="do not refresh 13F holdings (needs SEC_USER_AGENT)")
    parser.add_argument("--fundamentals", action="store_true", help="also re-ingest the screener universe (slow)")
    parser.add_argument("--no-deliver", action="store_true", help="archive only")
    parser.add_argument("--transactions", default=None, help="JSON export of transactions instead of Firestore")
    parser.add_argument("--today", default=None)
    args = parser.parse_args(argv)
    if args.transactions:
        os.environ["INVEST_TRANSACTIONS_JSON"] = args.transactions
    today = date.fromisoformat(args.today) if args.today else None
    with Store(args.db) as store:
        try:
            result = run_weekly(store, today=today, skip_refresh=args.skip_refresh, skip_filings=args.skip_filings,
                                run_fundamentals=args.fundamentals, send=not args.no_deliver)
        except Exception:
            traceback.print_exc()
            return 2
    print(f"\nreport: {result.paths.get('html')}")
    print(f"{len(result.failures)} problem(s)")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
