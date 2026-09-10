"""Rebalancing: drift against targets, bands, contribution first, then trades. Pure.

Money is in EUR throughout, like the portfolio engine. The plan proposes
bucket-level amounts; which holding to buy or sell inside a bucket is the
reader's decision, and the report lists the candidates (funds first, because a
transfer between funds defers tax in Spain).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from invest.paths import CONFIG_DIR

TARGETS_PATH = CONFIG_DIR / "targets.toml"
FUND_CATEGORIES = frozenset({"Fund"})


class TargetsError(ValueError):
    """The targets file is not usable."""


@dataclass(frozen=True)
class Targets:
    weights: dict[str, float]          # bucket -> percent, summing to 100
    band_abs: float                    # percentage points
    band_rel: float                    # percent of target
    trade_to: str                      # "target" | "band_edge"
    min_trade_eur: float
    contribution_eur: float
    tilt_enabled: bool
    tilt_max_points: float
    category_map: dict[str, str]
    asset_map: dict[str, str]

    def bucket_of(self, category: str | None, asset_id: str) -> str:
        if asset_id in self.asset_map:
            return self.asset_map[asset_id]
        return self.category_map.get(category or "", "other")


def parse_targets(payload: dict) -> Targets:
    weights = {str(k): float(v) for k, v in (payload.get("targets") or {}).items()}
    if not weights:
        raise TargetsError("no [targets]")
    total = sum(weights.values())
    if abs(total - 100.0) > 0.01:
        raise TargetsError(f"target weights sum to {total:g}, not 100")
    if any(w < 0 for w in weights.values()):
        raise TargetsError("target weights must be non-negative")
    bands = payload.get("bands") or {}
    trade_to = str(bands.get("trade_to", "target"))
    if trade_to not in ("target", "band_edge"):
        raise TargetsError(f"trade_to must be target or band_edge, not {trade_to!r}")
    tilts = payload.get("tilts") or {}
    maps = payload.get("map") or {}
    return Targets(
        weights=weights,
        band_abs=float(bands.get("absolute", 5.0)),
        band_rel=float(bands.get("relative", 25.0)),
        trade_to=trade_to,
        min_trade_eur=float(bands.get("min_trade_eur", 0.0)),
        contribution_eur=float((payload.get("contribution") or {}).get("amount_eur", 0.0)),
        tilt_enabled=bool(tilts.get("enabled", False)),
        tilt_max_points=float(tilts.get("max_points", 0.0)),
        category_map={str(k): str(v) for k, v in (maps.get("category") or {}).items()},
        asset_map={str(k): str(v) for k, v in (maps.get("asset") or {}).items()},
    )


def load_targets(path: Path = TARGETS_PATH) -> Targets:
    with Path(path).open("rb") as handle:
        return parse_targets(tomllib.load(handle))


@dataclass(frozen=True)
class Holding:
    asset_id: str
    name: str
    category: str | None
    bucket: str
    value_eur: float


@dataclass(frozen=True)
class BucketDrift:
    bucket: str
    value_eur: float
    weight: float          # percent
    target: float          # percent
    drift: float           # percentage points, actual minus target
    drift_rel: float | None  # percent of target
    lower: float           # band edge, percent
    upper: float
    out_of_band: bool
    to_target_eur: float   # positive: buy this much to reach the target

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class Trade:
    bucket: str
    action: str            # buy | sell
    amount_eur: float
    reason: str
    candidates: tuple[str, ...] = ()   # holdings in the bucket, funds first
    traspaso_possible: bool = False

    def as_dict(self) -> dict:
        out = self.__dict__.copy()
        out["candidates"] = list(self.candidates)
        return out


@dataclass
class Plan:
    total_eur: float
    contribution_eur: float
    drifts: list[BucketDrift]
    contribution_allocation: dict[str, float]
    drifts_after_contribution: list[BucketDrift]
    trades: list[Trade]
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total_eur": self.total_eur,
            "contribution_eur": self.contribution_eur,
            "drifts": [d.as_dict() for d in self.drifts],
            "contribution_allocation": dict(self.contribution_allocation),
            "drifts_after_contribution": [d.as_dict() for d in self.drifts_after_contribution],
            "trades": [t.as_dict() for t in self.trades],
            "notes": list(self.notes),
        }


def apply_tilt(targets: Targets, tilt_points: Mapping[str, float]) -> tuple[Targets, list[str]]:
    """Strategic weights shifted by bounded tactical points, renormalised to 100.

    Off unless the file enables it; every applied tilt is listed in the notes so
    the report can print it next to the strategic weight.
    """
    notes = []
    if not targets.tilt_enabled or not tilt_points:
        return targets, notes
    weights = dict(targets.weights)
    for bucket, points in tilt_points.items():
        if bucket not in weights:
            continue
        bounded = max(-targets.tilt_max_points, min(targets.tilt_max_points, float(points)))
        weights[bucket] = max(0.0, weights[bucket] + bounded)
        notes.append(f"tilt {bucket} {bounded:+.1f} points (strategic {targets.weights[bucket]:.0f}%)")
    total = sum(weights.values())
    if total > 0:
        weights = {k: v / total * 100.0 for k, v in weights.items()}
    return Targets(**{**targets.__dict__, "weights": weights}), notes


def bucket_values(holdings: Iterable[Holding]) -> dict[str, float]:
    out: dict[str, float] = {}
    for h in holdings:
        out[h.bucket] = out.get(h.bucket, 0.0) + float(h.value_eur)
    return out


def drift_table(values: Mapping[str, float], targets: Targets) -> list[BucketDrift]:
    """One row per bucket named in the targets or held, ordered by drift."""
    total = sum(values.values())
    buckets = list(targets.weights) + [b for b in values if b not in targets.weights]
    rows = []
    for bucket in buckets:
        value = float(values.get(bucket, 0.0))
        target = float(targets.weights.get(bucket, 0.0))
        weight = value / total * 100.0 if total > 0 else 0.0
        drift = weight - target
        drift_rel = (drift / target * 100.0) if target > 0 else None
        band = min(targets.band_abs, target * targets.band_rel / 100.0) if target > 0 else targets.band_abs
        lower, upper = max(0.0, target - band), target + band
        out = weight < lower - 1e-9 or weight > upper + 1e-9
        rows.append(BucketDrift(bucket, value, weight, target, drift, drift_rel, lower, upper, out,
                                target / 100.0 * total - value))
    return sorted(rows, key=lambda r: r.drift)


def allocate_contribution(values: Mapping[str, float], targets: Targets, amount: float) -> dict[str, float]:
    """New cash to the most underweight buckets first, until every shortfall is filled.

    Water-filling against the post-contribution total: each euro goes to the
    bucket furthest below its target weight of (total + contribution). Buckets
    with a zero target never receive cash.
    """
    if amount <= 0:
        return {}
    total_after = sum(values.values()) + amount
    alloc = {b: 0.0 for b in targets.weights}
    remaining = amount
    step = max(amount / 1000.0, 1.0)
    for _ in range(100_000):
        if remaining <= 1e-9:
            break
        shortfalls = {
            b: targets.weights[b] / 100.0 * total_after - (values.get(b, 0.0) + alloc[b])
            for b in targets.weights if targets.weights[b] > 0
        }
        bucket, gap = max(shortfalls.items(), key=lambda kv: kv[1])
        if gap <= 1e-9:
            # every bucket is at or above target: spread the rest by target weight
            for b in alloc:
                alloc[b] += remaining * targets.weights[b] / 100.0
            remaining = 0.0
            break
        give = min(step, remaining, gap)
        alloc[bucket] += give
        remaining -= give
    return {b: round(v, 2) for b, v in alloc.items() if v > 0.005}


def rebalance(holdings: Iterable[Holding], targets: Targets, *, contribution_eur: float | None = None) -> Plan:
    """Drift, contribution allocation and the trade list.

    New cash first: the planned contribution buys the most underweight buckets.
    Then, for buckets still outside their band, sells of the overweight and buys
    of the underweight bring them to the target (or the band edge), trades
    below the minimum are dropped, and the plan says whether the sells fund the
    buys. Fund-to-fund legs are flagged as possible traspasos.
    """
    holdings = list(holdings)
    values = bucket_values(holdings)
    total = sum(values.values())
    contribution = targets.contribution_eur if contribution_eur is None else float(contribution_eur)
    drifts = drift_table(values, targets)
    notes: list[str] = []
    if total <= 0:
        return Plan(0.0, contribution, drifts, {}, drifts, [], ["portfolio has no valued holdings"])

    alloc = allocate_contribution(values, targets, contribution)
    after = {b: values.get(b, 0.0) + alloc.get(b, 0.0) for b in set(values) | set(alloc)}
    drifts_after = drift_table(after, targets)
    total_after = sum(after.values())

    by_bucket: dict[str, list[Holding]] = {}
    for h in holdings:
        by_bucket.setdefault(h.bucket, []).append(h)

    def candidates(bucket: str) -> tuple[str, ...]:
        rows = sorted(by_bucket.get(bucket, []), key=lambda h: (h.category not in FUND_CATEGORIES, -h.value_eur))
        return tuple(f"{h.name} ({h.category or 'uncategorised'})" for h in rows)

    trades: list[Trade] = []
    for row in drifts_after:
        if not row.out_of_band:
            continue
        if targets.trade_to == "target":
            amount = row.to_target_eur
            goal = "to target"
        else:
            edge = row.upper if row.drift > 0 else row.lower
            amount = edge / 100.0 * total_after - row.value_eur
            goal = "to the band edge"
        if abs(amount) < targets.min_trade_eur:
            notes.append(f"{row.bucket}: drift {row.drift:+.1f} points but the trade ({abs(amount):,.0f} EUR) is below the minimum")
            continue
        action = "buy" if amount > 0 else "sell"
        reason = (f"{row.weight:.1f}% vs target {row.target:.0f}% (band {row.lower:.1f}% to {row.upper:.1f}%), {goal}")
        trades.append(Trade(row.bucket, action, round(abs(amount), 2), reason, candidates(row.bucket)))

    sells = [t for t in trades if t.action == "sell"]
    buys = [t for t in trades if t.action == "buy"]
    if sells and buys:
        fund_sells = any(any("(Fund)" in c for c in t.candidates) for t in sells)
        fund_buys = any(any("(Fund)" in c for c in t.candidates) for t in buys)
        if fund_sells and fund_buys:
            trades = [Trade(t.bucket, t.action, t.amount_eur, t.reason, t.candidates, True) for t in trades]
            notes.append("both legs can be funds: a traspaso between funds defers the capital gain (Spain)")
    sold, bought = sum(t.amount_eur for t in sells), sum(t.amount_eur for t in buys)
    if trades:
        notes.append(f"sells {sold:,.0f} EUR, buys {bought:,.0f} EUR; difference {sold - bought:+,.0f} EUR stays in or comes from cash")
    if contribution > 0 and alloc:
        notes.append("contribution allocated before any sale: " + ", ".join(f"{b} {v:,.0f}" for b, v in alloc.items()))
    if not trades and not any(r.out_of_band for r in drifts_after):
        notes.append("every bucket is inside its band; no trade proposed")
    return Plan(total, contribution, drifts, alloc, drifts_after, trades, notes)
