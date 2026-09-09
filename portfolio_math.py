"""Pure portfolio arithmetic.

Deliberately free of Streamlit, Firestore, yfinance and any network access, so that
every euro figure the dashboard shows can be unit-tested in isolation.

Two conventions are stated once here and never re-derived elsewhere:

1. ``rates[X]`` is *units of X per 1 EUR* -- the Yahoo ``EURX=X`` quote. So
   ``rates["USD"] == 1.17`` means 1 EUR buys 1.17 USD, and a USD amount is
   converted to EUR by *dividing* by it.
2. A position is built by replaying its transactions in chronological order.
   Average cost with sells is order-dependent, so it cannot be expressed as a
   groupby aggregate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

BASE_CCY = "EUR"

# The dashboard can only value assets whose listing currency it can convert.
SUPPORTED_CCY = frozenset({"EUR", "USD"})

# Quantity tolerance is *relative* to lifetime volume, so it behaves for both
# 0.5 BTC and 12,000 fund units.
QTY_REL_EPS = 1e-9
MONEY_EPS = 0.01  # one cent

# Below this many observations a CAPM estimate is not worth showing.
MIN_CAPM_OBS = 60
TRADING_DAYS = 252

# Annualising a few weeks of return produces enormous meaningless figures -- the
# same failure mode as a 252x annualised alpha off six observations.
MIN_XIRR_DAYS = 90
DAYS_PER_YEAR = 365.0


class MissingRate(LookupError):
    """Raised instead of silently defaulting an FX rate to 1.0.

    A silent 1.0 is how a USD cost basis quietly ends up ~15% too low.
    """


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------

def to_base(amount: float, ccy: str | None, rates: Mapping[str, float]) -> float:
    """Convert `amount`, expressed in `ccy`, into EUR. See module docstring."""
    code = (ccy or "").upper()
    if not code:
        raise MissingRate("no currency given")
    if code == BASE_CCY:
        return float(amount)
    rate = rates.get(code)
    if rate is None or not math.isfinite(rate) or rate <= 0:
        raise MissingRate(f"no usable {BASE_CCY}{code} rate")
    return float(amount) / float(rate)


def from_base(amount_eur: float, ccy: str | None, rates: Mapping[str, float]) -> float:
    """Inverse of :func:`to_base` -- EUR into `ccy`."""
    code = (ccy or "").upper()
    if code == BASE_CCY:
        return float(amount_eur)
    rate = rates.get(code)
    if rate is None or not math.isfinite(rate) or rate <= 0:
        raise MissingRate(f"no usable {BASE_CCY}{code} rate")
    return float(amount_eur) * float(rate)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Transaction:
    """One logged trade, already normalised out of its Firestore shape.

    `fx_rate` is units of `currency` per 1 EUR *on the trade date*, and is always
    1.0 when `currency` is EUR. Cost basis uses this historical rate; only market
    value uses a live one.
    """

    asset_id: str
    trade_date: date
    action: str
    quantity: float
    price_nominal: float
    currency: str
    fx_rate: float
    fees: float = 0.0
    seq: int = 0
    doc_id: str | None = None
    # Display metadata, carried through so the UI does not need a second pass.
    category: str | None = None
    name: str | None = None
    ticker: str | None = None
    isin: str | None = None
    #: Yahoo symbol an ISIN resolved to, captured once at entry time so the pricing
    #: path never has to run a live ISIN search.
    resolved_ticker: str | None = None
    #: Currency the asset was quoted in when the trade was logged. A fallback for
    #: valuation when the live metadata lookup fails -- never a reason to assume EUR.
    listing_ccy: str | None = None

    @property
    def is_buy(self) -> bool:
        return self.action == "Buy"

    @property
    def gross_nominal(self) -> float:
        return self.quantity * self.price_nominal

    @property
    def gross_eur(self) -> float:
        return self.gross_nominal / self.fx_rate

    @property
    def fees_eur(self) -> float:
        return self.fees / self.fx_rate

    @property
    def net_eur(self) -> float:
        """Cash that actually moved, in EUR.

        Cost including fees on a buy, proceeds net of fees on a sell. The one
        figure a stored ``cost_eur`` has to agree with.
        """
        if self.is_buy:
            return self.gross_eur + self.fees_eur
        return self.gross_eur - self.fees_eur


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

@dataclass
class PositionState:
    """Running state of one holding after replaying its transactions."""

    asset_id: str
    quantity: float = 0.0
    cost_basis_eur: float = 0.0
    #: Cost basis released by sells (and by the closing sweep). Open basis plus
    #: released basis is every euro of cost ever deployed in this asset, and
    #: released basis is the denominator realised P&L was earned on.
    cost_released_eur: float = 0.0
    #: Same basis in the transaction currency. ``None`` once an asset has been
    #: traded in more than one currency, where a nominal average is meaningless.
    cost_basis_nominal: float | None = 0.0
    nominal_ccy: str | None = None
    realised_pnl_eur: float = 0.0
    fees_eur_total: float = 0.0
    proceeds_eur_total: float = 0.0
    qty_bought_lifetime: float = 0.0
    qty_sold_lifetime: float = 0.0
    first_trade_date: date | None = None
    last_trade_date: date | None = None
    n_transactions: int = 0
    category: str | None = None
    name: str | None = None
    ticker: str | None = None
    isin: str | None = None
    resolved_ticker: str | None = None
    listing_ccy: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def price_symbol(self) -> str:
        """What to send to the price provider, as opposed to the grouping identity."""
        return self.resolved_ticker or self.ticker or self.asset_id

    @property
    def is_open(self) -> bool:
        return self.quantity > 0.0

    @property
    def avg_cost_eur(self) -> float:
        """EUR cost per share still held, fees included."""
        return self.cost_basis_eur / self.quantity if self.quantity > 0 else 0.0

    @property
    def avg_cost_nominal(self) -> float | None:
        if self.cost_basis_nominal is None or self.quantity <= 0:
            return None
        return self.cost_basis_nominal / self.quantity


def _qty_eps(pos: PositionState) -> float:
    return max(1e-9, QTY_REL_EPS * pos.qty_bought_lifetime)


def _sweep_residue(pos: PositionState) -> None:
    """Snap a float-dust quantity to zero and move any stranded basis to realised.

    Without this, selling everything can leave ~1e-15 shares carrying the full
    cost basis, which renders as a single enormous negative-P&L row.
    """
    if abs(pos.quantity) > _qty_eps(pos):
        return
    pos.quantity = 0.0
    if abs(pos.cost_basis_eur) > MONEY_EPS:
        pos.warnings.append(
            f"swept residual cost basis of EUR {pos.cost_basis_eur:.2f} into realised P&L "
            f"when the position closed"
        )
    # Keeps the identity total == unrealised + realised exactly true.
    pos.realised_pnl_eur -= pos.cost_basis_eur
    pos.cost_released_eur += pos.cost_basis_eur
    pos.cost_basis_eur = 0.0
    if pos.cost_basis_nominal is not None:
        pos.cost_basis_nominal = 0.0


def _apply(pos: PositionState, tx: Transaction) -> None:
    pos.n_transactions += 1
    if pos.first_trade_date is None or tx.trade_date < pos.first_trade_date:
        pos.first_trade_date = tx.trade_date
    if pos.last_trade_date is None or tx.trade_date >= pos.last_trade_date:
        pos.last_trade_date = tx.trade_date
        # Latest non-empty metadata wins, matching the old agg("first") on a
        # date-descending frame.
        pos.category = tx.category or pos.category
        pos.name = tx.name or pos.name
        pos.ticker = tx.ticker or pos.ticker
        pos.isin = tx.isin or pos.isin
        pos.resolved_ticker = tx.resolved_ticker or pos.resolved_ticker
        pos.listing_ccy = tx.listing_ccy or pos.listing_ccy

    if pos.nominal_ccy is None and pos.n_transactions == 1:
        pos.nominal_ccy = tx.currency
    elif pos.nominal_ccy != tx.currency:
        pos.nominal_ccy = None
        pos.cost_basis_nominal = None

    fees_eur = tx.fees_eur
    pos.fees_eur_total += fees_eur

    if tx.is_buy:
        pos.cost_basis_eur += tx.gross_eur + fees_eur
        if pos.cost_basis_nominal is not None:
            pos.cost_basis_nominal += tx.gross_nominal + tx.fees
        pos.quantity += tx.quantity
        pos.qty_bought_lifetime += tx.quantity
        _sweep_residue(pos)
        return

    # --- sell -------------------------------------------------------------
    pos.qty_sold_lifetime += tx.quantity
    proceeds_eur = tx.gross_eur - fees_eur
    pos.proceeds_eur_total += proceeds_eur

    if pos.quantity <= _qty_eps(pos):
        pos.warnings.append(
            f"sell of {tx.quantity:g} on {tx.trade_date} with no open position; "
            f"booked as pure gain against zero basis"
        )
        pos.realised_pnl_eur += proceeds_eur
        pos.quantity = 0.0
        return

    sell_qty = min(tx.quantity, pos.quantity)
    if tx.quantity > sell_qty + _qty_eps(pos):
        pos.warnings.append(
            f"oversell on {tx.trade_date}: {tx.quantity:g} requested but only "
            f"{pos.quantity:g} held"
        )

    # Release a *fraction of the running total* rather than avg_cost * qty, so a
    # full exit lands on exactly 0.0 with no rounding drift.
    frac = sell_qty / pos.quantity
    released_eur = pos.cost_basis_eur * frac
    pos.realised_pnl_eur += proceeds_eur - released_eur
    pos.cost_basis_eur -= released_eur
    pos.cost_released_eur += released_eur
    if pos.cost_basis_nominal is not None:
        pos.cost_basis_nominal *= 1.0 - frac
    pos.quantity -= sell_qty
    _sweep_residue(pos)


def build_position(txs: Sequence[Transaction]) -> PositionState:
    """Replay one asset's transactions chronologically. Never raises."""
    if not txs:
        raise ValueError("build_position requires at least one transaction")
    pos = PositionState(asset_id=txs[0].asset_id)
    for tx in sorted(txs, key=lambda t: (t.trade_date, t.seq)):
        _apply(pos, tx)
    return pos


