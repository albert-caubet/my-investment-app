"""The indicator catalog: ``config/series.toml`` loaded and validated.

A catalog entry says where a series comes from, how often it updates and how
late, how to read it (transform, direction of concern, z-score window), which
group of the scorecard it belongs to, and what to fall back on. Validation is
strict because a typo here silently drops an indicator from the report.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from invest.macro import derived
from invest.paths import CONFIG_DIR

SERIES_PATH = CONFIG_DIR / "series.toml"

SOURCES = frozenset(
    {"fred", "ecb", "eurostat", "oecd", "bis", "yahoo", "shiller", "ebp", "philly",
     "nyfed_recprob", "hlw", "cot", "release", "derived"}
)
#: ``I`` is irregular (policy-rate changes): never stale by construction.
FREQUENCIES = frozenset({"D", "W", "M", "Q", "A", "I"})
DIRECTIONS = frozenset({"high_bad", "low_bad", "neutral"})
GROUPS = {
    "rates": "Rates and curve",
    "policy": "Policy stance and currency",
    "surveys": "Business surveys",
    "composites": "Leading composites and nowcasts",
    "inflation": "Inflation",
    "labor": "Labor",
    "housing": "Housing",
    "investment": "Business investment and trade",
    "consumer": "Consumer",
    "credit": "Credit and liquidity",
    "fiscal": "Fiscal",
    "valuation": "Valuation",
    "positioning": "Positioning and sentiment",
    "internals": "Market internals",
}
#: Days in one period of each frequency, for staleness thresholds.
PERIOD_DAYS = {"D": 1, "W": 7, "M": 31, "Q": 92, "A": 366, "I": 0}
STALE_GRACE_DAYS = 7


class CatalogError(ValueError):
    """The catalog file is not usable. The message names the entry and the field."""


@dataclass(frozen=True)
class SeriesSpec:
    id: str
    label: str
    group: str
    source: str
    key: str | None
    frequency: str
    lag_days: int
    transform: str
    direction: str
    units: str
    critical: bool
    show: bool
    fallback: str | None
    inputs: tuple[str, ...]
    formula: str | None
    z_window_years: int
    notes: str
    start: str | None = None
    field_: str | None = None

    @property
    def is_derived(self) -> bool:
        return self.source == "derived"

    @property
    def is_release(self) -> bool:
        return self.source == "release"

    @property
    def stale_after_days(self) -> int | None:
        """Days after the last observation date beyond which the series is stale.

        Observations are dated at the *start* of their period, so just before a
        release the newest observation is two periods old plus the publication
        lag: on September 30th the latest monthly value is August's, dated
        August 1st. ``None`` for irregular series and for hand-entered releases,
        which have no fetcher to be late. Derived series take the limit of their
        slowest input (see :meth:`Catalog.stale_after`).
        """
        if self.frequency == "I" or self.is_release:
            return None
        return 2 * PERIOD_DAYS[self.frequency] + self.lag_days + STALE_GRACE_DAYS


@dataclass(frozen=True)
class Catalog:
    series: tuple[SeriesSpec, ...]
    by_id: dict[str, SeriesSpec] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "by_id", {s.id: s for s in self.series})

    def __iter__(self):
        return iter(self.series)

    def __len__(self):
        return len(self.series)

    def __getitem__(self, series_id: str) -> SeriesSpec:
        return self.by_id[series_id]

    def fetched(self) -> list[SeriesSpec]:
        """Everything that comes from a source, in file order."""
        return [s for s in self.series if not s.is_derived and not s.is_release]

    def releases(self) -> list[SeriesSpec]:
        return [s for s in self.series if s.is_release]

    def derived_in_order(self) -> list[SeriesSpec]:
        """Derived series topologically sorted so inputs are computed first."""
        pending = {s.id: s for s in self.series if s.is_derived}
        done: list[SeriesSpec] = []
        resolved: set[str] = {s.id for s in self.series if not s.is_derived}
        while pending:
            ready = [s for s in pending.values() if all(i in resolved for i in s.inputs)]
            if not ready:
                cycle = ", ".join(sorted(pending))
                raise CatalogError(f"derived series form a cycle or reference each other unresolvably: {cycle}")
            for spec in ready:
                done.append(spec)
                resolved.add(spec.id)
                del pending[spec.id]
        return done

    def stale_after(self, spec: SeriesSpec) -> int | None:
        """Staleness limit for any series; a derived one inherits its slowest input's."""
        if not spec.is_derived:
            return spec.stale_after_days
        limits = [self.stale_after(self.by_id[i]) for i in spec.inputs]
        limits = [l for l in limits if l is not None]
        return max(limits) if limits else None

    def scorecard(self) -> list[SeriesSpec]:
        return [s for s in self.series if s.show]

    def groups(self) -> list[str]:
        seen: list[str] = []
        for s in self.series:
            if s.show and s.group not in seen:
                seen.append(s.group)
        return seen


