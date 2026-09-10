"""Refresh 13F holdings for the tracked managers and Form 4 trades for issuers.

    python -m invest.jobs.filings [--db PATH] [--insiders CIK,CIK] [--since YYYY-MM-DD]

Needs ``SEC_USER_AGENT`` (an app name and contact address). Every problem is
returned and printed; nothing is hidden, and a failing manager does not stop
the others.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from invest.data.cache import Store
from invest.data.edgar import EdgarClient
from invest.positioning import filings13f, insiders
from invest.positioning.managers import Manager, load_managers

INSIDER_LOOKBACK_DAYS = 365


def refresh_holdings(store: Store, client, managers: list[Manager], *, n_quarters: int = 2, log=print) -> list[str]:
    problems: list[str] = []
    for manager in managers:
        try:
            frames = filings13f.fetch_manager_holdings(client, manager.cik, name_hint=manager.name, n_quarters=n_quarters)
        except Exception as exc:
            problems.append(f"{manager.name} ({manager.cik}): {exc}")
            if log:
                log(f"  {manager.name}: FAILED {exc}")
            continue
        if not frames:
            problems.append(f"{manager.name} ({manager.cik}): no 13F-HR information table found")
            continue
        edgar_name = frames[0]["manager"].iloc[0]
        if edgar_name and manager.name.split()[0].lower() not in edgar_name.lower():
            problems.append(f"{manager.name} ({manager.cik}): EDGAR names this filer {edgar_name!r}; check the CIK")
        n = sum(store.upsert_holdings(frame) for frame in frames)
        if log:
            periods = ", ".join(str(f["period_end"].iloc[0]) for f in frames)
            log(f"  {manager.name}: {n} positions over {len(frames)} quarter(s) ({periods})")
            for frame in frames:
                if frame.attrs.get("value_factor") == 1000.0 and frame["period_end"].iloc[0] >= filings13f.DOLLARS_FROM:
                    log(f"    {frame['period_end'].iloc[0]}: values were reported in thousands; converted to dollars")
    return problems


def refresh_insiders(store: Store, client, issuer_ciks: list[int], *, since: date, log=print) -> list[str]:
    problems: list[str] = []
    for cik in issuer_ciks:
        try:
            frame = insiders.fetch_form4s(client, int(cik), since=since)
        except Exception as exc:
            problems.append(f"insiders {cik}: {exc}")
            if log:
                log(f"  insiders {cik}: FAILED {exc}")
            continue
        n = store.upsert_insider_tx(frame)
        if log:
            log(f"  insiders {cik}: {n} transaction rows since {since}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch 13F holdings and Form 4 trades into DuckDB.")
    parser.add_argument("--db", default=None)
    parser.add_argument("--insiders", default="", help="comma-separated issuer CIKs for Form 4 aggregation")
    parser.add_argument("--since", default=None, help="Form 4 window start (default: one year ago)")
    parser.add_argument("--quarters", type=int, default=2)
    args = parser.parse_args(argv)

    since = date.fromisoformat(args.since) if args.since else date.today() - timedelta(days=INSIDER_LOOKBACK_DAYS)
    client = EdgarClient()
    managers = load_managers()
    problems: list[str] = []
    with Store(args.db) as store:
        run_id = store.start_run("filings")
        problems += refresh_holdings(store, client, managers, n_quarters=args.quarters)
        ciks = [int(c) for c in args.insiders.split(",") if c.strip()]
        if ciks:
            problems += refresh_insiders(store, client, ciks, since=since)
        store.finish_run(run_id, ok=len(managers) + len(ciks) - len(problems), failed=len(problems),
                         notes="; ".join(problems))
        for manager in managers:
            pair = filings13f.latest_two_quarters(store.read_holdings(manager.cik))
            if pair is None:
                continue
            prev, curr = pair
            diff = filings13f.diff_quarters(prev, curr)
            print()
            print(filings13f.summarise(curr["manager"].iloc[0], curr["period_end"].iloc[0],
                                       curr["filed_at"].iloc[0], diff, filings13f.concentration(curr)))
    print(f"\n{client.n_requests} EDGAR requests; {len(problems)} problem(s)")
    for problem in problems:
        print("  " + problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