def build_positions(txs: Iterable[Transaction]) -> dict[str, PositionState]:
    grouped: dict[str, list[Transaction]] = {}
    for tx in txs:
        grouped.setdefault(tx.asset_id, []).append(tx)
    return {aid: build_position(rows) for aid, rows in grouped.items()}


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Quote:
    """A live price, in whatever currency the asset is *listed* in."""

    asset_id: str
    price: float | None
    listing_ccy: str | None
    as_of: date | None = None


@dataclass
class Valuation:
    asset_id: str
    quantity: float
    cost_basis_eur: float
    realised_pnl_eur: float
    cost_released_eur: float = 0.0
    market_value_eur: float | None = None
    unrealised_pnl_eur: float | None = None
    unrealised_pnl_pct: float | None = None
    total_pnl_eur: float | None = None
    price: float | None = None
    listing_ccy: str | None = None
    error: str | None = None


def value_position(
    pos: PositionState,
    quote: Quote | None,
    rates: Mapping[str, float],
) -> Valuation:
    """Mark a position to market. Never raises -- failures land in ``.error``.

    Conversion keys off the *listing* currency, because that is the currency the
    quoted price is denominated in. The transaction currency says what left your
    bank account and is already baked into ``cost_basis_eur``; the two are
    different things and only coincide by accident.

    An unknown listing currency refuses to value rather than assuming EUR.
    """
    val = Valuation(
        asset_id=pos.asset_id,
        quantity=pos.quantity,
        cost_basis_eur=pos.cost_basis_eur,
        realised_pnl_eur=pos.realised_pnl_eur,
        cost_released_eur=pos.cost_released_eur,
        total_pnl_eur=pos.realised_pnl_eur,
    )

    if quote is None or quote.price is None or not math.isfinite(quote.price):
        val.error = "no price available"
        return val

    val.price = quote.price
    val.listing_ccy = quote.listing_ccy
    code = (quote.listing_ccy or "").upper()
    if not code:
        # Unknown is not EUR. Valuing a USD price one-for-one as euros is the same
        # silent error as an FX fallback of 1.0, only in the other direction.
        val.error = "listing currency unknown"
        return val
    if code not in SUPPORTED_CCY:
        val.error = f"unsupported listing currency {quote.listing_ccy!r}"
        return val

    try:
        val.market_value_eur = to_base(pos.quantity * quote.price, code, rates)
    except MissingRate as exc:
        val.error = str(exc)
        return val

    val.unrealised_pnl_eur = val.market_value_eur - pos.cost_basis_eur
    if pos.cost_basis_eur > MONEY_EPS:
        val.unrealised_pnl_pct = val.unrealised_pnl_eur / pos.cost_basis_eur * 100.0
    val.total_pnl_eur = val.unrealised_pnl_eur + pos.realised_pnl_eur
    return val


