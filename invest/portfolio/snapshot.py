"""Portfolio valuation for the jobs: the same pure engine as the page, fed outside Streamlit.

Transactions come from Firestore (as the app) or from a JSON export named by
``INVEST_TRANSACTIONS_JSON``; prices and FX from Yahoo through ``invest.data.yahoo``.
Nothing here defaults a currency or a rate: a position that cannot be valued is
listed as such, and the report prints the list.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import portfolio_math as pm
from invest.data import yahoo
from invest.portfolio.rebalance import Holding, Targets

TRANSACTIONS_ENV = "INVEST_TRANSACTIONS_JSON"


def load_transaction_docs() -> tuple[list[dict], str]:
    """``(docs, source)``: the JSON export when configured, else Firestore. Raises when neither works."""
    path = os.environ.get(TRANSACTIONS_ENV)
    if path:
        docs = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(docs, list):
            raise ValueError(f"{path}: expected a JSON list of transaction documents")
        return docs, f"json export {path}"
    import database  # Firestore; imports Streamlit, so only when actually used

    return database.get_all_transactions(), "Firestore"


@dataclass
class PortfolioSnapshot:
    as_of: date
    source: str
    positions: dict = field(default_factory=dict)
    valuations: dict = field(default_factory=dict)
    totals: pm.PortfolioTotals | None = None
    mwr: pm.XirrResult | None = None
    weights: dict = field(default_factory=dict)
    holdings: list[Holding] = field(default_factory=list)
    prices_eur: dict[str, float] = field(default_factory=dict)  # per unit, for lot estimates
    problems: list[str] = field(default_factory=list)
    unvalued: list[str] = field(default_factory=list)
    transactions: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "source": self.source,
            "market_value_eur": self.totals.market_value_eur if self.totals else None,
            "cost_basis_eur": self.totals.cost_basis_eur if self.totals else None,
            "total_pnl_eur": self.totals.total_pnl_eur if self.totals else None,
            "pnl_pct": self.totals.pnl_pct if self.totals else None,
            "mwr": self.mwr.rate if self.mwr and self.mwr.credible else None,
            "mwr_note": self.mwr.note if self.mwr else None,
            "weights": dict(self.weights),
            "holdings": [h.__dict__ for h in self.holdings],
            "unvalued": list(self.unvalued),
            "problems": list(self.problems),
        }


def value_portfolio(docs: list[dict], targets: Targets, *, source: str, today: date | None = None,
                    spot=None, fx=None, currencies=None) -> PortfolioSnapshot:
    """Replay, price and weigh the portfolio; ``spot``/``fx``/``currencies`` can be injected for tests."""
    today = today or date.today()
    snap = PortfolioSnapshot(as_of=today, source=source)
    transactions, problems = pm.load_transactions(docs)
    snap.problems.extend(problems)
    snap.transactions = transactions
    if not transactions:
        snap.problems.append("no usable transactions")
        return snap
    positions = pm.build_positions(transactions)
    snap.positions = positions
    open_positions = {aid: p for aid, p in positions.items() if p.is_open}
    symbols = sorted({p.price_symbol for p in open_positions.values()})

    if spot is None:
        frames = yahoo.fetch_history(symbols, period="10d") if symbols else {}
        spot = {s: float(f["close"].dropna().iloc[-1]) for s, f in frames.items() if not f["close"].dropna().empty}
    if currencies is None:
        currencies = {}
        for symbol in symbols:
            stored = next((p.listing_ccy for p in open_positions.values() if p.price_symbol == symbol and p.listing_ccy), None)
            currencies[symbol] = yahoo.fetch_currency(symbol) or stored
    if fx is None:
        wanted = sorted({c for c in currencies.values() if c and c != pm.BASE_CCY})
        fx = {pm.BASE_CCY: 1.0}
        if wanted:
            frames = yahoo.fetch_history([f"{pm.BASE_CCY}{c}=X" for c in wanted], period="10d")
            for c in wanted:
                frame = frames.get(f"{pm.BASE_CCY}{c}=X")
                if frame is not None and not frame["close"].dropna().empty:
                    fx[c] = float(frame["close"].dropna().iloc[-1])

    valuations = {}
    for aid, pos in positions.items():
        quote = pm.Quote(aid, spot.get(pos.price_symbol), currencies.get(pos.price_symbol))
        valuations[aid] = pm.value_position(pos, quote, fx)
    snap.valuations = valuations
    snap.totals = pm.portfolio_totals(list(valuations.values()))
    snap.unvalued = [aid for aid in open_positions if valuations[aid].market_value_eur is None]
    snap.weights = pm.weights([valuations[a] for a in open_positions])
    snap.mwr = pm.xirr(pm.build_cashflows(transactions, snap.totals.market_value_eur, today))
    for aid, pos in open_positions.items():
        val = valuations[aid]
        if val.market_value_eur is None:
            continue
        snap.holdings.append(Holding(aid, pos.name or aid, pos.category, targets.bucket_of(pos.category, aid), val.market_value_eur))
        if pos.quantity > 0:
            snap.prices_eur[aid] = val.market_value_eur / pos.quantity
    return snap
