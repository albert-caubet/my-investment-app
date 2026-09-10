"""Scorecard assembly: store + catalog -> readings, regime, changes, freshness.

The I/O glue between the DuckDB store and the pure macro modules. Pages and the
report call this; nothing here touches the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from invest.data.cache import Store
from invest.macro.catalog import Catalog, SeriesSpec
from invest.macro.indicators import IndicatorReading, indicator
from invest.macro.regimes import Regime, RuleResult, regime

CONCERN_THRESHOLD = 1.0
CHANGE_BASELINE_DAYS = 6


def load_series(store: Store, ids) -> dict[str, pd.Series]:
    return {sid: store.read_series(sid) for sid in ids}


def series_with_fallback(
    store: Store, catalog: Catalog, spec: SeriesSpec, *, today: date | None
) -> tuple[pd.Series, str]:
    """The series for a spec, or its fallback when the primary is missing or stale.

    Returns ``(series, note)``; the note names the fallback so the reading can
    say where its number came from.
    """
    primary = store.read_series(spec.id)
    limit = catalog.stale_after(spec)
    stale = False
    if not primary.empty and today is not None and limit is not None:
        stale = (today - primary.index[-1].date()).days > limit
    if (primary.empty or stale) and spec.fallback:
        alt = store.read_series(spec.fallback)
        if not alt.empty and (primary.empty or alt.index[-1] > primary.index[-1]):
            return alt, f"via fallback {spec.fallback}"
    return primary, ""


def build_readings(
    store: Store, catalog: Catalog, *, today: date | None = None, as_of: date | None = None
) -> list[IndicatorReading]:
    """One reading per scorecard series (``show = true``), in catalog order."""
    readings = []
    for spec in catalog.scorecard():
        series, note = series_with_fallback(store, catalog, spec, today=today)
        readings.append(
            indicator(series, spec, as_of=as_of, today=today, stale_after_days=catalog.stale_after(spec), note=note)
        )
    return readings


def rule_inputs(catalog: Catalog) -> list[str]:
    from invest.macro.regimes import RULES

    ids: list[str] = []
    for rule, _ in RULES:
        for sid in rule.inputs:
            if sid not in ids:
                ids.append(sid)
    return ids


def build_regime(store: Store, catalog: Catalog, *, as_of: date | None = None) -> tuple[Regime, list[RuleResult]]:
    series = load_series(store, rule_inputs(catalog))
    return regime(series, as_of=as_of)


def build_snapshot(readings: list[IndicatorReading], reg: Regime, results: list[RuleResult]) -> dict:
    return {
        "readings": [r.as_dict() for r in readings],
        "regime": reg.as_dict(),
        "rules": [r.as_dict() for r in results],
    }


@dataclass(frozen=True)
class Change:
    id: str
    label: str
    kind: str  # new observation | concern crossed | rule fired | rule cleared
    before: str
    after: str
    detail: str = ""


def changes_since(
    readings: list[IndicatorReading], results: list[RuleResult], previous: dict | None
) -> list[Change]:
    """What moved between a previous snapshot and the current readings.

    Reported: a new observation date with its value change; a concern score
    crossing +1 in either direction; a rule that fired or cleared. Nothing is
    reported for series absent from the earlier snapshot.
    """
    if not previous:
        return []
    old_readings = {r["id"]: r for r in previous.get("readings", [])}
    old_rules = {r["name"]: r for r in previous.get("rules", [])}
    out: list[Change] = []
    for r in readings:
        old = old_readings.get(r.id)
        if old is None or r.value is None:
            continue
        old_date = old.get("obs_date")
        if old_date and r.obs_date and r.obs_date.isoformat() != old_date and old.get("value") is not None:
            out.append(
                Change(r.id, r.label, "new observation", f"{old['value']:.4g} ({old_date})",
                       f"{r.value:.4g} ({r.obs_date.isoformat()})")
            )
        old_c, new_c = old.get("concern"), r.concern
        if old_c is not None and new_c is not None:
            if old_c < CONCERN_THRESHOLD <= new_c:
                out.append(Change(r.id, r.label, "concern crossed", f"z {old_c:+.2f}", f"z {new_c:+.2f}", "above +1"))
            elif new_c < CONCERN_THRESHOLD <= old_c:
                out.append(Change(r.id, r.label, "concern crossed", f"z {old_c:+.2f}", f"z {new_c:+.2f}", "back below +1"))
    for res in results:
        old = old_rules.get(res.rule.name)
        if old is None or res.fired is None or old.get("fired") is None:
            continue
        if res.fired and not old["fired"]:
            out.append(Change(res.rule.name, res.rule.description, "rule fired", "not firing", "firing", res.detail))
        elif old["fired"] and not res.fired:
            out.append(Change(res.rule.name, res.rule.description, "rule cleared", "firing", "not firing", res.detail))
    return out


def previous_snapshot(store: Store, kind: str, *, before: datetime | None = None, min_age_days: int = CHANGE_BASELINE_DAYS):
    """The latest snapshot at least ``min_age_days`` older than ``before`` (default: now).

    Falls back to the oldest snapshot when nothing is that old, and to ``None``
    when there is only the current one. Returns ``(run_id, created_at, payload)``.
    """
    latest = store.load_snapshot(kind)
    if latest is None:
        return None
    anchor = before or latest[1]
    cutoff = anchor - timedelta(days=min_age_days)
    candidate = store.load_snapshot(kind, before=cutoff)
    if candidate is not None:
        return candidate
    listing = store.snapshots(kind, limit=1000)
    if len(listing) < 2:
        return None
    oldest = listing.iloc[-1]
    return store.load_snapshot(kind, before=pd.Timestamp(oldest["created_at"]).to_pydatetime() + timedelta(microseconds=1))


def freshness_table(store: Store, catalog: Catalog, *, today: date | None = None) -> pd.DataFrame:
    """Per-series freshness for the page footer and the report header.

    Uses the last refresh's freshness snapshot when there is one (it carries the
    fetch outcome), and recomputes ages against ``today`` so a footer read days
    after the run still tells the truth.
    """
    today = today or date.today()
    snap = store.load_snapshot("freshness")
    by_id = {row["id"]: row for row in snap[2]} if snap else {}
    rows = []
    for spec in catalog:
        meta = store.series_meta(spec.id)
        last = meta.last_obs
        age = (today - last).days if last else None
        limit = catalog.stale_after(spec)
        snap_row = by_id.get(spec.id, {})
        status = snap_row.get("status")
        # A fetch failure stands until the next refresh; everything else is judged on
        # what the store holds now, so a series written by another job after the
        # refresh (breadth) or a release entered since is not reported missing.
        if status in (None, "ok", "stale", "fallback", "missing", "manual"):
            if last is None:
                status = "manual" if spec.is_release else "missing"
            elif limit is not None and age is not None and age > limit:
                status = "stale"
            elif status in (None, "missing", "manual"):
                status = "ok"
        rows.append(
            {
                "id": spec.id,
                "label": spec.label,
                "group": spec.group,
                "source": spec.source,
                "key": spec.key or spec.formula or "",
                "last_obs": last,
                "age_days": age,
                "limit_days": limit,
                "status": status,
                "critical": spec.critical,
                "message": snap_row.get("message", ""),
                "last_fetched": meta.last_fetched,
            }
        )
    return pd.DataFrame(rows)
