"""Intrinsic value: earnings power value and a conservative DCF, inputs printed.

Both models return their inputs alongside the result so the screener row can
show them, and both refuse to produce a number when a needed input is missing.
Defaults are deliberately plain and stated: a 9% cost of capital, a 21% tax
rate unless the effective rate is sensible, growth capped at 5% and terminal
growth of 2%.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

DEFAULT_COST_OF_CAPITAL = 0.09
DEFAULT_TAX_RATE = 0.21
MAX_GROWTH = 0.05
TERMINAL_GROWTH = 0.02
EXPLICIT_YEARS = 5


def _ok(*values) -> bool:
    return all(v is not None and isinstance(v, (int, float)) and math.isfinite(v) for v in values)


@dataclass(frozen=True)
class ValueResult:
    model: str
    value_per_share: float | None
    equity_value: float | None
    inputs: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"model": self.model, "value_per_share": self.value_per_share, "equity_value": self.equity_value,
                "inputs": dict(self.inputs), "notes": list(self.notes)}


def effective_tax_rate(income_tax, pretax_income) -> float:
    """Effective rate when it is between 10% and 35%; the statutory default otherwise."""
    if _ok(income_tax, pretax_income) and pretax_income > 0:
        rate = float(income_tax) / float(pretax_income)
        if 0.10 <= rate <= 0.35:
            return rate
    return DEFAULT_TAX_RATE


def normalised_ebit(ebit_by_year: dict[int, float], *, years: int = 5) -> float | None:
    """Average operating income over the last ``years`` fiscal years (at least three)."""
    vals = [v for _, v in sorted(ebit_by_year.items())[-years:] if _ok(v)]
    if len(vals) < 3:
        return None
    return sum(vals) / len(vals)


def epv(
    *,
    ebit_normalised: float | None,
    tax_rate: float,
    cost_of_capital: float,
    net_debt: float | None,
    shares: float | None,
) -> ValueResult:
    """Earnings power value: normalised EBIT after tax, capitalised at the cost of capital,
    less net debt. Assumes no growth, which is the point: it is the value of the
    business as it is."""
    inputs = {"ebit_normalised": ebit_normalised, "tax_rate": tax_rate, "cost_of_capital": cost_of_capital,
              "net_debt": net_debt, "shares": shares}
    notes = []
    if not _ok(ebit_normalised):
        notes.append("no normalised EBIT")
    if not _ok(shares) or (shares or 0) <= 0:
        notes.append("no share count")
    if cost_of_capital <= 0:
        notes.append("cost of capital must be positive")
    if notes:
        return ValueResult("EPV", None, None, inputs, tuple(notes))
    ev_value = ebit_normalised * (1.0 - tax_rate) / cost_of_capital
    equity = ev_value - (net_debt if _ok(net_debt) else 0.0)
    if not _ok(net_debt):
        notes.append("net debt unknown, taken as zero")
    return ValueResult("EPV", equity / shares, equity, {**inputs, "enterprise_value": ev_value}, tuple(notes))


def conservative_growth(revenue_cagr: float | None) -> float:
    """Growth for the explicit period: revenue CAGR floored at 0 and capped at 5%."""
    if not _ok(revenue_cagr):
        return 0.0
    return min(max(float(revenue_cagr), 0.0), MAX_GROWTH)


def dcf(
    *,
    fcf_base: float | None,
    growth: float,
    discount_rate: float,
    terminal_growth: float,
    net_debt: float | None,
    shares: float | None,
    years: int = EXPLICIT_YEARS,
) -> ValueResult:
    """Five years of FCF growing at ``growth``, then a Gordon terminal value, discounted at ``discount_rate``."""
    inputs = {"fcf_base": fcf_base, "growth": growth, "discount_rate": discount_rate,
              "terminal_growth": terminal_growth, "years": years, "net_debt": net_debt, "shares": shares}
    notes = []
    if not _ok(fcf_base):
        notes.append("no free cash flow")
    elif fcf_base <= 0:
        notes.append("free cash flow is not positive; no DCF value")
    if not _ok(shares) or (shares or 0) <= 0:
        notes.append("no share count")
    if discount_rate <= terminal_growth:
        notes.append("discount rate must exceed terminal growth")
    if notes:
        return ValueResult("DCF", None, None, inputs, tuple(notes))
    pv_explicit = 0.0
    cash_flow = fcf_base
    for year in range(1, years + 1):
        cash_flow = cash_flow * (1.0 + growth)
        pv_explicit += cash_flow / (1.0 + discount_rate) ** year
    terminal = cash_flow * (1.0 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal = terminal / (1.0 + discount_rate) ** years
    ev_value = pv_explicit + pv_terminal
    equity = ev_value - (net_debt if _ok(net_debt) else 0.0)
    if not _ok(net_debt):
        notes.append("net debt unknown, taken as zero")
    return ValueResult(
        "DCF", equity / shares, equity,
        {**inputs, "pv_explicit": pv_explicit, "pv_terminal": pv_terminal, "enterprise_value": ev_value,
         "terminal_share": pv_terminal / ev_value if ev_value else None},
        tuple(notes),
    )


def margin_of_safety(price: float | None, value: float | None) -> float | None:
    """1 minus price / value. Positive means the price is below the estimate."""
    if not _ok(price, value) or value <= 0:
        return None
    return 1.0 - float(price) / float(value)


def value_summary(
    *,
    price: float | None,
    ebit_by_year: dict[int, float],
    income_tax: float | None,
    pretax_income: float | None,
    net_debt: float | None,
    shares: float | None,
    fcf: float | None,
    revenue_cagr: float | None,
    cost_of_capital: float = DEFAULT_COST_OF_CAPITAL,
) -> dict:
    """EPV and DCF side by side, with the margin of safety against each and the conservative one."""
    tax = effective_tax_rate(income_tax, pretax_income)
    epv_result = epv(ebit_normalised=normalised_ebit(ebit_by_year), tax_rate=tax, cost_of_capital=cost_of_capital,
                     net_debt=net_debt, shares=shares)
    dcf_result = dcf(fcf_base=fcf, growth=conservative_growth(revenue_cagr), discount_rate=cost_of_capital,
                     terminal_growth=TERMINAL_GROWTH, net_debt=net_debt, shares=shares)
    values = [r.value_per_share for r in (epv_result, dcf_result) if r.value_per_share is not None and r.value_per_share > 0]
    conservative = min(values) if values else None
    return {
        "epv": epv_result.as_dict(),
        "dcf": dcf_result.as_dict(),
        "mos_epv": margin_of_safety(price, epv_result.value_per_share),
        "mos_dcf": margin_of_safety(price, dcf_result.value_per_share),
        "value_conservative": conservative,
        "mos_conservative": margin_of_safety(price, conservative),
        "price": price,
    }
