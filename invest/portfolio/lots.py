"""FIFO tax lots from the same transactions the average-cost engine replays. Pure.

Spanish capital-gains tax matches sales against the oldest purchases first,
while the app measures performance with average cost. Both are right for their
purpose; this module gives the tax view and estimates the taxable gain of a
proposed sale. Total profit (realised plus unrealised) is identical under both
methods; only the split between realised and unrealised differs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from portfolio_math import Transaction

QTY_EPS = 1e-9


@dataclass
class Lot:
    trade_date: date
    quantity: float           # remaining units
    cost_eur_per_unit: float  # fees included


@dataclass
class FifoState:
    asset_id: str
    open_lots: list[Lot] = field(default_factory=list)
    realised_eur: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def quantity(self) -> float:
        return sum(lot.quantity for lot in self.open_lots)

    @property
    def cost_basis_eur(self) -> float:
        return sum(lot.quantity * lot.cost_eur_per_unit for lot in self.open_lots)


def fifo_lots(txs: Sequence[Transaction]) -> FifoState:
    """Replay chronologically; sells consume the oldest lots first."""
    if not txs:
        raise ValueError("fifo_lots requires at least one transaction")
    state = FifoState(asset_id=txs[0].asset_id)
    for tx in sorted(txs, key=lambda t: (t.trade_date, t.seq)):
        if tx.is_buy:
            state.open_lots.append(Lot(tx.trade_date, tx.quantity, tx.net_eur / tx.quantity))
            continue
        proceeds_per_unit = tx.net_eur / tx.quantity
        remaining = tx.quantity
        while remaining > QTY_EPS and state.open_lots:
            lot = state.open_lots[0]
            take = min(lot.quantity, remaining)
            state.realised_eur += take * (proceeds_per_unit - lot.cost_eur_per_unit)
            lot.quantity -= take
            remaining -= take
            if lot.quantity <= QTY_EPS:
                state.open_lots.pop(0)
        if remaining > QTY_EPS:
            state.warnings.append(
                f"sell of {tx.quantity:g} on {tx.trade_date} exceeds the lots held by {remaining:g}; "
                f"the excess is booked as pure gain"
            )
            state.realised_eur += remaining * proceeds_per_unit
    return state


@dataclass(frozen=True)
class SaleEstimate:
    quantity: float
    proceeds_eur: float
    cost_eur: float
    gain_eur: float
    lots_used: tuple[tuple[date, float, float], ...]  # (bought, quantity, cost per unit)
    short_by: float  # units requested beyond what is held

    def as_dict(self) -> dict:
        return {
            "quantity": self.quantity, "proceeds_eur": self.proceeds_eur, "cost_eur": self.cost_eur,
            "gain_eur": self.gain_eur, "short_by": self.short_by,
            "lots_used": [{"bought": d.isoformat(), "quantity": q, "cost_per_unit": c} for d, q, c in self.lots_used],
        }


def taxable_gain(state: FifoState, quantity: float, price_eur_per_unit: float) -> SaleEstimate:
    """Gain of selling ``quantity`` units at ``price_eur_per_unit`` under FIFO, lots consumed oldest first."""
    remaining = float(quantity)
    cost = 0.0
    used = []
    for lot in state.open_lots:
        if remaining <= QTY_EPS:
            break
        take = min(lot.quantity, remaining)
        cost += take * lot.cost_eur_per_unit
        used.append((lot.trade_date, take, lot.cost_eur_per_unit))
        remaining -= take
    sold = float(quantity) - max(remaining, 0.0)
    proceeds = sold * price_eur_per_unit
    return SaleEstimate(sold, proceeds, cost, proceeds - cost, tuple(used), max(remaining, 0.0))


def units_for_amount(state: FifoState, amount_eur: float, price_eur_per_unit: float) -> float:
    """How many units a EUR amount is at the given price, capped at what is held."""
    if price_eur_per_unit <= 0:
        return 0.0
    return min(amount_eur / price_eur_per_unit, state.quantity)