def _entry(raw: dict, defaults: dict, index: int) -> SeriesSpec:
    def need(name: str):
        if name not in raw:
            raise CatalogError(f"series #{index} ({raw.get('id', '?')}): missing {name!r}")
        return raw[name]

    sid = str(need("id")).strip()
    source = str(need("source")).strip()
    if source not in SOURCES:
        raise CatalogError(f"{sid}: unknown source {source!r}")
    frequency = str(raw.get("frequency", "M")).strip()
    if frequency not in FREQUENCIES:
        raise CatalogError(f"{sid}: unknown frequency {frequency!r}")
    direction = str(raw.get("direction", "neutral")).strip()
    if direction not in DIRECTIONS:
        raise CatalogError(f"{sid}: unknown direction {direction!r}")
    transform = str(raw.get("transform", "level")).strip()
    if transform not in derived.TRANSFORMS:
        raise CatalogError(f"{sid}: unknown transform {transform!r}")
    group = str(need("group")).strip()
    if group not in GROUPS:
        raise CatalogError(f"{sid}: unknown group {group!r}")
    inputs = tuple(str(i) for i in raw.get("inputs", ()))
    formula = raw.get("formula")
    key = raw.get("key")
    if source == "derived":
        if not formula:
            raise CatalogError(f"{sid}: derived series needs a formula")
        if formula not in derived.FORMULAS:
            raise CatalogError(f"{sid}: unknown formula {formula!r}")
        if not inputs:
            raise CatalogError(f"{sid}: derived series needs inputs")
    elif source != "release" and not key:
        raise CatalogError(f"{sid}: source {source!r} needs a key")
    lag = int(raw.get("lag_days", defaults.get("lag_days", 0)))
    if lag < 0:
        raise CatalogError(f"{sid}: lag_days must be >= 0")
    return SeriesSpec(
        id=sid,
        label=str(raw.get("label", sid)),
        group=group,
        source=source,
        key=str(key) if key is not None else None,
        frequency=frequency,
        lag_days=lag,
        transform=transform,
        direction=direction,
        units=str(raw.get("units", "")),
        critical=bool(raw.get("critical", defaults.get("critical", False))),
        show=bool(raw.get("show", True)),
        fallback=(str(raw["fallback"]) if raw.get("fallback") else None),
        inputs=inputs,
        formula=str(formula) if formula else None,
        z_window_years=int(raw.get("z_window_years", defaults.get("z_window_years", 10))),
        notes=str(raw.get("notes", "")),
        start=(str(raw["start"]) if raw.get("start") else None),
        field_=(str(raw["field"]) if raw.get("field") else None),
    )


def parse_catalog(payload: dict) -> Catalog:
    defaults = payload.get("defaults", {})
    entries = payload.get("series")
    if not entries:
        raise CatalogError("catalog has no [[series]] entries")
    specs = [_entry(raw, defaults, i + 1) for i, raw in enumerate(entries)]
    ids = [s.id for s in specs]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise CatalogError(f"duplicate series ids: {dupes}")
    known = set(ids)
    for spec in specs:
        if spec.fallback and spec.fallback not in known:
            raise CatalogError(f"{spec.id}: fallback {spec.fallback!r} is not in the catalog")
        if spec.fallback == spec.id:
            raise CatalogError(f"{spec.id}: a series cannot be its own fallback")
        for inp in spec.inputs:
            if inp not in known:
                raise CatalogError(f"{spec.id}: input {inp!r} is not in the catalog")
    catalog = Catalog(tuple(specs))
    catalog.derived_in_order()  # raises on cycles
    return catalog


def load_catalog(path: Path = SERIES_PATH) -> Catalog:
    with Path(path).open("rb") as handle:
        return parse_catalog(tomllib.load(handle))