@dataclass
class PortfolioTotals:
    market_value_eur: float = 0.0
    #: Open cost basis of the positions that could be valued.
    cost_basis_eur: float = 0.0
    #: Cost basis released by sells across every position, valued or not.
    cost_released_eur: float = 0.0
    unrealised_pnl_eur: float = 0.0
    realised_pnl_eur: float = 0.0
    total_pnl_eur: float = 0.0
    #: Each percentage is against the cost it was earned on: unrealised on the open
    #: basis, realised on the released basis, total on the two together. Total is
    #: therefore the cost-weighted blend of the other two, and carries no time.
    unrealised_pnl_pct: float | None = None
    realised_pnl_pct: float | None = None
    pnl_pct: float | None = None
    n_valued: int = 0
    n_unvalued: int = 0
    unvalued_ids: list[str] = field(default_factory=list)

    @property
    def invested_eur(self) -> float:
        """Every euro of cost ever deployed: still open, or since sold."""
        return self.cost_basis_eur + self.cost_released_eur


def portfolio_totals(vals: Sequence[Valuation]) -> PortfolioTotals:
    """Aggregate. Positions that failed to value are counted, never silently zeroed.

    Realised P&L and released basis are summed over every position, because money
    already banked is real whether or not today's price is available. Open basis
    and unrealised P&L are summed over valued positions only, so a position with a
    cost and no value against it cannot drag the totals.
    """
    tot = PortfolioTotals()
    for v in vals:
        tot.realised_pnl_eur += v.realised_pnl_eur
        tot.cost_released_eur += v.cost_released_eur
        if v.market_value_eur is None:
            tot.n_unvalued += 1
            tot.unvalued_ids.append(v.asset_id)
            continue
        tot.n_valued += 1
        tot.market_value_eur += v.market_value_eur
        tot.cost_basis_eur += v.cost_basis_eur
        tot.unrealised_pnl_eur += v.unrealised_pnl_eur or 0.0
    tot.total_pnl_eur = tot.unrealised_pnl_eur + tot.realised_pnl_eur
    if tot.cost_basis_eur > MONEY_EPS:
        tot.unrealised_pnl_pct = tot.unrealised_pnl_eur / tot.cost_basis_eur * 100.0
    if tot.cost_released_eur > MONEY_EPS:
        tot.realised_pnl_pct = tot.realised_pnl_eur / tot.cost_released_eur * 100.0
    # Against every euro of cost ever deployed. Dividing by the open basis alone
    # measured gains on capital already withdrawn against capital still at work,
    # which overstated the ratio by however much had been sold.
    if tot.invested_eur > MONEY_EPS:
        tot.pnl_pct = tot.total_pnl_eur / tot.invested_eur * 100.0
    return tot


