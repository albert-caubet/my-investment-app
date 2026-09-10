"""Refresh every series in the catalog and print the freshness table.

    python -m invest.jobs.refresh [--db PATH] [--only ID,ID] [--skip-derived]

Exit status is non-zero when a *critical* series failed to fetch or is older
than its frequency and publication lag allow (and no fallback covers it). A
failed non-critical series is printed and stored in the fetch log, never hidden.

The job is the only writer of the DuckDB file. Everything it stores carries the
run's fetch time, so a second identical run adds no rows.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from invest.data.cache import Store, utcnow
from invest.data.releases import Release, load_releases
from invest.data.sources import Fetcher, SourceError
from invest.macro import derived
from invest.macro.catalog import Catalog, SeriesSpec, load_catalog
from invest.secrets import fred_api_key


@dataclass
class FreshnessRow:
    id: str
    source: str
    status: str  # ok | stale | failed | missing | manual | fallback | skipped
    last_obs: date | None
    age_days: int | None
    stale_after: int | None
    critical: bool
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "status": self.status,
            "last_obs": self.last_obs.isoformat() if self.last_obs else None,
            "age_days": self.age_days,
            "stale_after": self.stale_after,
            "critical": self.critical,
            "message": self.message,
        }


@dataclass
class RefreshReport:
    run_id: str
    rows: list[FreshnessRow] = field(default_factory=list)
    n_ok: int = 0
    n_failed: int = 0

    @property
    def failures(self) -> list[FreshnessRow]:
        return [r for r in self.rows if r.status in ("failed", "stale", "missing")]

    @property
    def critical_failures(self) -> list[FreshnessRow]:
        return [r for r in self.failures if r.critical]

    @property
    def exit_code(self) -> int:
        return 1 if self.critical_failures else 0

    def table(self) -> str:
        widths = (34, 13, 9, 11, 5, 6)
        header = ("series", "source", "status", "last obs", "age", "limit")
        lines = ["  ".join(h.ljust(w) for h, w in zip(header, widths))]
        lines.append("  ".join("-" * w for w in widths))
        for r in self.rows:
            cells = (
                r.id[: widths[0]],
                r.source,
                r.status,
                r.last_obs.isoformat() if r.last_obs else "-",
                str(r.age_days) if r.age_days is not None else "-",
                str(r.stale_after) if r.stale_after is not None else "-",
            )
            line = "  ".join(c.ljust(w) for c, w in zip(cells, widths))
            if r.message:
                line += "  " + r.message[:90]
            lines.append(line)
        return "\n".join(lines)


def _status_for(
    spec: SeriesSpec,
    store: Store,
    catalog: Catalog,
    today: date,
    fetch_status: str | None,
    message: str,
    input_rows: dict[str, "FreshnessRow"],
) -> FreshnessRow:
    meta = store.series_meta(spec.id)
    last = meta.last_obs
    age = (today - last).days if last else None
    limit = catalog.stale_after(spec)
    bad_inputs = [
        i for i in spec.inputs
        if i in input_rows and input_rows[i].status in ("failed", "stale", "missing")
    ]
    if fetch_status == "failed":
        status = "failed"
    elif spec.is_release:
        status = "manual"
    elif last is None:
        status = "missing"
    elif limit is not None and age is not None and age > limit:
        status = "stale"
        message = message or f"last observation {age} days old, limit {limit}"
    elif bad_inputs:
        # A derived value dated today can still rest on an input that stopped
        # updating; carrying the slow input forward is what the formula does, so
        # the freshness of the result is the freshness of its weakest input.
        status = "stale"
        message = message or "stale input: " + ", ".join(bad_inputs)
    else:
        status = "ok"
    return FreshnessRow(spec.id, spec.source, status, last, age, limit, spec.critical, message)


def _apply_fallbacks(rows: list[FreshnessRow], catalog: Catalog) -> None:
    by_id = {r.id: r for r in rows}
    for row in rows:
        spec = catalog.by_id.get(row.id)
        if row.status in ("failed", "stale", "missing") and spec and spec.fallback:
            alt = by_id.get(spec.fallback)
            if alt and alt.status == "ok":
                row.status = "fallback"
                row.message = (row.message + f" -> using {spec.fallback}").strip()


def ingest_releases(store: Store, releases: list[Release], catalog: Catalog, *, fetched_at: datetime) -> int:
    """Hand-entered releases into the series table, vintage = release date. Returns rows added."""
    added = 0
    for release in releases:
        if release.series_id not in catalog.by_id:
            raise ValueError(f"release for unknown series {release.series_id!r}")
        added += store.upsert_series(
            release.series_id,
            {release.period: release.value},
            source="release",
            fetched_at=fetched_at,
            vintage_date=release.released,
        )
    return added


def compute_derived(
    store: Store, catalog: Catalog, *, fetched_at: datetime, log=None
) -> dict[str, tuple[str, str]]:
    """Every derived series in dependency order.

    Returns ``{id: (status, message)}`` for the ones not computed cleanly:
    ``failed`` when an input is missing or the formula broke, ``manual`` when the
    only missing inputs are hand-entered releases that nobody has entered yet,
    and ``ok`` with a note when an optional-input formula ran on a subset.
    """
    problems: dict[str, tuple[str, str]] = {}
    for spec in catalog.derived_in_order():
        inputs = [store.read_series(i) for i in spec.inputs]
        missing = [i for i, s in zip(spec.inputs, inputs) if s.empty]
        note = ""
        if missing and spec.formula in derived.OPTIONAL_INPUT_FORMULAS:
            present = [s for s in inputs if not s.empty]
            if len(present) >= derived.MIN_OPTIONAL_INPUTS:
                inputs = present
                note = f"without {', '.join(missing)}"
                missing = []
        if missing:
            message = f"missing inputs: {', '.join(missing)}"
            only_releases = all(catalog.by_id[i].is_release for i in missing)
            problems[spec.id] = ("manual" if only_releases else "failed", message)
            store.log_fetch(spec.id, source="derived", key=spec.formula, status="failed", message=message)
            continue
        try:
            result = derived.compute(spec.formula, inputs)
        except Exception as exc:
            message = f"{spec.formula}: {exc}"
            problems[spec.id] = ("failed", message)
            store.log_fetch(spec.id, source="derived", key=spec.formula, status="failed", message=message)
            continue
        if result.empty:
            message = f"{spec.formula} produced no values"
            problems[spec.id] = ("failed", message)
            store.log_fetch(spec.id, source="derived", key=spec.formula, status="empty", message=message)
            continue
        n = store.upsert_series(spec.id, result, source="derived", fetched_at=fetched_at)
        store.log_fetch(spec.id, source="derived", key=spec.formula, status="ok", n_rows=n, message=note)
        if note:
            problems[spec.id] = ("ok", note)
        if log:
            log(f"  derived {spec.id}: {len(result)} values, {n} new {note}".rstrip())
    return problems


def refresh(
    store: Store,
    catalog: Catalog,
    fetcher: Fetcher,
    *,
    only: set[str] | None = None,
    releases: list[Release] | None = None,
    skip_derived: bool = False,
    today: date | None = None,
    log=print,
) -> RefreshReport:
    today = today or date.today()
    fetched_at = utcnow()
    run_id = store.start_run("refresh")
    report = RefreshReport(run_id=run_id)
    fetch_status: dict[str, tuple[str, str]] = {}

    specs = catalog.fetched()
    if only:
        specs = [s for s in specs if s.id in only]
    fetcher.prefetch_yahoo(specs)

    for spec in specs:
        started = utcnow()
        try:
            frame = fetcher.fetch(spec)
            if frame is None or frame.empty:
                raise SourceError("no observations returned")
            n = store.upsert_series(spec.id, frame, source=spec.source, fetched_at=fetched_at)
            store.log_fetch(spec.id, source=spec.source, key=spec.key, status="ok", n_rows=n, started=started)
            fetch_status[spec.id] = ("ok", "")
            report.n_ok += 1
            if log:
                log(f"  {spec.id}: {len(frame)} obs, {n} new")
        except Exception as exc:
            message = str(exc) if isinstance(exc, SourceError) else f"{type(exc).__name__}: {exc}"
            store.log_fetch(spec.id, source=spec.source, key=spec.key, status="failed", message=message, started=started)
            fetch_status[spec.id] = ("failed", message)
            report.n_failed += 1
            if log:
                log(f"  {spec.id}: FAILED {message[:200]}")

    for symbol, frame in fetcher.yahoo_frames.items():
        store.upsert_prices(symbol, frame, fetched_at=fetched_at)
    for result in fetcher.downloads:
        store.record_file(result.name, url=result.url, sha256=result.sha256, n_bytes=result.n_bytes, path=str(result.path))

    if releases:
        try:
            added = ingest_releases(store, releases, catalog, fetched_at=fetched_at)
            if log:
                log(f"  releases: {len(releases)} entries, {added} new")
        except Exception as exc:
            if log:
                log(f"  releases: FAILED {exc}")
            for spec in catalog.releases():
                fetch_status[spec.id] = ("failed", f"releases file: {exc}")

    derived_problems: dict[str, tuple[str, str]] = {}
    if not skip_derived:
        derived_problems = compute_derived(store, catalog, fetched_at=fetched_at, log=log)

    rows_by_id: dict[str, FreshnessRow] = {}
    # Fetched and release series first, then derived ones in dependency order, so
    # a derived series can look up the status of its inputs.
    ordered = [s for s in catalog if not s.is_derived] + catalog.derived_in_order()
    for spec in ordered:
        if only and spec.id not in only and not spec.is_derived and not spec.is_release:
            row = FreshnessRow(spec.id, spec.source, "skipped", None, None, spec.stale_after_days, spec.critical)
        else:
            status, message = fetch_status.get(spec.id, (None, ""))
            if spec.is_derived and spec.id in derived_problems:
                status, message = derived_problems[spec.id]
                if status == "ok":
                    status = None  # computed on a subset; the note travels with the row
            row = _status_for(spec, store, catalog, today, status, message, rows_by_id)
            if spec.is_derived and status == "manual":
                row.status = "manual"
        rows_by_id[spec.id] = row
    report.rows = [rows_by_id[s.id] for s in catalog]  # back in catalog order
    _apply_fallbacks(report.rows, catalog)

    store.save_snapshot(run_id, "freshness", [r.as_dict() for r in report.rows])
    if not skip_derived:
        # The scorecard as it stood after this run, so the next run (and the
        # report) can say what changed since.
        from invest.macro import scorecard

        try:
            readings = scorecard.build_readings(store, catalog, today=today)
            reg, results = scorecard.build_regime(store, catalog)
            store.save_snapshot(run_id, "scorecard", scorecard.build_snapshot(readings, reg, results))
            if log:
                log(f"  regime: {reg.label} ({', '.join(reg.firing) or 'no counted rule firing'})")
        except Exception as exc:  # the data is stored; a scorecard problem must not lose the run
            if log:
                log(f"  scorecard snapshot FAILED: {exc}")
    notes = "; ".join(f"{r.id}: {r.status}" for r in report.failures)[:4000]
    store.finish_run(run_id, ok=report.n_ok, failed=report.n_failed, notes=notes)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the indicator catalog into DuckDB.")
    parser.add_argument("--db", type=Path, default=None, help="DuckDB file (default: data/market.duckdb)")
    parser.add_argument("--only", default="", help="comma-separated series ids to fetch")
    parser.add_argument("--skip-derived", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    catalog = load_catalog()
    releases = load_releases()
    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    log = (lambda *a, **k: None) if args.quiet else print

    with Store(args.db) as store:
        fetcher = Fetcher(fred_api_key=fred_api_key())
        try:
            report = refresh(store, catalog, fetcher, only=only, releases=releases, skip_derived=args.skip_derived, log=log)
        except Exception:
            traceback.print_exc()
            return 2
    print()
    print(report.table())
    print()
    print(f"run {report.run_id}: {report.n_ok} fetched, {report.n_failed} failed, "
          f"{len(report.critical_failures)} critical problem(s)")
    for row in report.critical_failures:
        print(f"  CRITICAL {row.id}: {row.status} {row.message}")
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