def weights(vals: Sequence[Valuation]) -> dict[str, float]:
    """Percent of total market value per asset. Empty when nothing could be valued."""
    total = sum(v.market_value_eur for v in vals if v.market_value_eur is not None)
    if total <= 0:
        return {}
    return {
        v.asset_id: v.market_value_eur / total * 100.0
        for v in vals
        if v.market_value_eur is not None
    }


# ---------------------------------------------------------------------------
# Firestore document loading
# ---------------------------------------------------------------------------

def _coerce_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _coerce_float(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def transaction_from_doc(doc: Mapping) -> tuple[Transaction | None, list[str]]:
    """Turn one Firestore document into a :class:`Transaction`.

    Deliberately ignores the stored ``cost_eur`` and recomputes it from
    quantity x price / fx. That single choice makes the read path immune to a
    denormalised field drifting out of sync with the values it was derived from,
    and removes any dependence on which schema version wrote the document.
    """
    problems: list[str] = []

    ticker = (doc.get("ticker") or "").strip() or None
    isin = (doc.get("isin") or "").strip() or None
    # Identity stays ISIN-first, matching the existing grouping so historical
    # positions do not split when this loader is introduced.
    asset_id = isin or ticker
    if not asset_id:
        return None, ["no ticker and no ISIN"]

    trade_date = _coerce_date(doc.get("date"))
    if trade_date is None:
        return None, [f"unparseable date {doc.get('date')!r}"]

    action = doc.get("action")
    if action not in ("Buy", "Sell"):
        problems.append(f"unknown action {action!r}, treated as Sell")
        action = "Sell"

    quantity = _coerce_float(doc.get("quantity"))
    if quantity is None or quantity <= 0:
        return None, [f"unusable quantity {doc.get('quantity')!r}"]

    # price_nominal is current; `price` is the pre-69753eb field name.
    price = _coerce_float(doc.get("price_nominal"))
    if price is None:
        price = _coerce_float(doc.get("price"))
        if price is not None:
            problems.append("legacy `price` field used; no `price_nominal`")
    if price is None:
        return None, ["no usable price_nominal or price"]

    currency = (doc.get("currency") or BASE_CCY).upper()
    if "currency" not in doc:
        problems.append(f"no currency field; assumed {BASE_CCY}")

    if currency == BASE_CCY:
        # The writer calls get_historical_fx(date, "EUR", "USD") unconditionally,
        # so EUR documents carry a stored EURUSD rate that was never applied to
        # cost_eur. Honouring it here would understate every EUR cost basis.
        fx_rate = 1.0
    else:
        fx_rate = _coerce_float(doc.get("fx_rate"))
        if fx_rate is None:
            fx_rate = _coerce_float(doc.get("fx_rate_at_buy"))
        if fx_rate is None or fx_rate <= 0:
            return None, [f"no usable FX rate for a {currency} transaction"]
        if fx_rate == 1.0:
            problems.append(
                f"FX rate is exactly 1.0 on a {currency} transaction -- almost "
                f"certainly the silent fallback, so this cost basis is too low"
            )

    fees = _coerce_float(doc.get("fees")) or 0.0

    # Firestore SERVER_TIMESTAMP gives a stable intra-day ordering.
    stamp = doc.get("timestamp")
    seq = int(stamp.timestamp()) if isinstance(stamp, datetime) else 0

    tx = Transaction(
        asset_id=asset_id,
        trade_date=trade_date,
        action=action,
        quantity=quantity,
        price_nominal=price,
        currency=currency,
        fx_rate=fx_rate,
        fees=fees,
        seq=seq,
        doc_id=doc.get("_doc_id"),
        category=(doc.get("category") or None),
        name=(doc.get("name") or None),
        ticker=ticker,
        isin=isin,
        resolved_ticker=(doc.get("resolved_ticker") or "").strip() or None,
        listing_ccy=(doc.get("listing_ccy") or "").strip().upper() or None,
    )
    return tx, problems


def load_transactions(docs: Iterable[Mapping]) -> tuple[list[Transaction], list[str]]:
    """Bulk loader. Returns usable transactions plus human-readable problems."""
    txs: list[Transaction] = []
    problems: list[str] = []
    for doc in docs:
        tx, issues = transaction_from_doc(doc)
        label = doc.get("_doc_id") or doc.get("isin") or doc.get("ticker") or "?"
        for issue in issues:
            problems.append(f"{label}: {issue}")
        if tx is None:
            problems.append(f"{label}: skipped")
        else:
            txs.append(tx)
    return txs, problems


def audit_transaction(doc: Mapping) -> list[str]:
    """Red flags for a stored document, including denormalised-field drift."""
    notes: list[str] = []
    tx, problems = transaction_from_doc(doc)
    notes.extend(problems)
    if tx is None:
        return notes

    stored = _coerce_float(doc.get("cost_eur"))
    if stored is not None:
        # The writer stores the cash that moved, fees included, so that is what a
        # consistent document must reproduce. Comparing against gross alone flagged
        # every fee-bearing trade, which teaches the reader to ignore the column.
        expected = tx.net_eur
        formula = (
            "(quantity x price + fees) / fx" if tx.is_buy else "(quantity x price - fees) / fx"
        )
        if abs(stored - expected) > max(MONEY_EPS, abs(expected) * 1e-6):
            notes.append(
                f"stored cost_eur {stored:.2f} disagrees with {formula} "
                f"= {expected:.2f} (difference {stored - expected:+.2f})"
            )
    if doc.get("fees") is None and "fees" not in doc:
        notes.append("no fees recorded")
    return notes


# ---------------------------------------------------------------------------
# CAPM
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapmResult:
    beta: float | None = None
    alpha_annual: float | None = None
    r_squared: float | None = None
    n_obs: int = 0
    credible: bool = False
    note: str = ""


def estimate_capm(
    asset_returns,
    market_returns,
    *,
    rf_annual: float,
    min_obs: int = MIN_CAPM_OBS,
    periods_per_year: int = TRADING_DAYS,
) -> CapmResult:
    """Jensen's alpha and beta from aligned return arrays.

    Both moments use ddof=1. Mixing numpy's defaults -- ``np.cov`` normalises by
    n-1, ``np.var`` by n -- inflates beta by exactly n/(n-1).
    """
    import numpy as np

    a = np.asarray(asset_returns, dtype=float)
    m = np.asarray(market_returns, dtype=float)
    if a.shape != m.shape:
        return CapmResult(note="asset and market series are not aligned")

    ok = np.isfinite(a) & np.isfinite(m)
    a, m = a[ok], m[ok]
    n = int(a.size)
    if n < min_obs:
        return CapmResult(n_obs=n, note=f"only {n} observations (minimum {min_obs})")

    rf_period = rf_annual / periods_per_year
    a_ex, m_ex = a - rf_period, m - rf_period

    # A constant series does not give exactly zero variance in floating point --
    # it gives denormal dust, and dividing by that yields a garbage beta. Real
    # daily-return volatility is ~1e-2, so anything near 1e-12 is degenerate.
    market_sd = float(np.std(m_ex, ddof=1))
    if not math.isfinite(market_sd) or market_sd < 1e-12:
        return CapmResult(n_obs=n, note="benchmark has no variance over this window")

    beta = float(np.cov(a_ex, m_ex, ddof=1)[0, 1] / market_sd**2)
    alpha = (a.mean() * periods_per_year - rf_annual) - beta * (
        m.mean() * periods_per_year - rf_annual
    )

    asset_sd = float(np.std(a_ex, ddof=1))
    r_squared = (
        float(np.corrcoef(a_ex, m_ex)[0, 1] ** 2) if asset_sd > 1e-12 else 0.0
    )

    return CapmResult(
        beta=beta,
        alpha_annual=float(alpha),
        r_squared=r_squared,
        n_obs=n,
        credible=True,
        note=f"{n} observations",
    )


# ---------------------------------------------------------------------------
# Money-weighted return (XIRR)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class XirrResult:
    rate: float | None = None
    n_flows: int = 0
    days: int = 0
    credible: bool = False
    note: str = ""


def build_cashflows(
    txs: Sequence[Transaction],
    terminal_value_eur: float,
    as_of: date,
) -> list[tuple[date, float]]:
    """Transactions as signed EUR cash flows, closed with today's portfolio value.

    Buys are money leaving (negative), sells money arriving (positive), each at the
    FX rate that applied on its own trade date. Fees are part of the flow because
    they genuinely left the account.
    """
    flows = [(tx.trade_date, -tx.net_eur if tx.is_buy else tx.net_eur) for tx in txs]
    flows.append((as_of, float(terminal_value_eur)))
    return sorted(flows)


def xirr(
    cashflows: Sequence[tuple[date, float]],
    *,
    min_days: int = MIN_XIRR_DAYS,
    max_iter: int = 200,
) -> XirrResult:
    """Annualised money-weighted return: the IRR of dated cash flows.

    Answers "what rate did *my money* earn", which is a different question from
    ``(value - cost) / cost``: that ratio contains no time at all, so it treats a
    euro invested last month the same as one invested two years ago.

    Solved by bisection -- no scipy needed. Returns a result with ``credible=False``
    rather than a number whenever the answer would not mean anything.
    """
    flows = sorted((d, float(cf)) for d, cf in cashflows if cf)
    if len(flows) < 2:
        return XirrResult(n_flows=len(flows), note="need at least two non-zero cash flows")

    if not (any(cf < 0 for _, cf in flows) and any(cf > 0 for _, cf in flows)):
        return XirrResult(
            n_flows=len(flows), note="need both an outflow and an inflow"
        )

    start = flows[0][0]
    span = (flows[-1][0] - start).days
    if span < min_days:
        return XirrResult(
            n_flows=len(flows),
            days=span,
            note=f"only {span} days of history (minimum {min_days})",
        )

    def npv(rate: float) -> float:
        return sum(
            cf / (1.0 + rate) ** ((d - start).days / DAYS_PER_YEAR) for d, cf in flows
        )

    # A rate at or below -100% is meaningless, so the lower bound sits just above
    # it -- far enough down that even a near-total loss still brackets. The upper
    # bound is deliberately generous so genuinely large returns bracket too.
    low, high = -0.999999, 10.0
    npv_low, npv_high = npv(low), npv(high)
    if npv_low * npv_high > 0:
        # No sign change over the bracket. When flows change sign more than once --
        # buy, exit completely, buy again -- the IRR may be non-unique or may not
        # exist at all. Reporting nothing beats reporting a plausible fiction.
        return XirrResult(
            n_flows=len(flows),
            days=span,
            note="no unique solution for these cash flows",
        )

    for _ in range(max_iter):
        mid = (low + high) / 2.0
        npv_mid = npv(mid)
        if npv_low * npv_mid <= 0:
            high = mid
        else:
            low, npv_low = mid, npv_mid

    return XirrResult(
        rate=(low + high) / 2.0,
        n_flows=len(flows),
        days=span,
        credible=True,
        note=f"{len(flows)} cash flows over {span} days",
    )


# ---------------------------------------------------------------------------
# Inflation adjustment
# ---------------------------------------------------------------------------

def real_rate(nominal_rate: float, inflation_rate: float) -> float:
    """Strip inflation out of a nominal rate (Fisher relation).

    Subtracting inflation is only an approximation; it drifts as either rate
    grows, and a rate is exactly the kind of figure where that matters.
    """
    return (1.0 + nominal_rate) / (1.0 + inflation_rate) - 1.0


def month_of(day: date) -> str:
    """The ``YYYY-MM`` key a price index is published against."""
    return f"{day.year:04d}-{day.month:02d}"


def to_real_terms(
    txs: Sequence[Transaction],
    to_month: str,
    index: Mapping[str, float],
) -> tuple[list[Transaction], list[str]]:
    """Restate transactions into the purchasing power of ``to_month``.

    Scales price and fees by the ratio of index levels, so replaying the result
    through :func:`build_position` yields a cost basis in constant euros -- with
    sells and averaging handled by exactly the same tested code path.

    Transactions in months the index does not cover are passed through unchanged
    and named in the returned list. A partial adjustment must never be presented
    as a complete one: the uncovered months are precisely where unmeasured
    inflation would flatter the result.
    """
    target = index.get(to_month)
    if not target:
        return list(txs), [f"no index value for {to_month}"]

    adjusted: list[Transaction] = []
    uncovered: list[str] = []
    for tx in txs:
        month = month_of(tx.trade_date)
        source = index.get(month)
        if not source:
            adjusted.append(tx)
            uncovered.append(month)
            continue
        factor = target / source
        adjusted.append(
            replace(tx, price_nominal=tx.price_nominal * factor, fees=tx.fees * factor)
        )
    return adjusted, sorted(set(uncovered))


def inflation_between(from_month: str, to_month: str, index: Mapping[str, float]) -> float | None:
    """Cumulative inflation between two months, as a decimal. None if uncovered."""
    start, end = index.get(from_month), index.get(to_month)
    if not start or not end:
        return None
    return end / start - 1.0
