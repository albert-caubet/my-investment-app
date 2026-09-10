"""Fundamental metrics, pure and hand-computable (PLAN.md section 6.2).

Every function takes plain numbers (or a small mapping of them) and returns a
number or ``None`` when an input is missing or the ratio is undefined. Nothing
here guesses a missing input; the caller reports it.

Conventions: monetary inputs in the filer's currency, ratios as plain numbers,
yields and margins as fractions (0.08 is 8%). Growth rates are fractions too.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence


def _ok(*values) -> bool:
    return all(v is not None and isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def safe_div(numerator, denominator) -> float | None:
    if not _ok(numerator, denominator) or denominator == 0:
        return None
    return float(numerator) / float(denominator)


# ---------------------------------------------------------------------------
# capital structure
# ---------------------------------------------------------------------------

def total_debt(debt_current, debt_noncurrent) -> float | None:
    parts = [v for v in (debt_current, debt_noncurrent) if _ok(v)]
    return float(sum(parts)) if parts else None


def net_debt(debt, cash, short_term_investments=None) -> float | None:
    if not _ok(debt, cash):
        return None
    liquid = float(cash) + (float(short_term_investments) if _ok(short_term_investments) else 0.0)
    return float(debt) - liquid


def enterprise_value(market_cap, debt, cash, short_term_investments=None) -> float | None:
    """Market cap plus debt minus cash (and liquid investments)."""
    if not _ok(market_cap):
        return None
    nd = net_debt(debt if _ok(debt) else 0.0, cash if _ok(cash) else 0.0, short_term_investments)
    return float(market_cap) + (nd or 0.0)


# ---------------------------------------------------------------------------
# Greenblatt
# ---------------------------------------------------------------------------

def earnings_yield(ebit, ev) -> float | None:
    """EBIT / EV: cheapness independent of capital structure."""
    if not _ok(ebit, ev) or ev <= 0:
        return None
    return float(ebit) / float(ev)


def net_working_capital(current_assets, current_liabilities, cash=None, short_term_investments=None, debt_current=None) -> float | None:
    """Operating working capital: current assets less cash and liquid investments,
    minus current liabilities less interest-bearing short-term debt. Floored at zero,
    as Greenblatt does, because negative working capital is free financing, not
    negative capital."""
    if not _ok(current_assets, current_liabilities):
        return None
    assets = float(current_assets) - (float(cash) if _ok(cash) else 0.0) - (
        float(short_term_investments) if _ok(short_term_investments) else 0.0)
    liabilities = float(current_liabilities) - (float(debt_current) if _ok(debt_current) else 0.0)
    return max(assets - liabilities, 0.0)


def roic(ebit, nwc, net_fixed_assets) -> float | None:
    """EBIT / (net working capital + net fixed assets)."""
    if not _ok(ebit, nwc, net_fixed_assets):
        return None
    capital = float(nwc) + float(net_fixed_assets)
    if capital <= 0:
        return None
    return float(ebit) / capital


# ---------------------------------------------------------------------------
# cash, margins, growth
# ---------------------------------------------------------------------------

def free_cash_flow(cfo, capex) -> float | None:
    """Operating cash flow minus capital expenditure (capex reported as a positive payment)."""
    if not _ok(cfo, capex):
        return None
    return float(cfo) - abs(float(capex))


def fcf_yield(fcf, market_cap) -> float | None:
    if not _ok(fcf, market_cap) or market_cap <= 0:
        return None
    return float(fcf) / float(market_cap)


def gross_margin(revenue, gross_profit=None, cost_of_revenue=None) -> float | None:
    if not _ok(revenue) or revenue == 0:
        return None
    if _ok(gross_profit):
        return float(gross_profit) / float(revenue)
    if _ok(cost_of_revenue):
        return (float(revenue) - float(cost_of_revenue)) / float(revenue)
    return None


def operating_margin(revenue, operating_income) -> float | None:
    return safe_div(operating_income, revenue) if _ok(revenue) and revenue != 0 else None


def slope_per_year(values: Mapping[int, float] | Sequence[tuple[int, float]]) -> float | None:
    """OLS slope of value on year, in units per year. Needs at least three points."""
    points = sorted(values.items()) if isinstance(values, Mapping) else sorted(values)
    points = [(x, y) for x, y in points if _ok(y)]
    if len(points) < 3:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var


def cagr(first, last, years: float) -> float | None:
    """Compound annual growth; undefined when either end is not positive."""
    if not _ok(first, last) or first <= 0 or last <= 0 or years <= 0:
        return None
    return (float(last) / float(first)) ** (1.0 / years) - 1.0


def stability(values: Sequence[float]) -> float | None:
    """Coefficient of variation (sd / |mean|) of a margin series; lower is steadier."""
    vals = [v for v in values if _ok(v)]
    if len(vals) < 3:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    return math.sqrt(var) / abs(mean)


# ---------------------------------------------------------------------------
# leverage and quality
# ---------------------------------------------------------------------------

def ebitda(ebit, depreciation) -> float | None:
    if not _ok(ebit):
        return None
    return float(ebit) + (float(depreciation) if _ok(depreciation) else 0.0)


def net_debt_to_ebitda(nd, ebitda_value) -> float | None:
    if not _ok(nd, ebitda_value) or ebitda_value <= 0:
        return None
    return float(nd) / float(ebitda_value)


def interest_coverage(ebit, interest_expense) -> float | None:
    """EBIT / interest expense; ``None`` when there is no interest expense to cover."""
    if not _ok(ebit, interest_expense) or interest_expense <= 0:
        return None
    return float(ebit) / float(interest_expense)


def altman_z(working_capital, retained_earnings, ebit, market_cap, total_liabilities, revenue, total_assets) -> float | None:
    """Altman's original 1968 Z for public manufacturers:
    1.2 A + 1.4 B + 3.3 C + 0.6 D + 1.0 E."""
    if not _ok(working_capital, retained_earnings, ebit, market_cap, total_liabilities, revenue, total_assets):
        return None
    if total_assets <= 0 or total_liabilities <= 0:
        return None
    a = working_capital / total_assets
    b = retained_earnings / total_assets
    c = ebit / total_assets
    d = market_cap / total_liabilities
    e = revenue / total_assets
    return 1.2 * a + 1.4 * b + 3.3 * c + 0.6 * d + 1.0 * e


def accruals_ratio(net_income, cfo, total_assets_now, total_assets_prev=None) -> float | None:
    """(net income minus operating cash flow) / average total assets. Positive is earnings not backed by cash."""
    if not _ok(net_income, cfo, total_assets_now):
        return None
    avg = (float(total_assets_now) + float(total_assets_prev)) / 2.0 if _ok(total_assets_prev) else float(total_assets_now)
    if avg <= 0:
        return None
    return (float(net_income) - float(cfo)) / avg


def share_count_change(shares_now, shares_prev) -> float | None:
    """Fractional change in shares outstanding; positive is dilution."""
    if not _ok(shares_now, shares_prev) or shares_prev <= 0:
        return None
    return float(shares_now) / float(shares_prev) - 1.0


def shareholder_yield(dividends, buybacks, market_cap) -> float | None:
    """(dividends + buybacks) / market cap, payments taken as positive amounts."""
    if not _ok(market_cap) or market_cap <= 0:
        return None
    paid = (abs(float(dividends)) if _ok(dividends) else 0.0) + (abs(float(buybacks)) if _ok(buybacks) else 0.0)
    return paid / float(market_cap)


# ---------------------------------------------------------------------------
# Piotroski F-score
# ---------------------------------------------------------------------------

F_CHECKS = (
    "roa_positive", "cfo_positive", "roa_improved", "cfo_exceeds_net_income",
    "leverage_fell", "current_ratio_rose", "no_dilution", "gross_margin_rose", "asset_turnover_rose",
)


def piotroski(curr: Mapping[str, float | None], prev: Mapping[str, float | None]) -> tuple[int | None, dict[str, bool | None]]:
    """The nine binary checks. Keys expected in both mappings: net_income, cfo,
    total_assets, debt_noncurrent (long-term debt), current_assets,
    current_liabilities, shares_outstanding, gross_margin, revenue.

    A check whose inputs are missing is ``None`` and counts as zero; the score
    is ``None`` only when fewer than six checks could be evaluated.
    """
    def g(m, k):
        v = m.get(k)
        return float(v) if _ok(v) else None

    ta, ta_p = g(curr, "total_assets"), g(prev, "total_assets")
    ni, ni_p = g(curr, "net_income"), g(prev, "net_income")
    cfo = g(curr, "cfo")
    roa = safe_div(ni, ta_p if ta_p else ta)
    roa_p = safe_div(ni_p, g(prev, "total_assets_prev") or ta_p)
    lev, lev_p = safe_div(g(curr, "debt_noncurrent"), ta), safe_div(g(prev, "debt_noncurrent"), ta_p)
    cr, cr_p = safe_div(g(curr, "current_assets"), g(curr, "current_liabilities")), safe_div(g(prev, "current_assets"), g(prev, "current_liabilities"))
    sh, sh_p = g(curr, "shares_outstanding"), g(prev, "shares_outstanding")
    gm, gm_p = g(curr, "gross_margin"), g(prev, "gross_margin")
    turn, turn_p = safe_div(g(curr, "revenue"), ta_p if ta_p else ta), safe_div(g(prev, "revenue"), g(prev, "total_assets_prev") or ta_p)

    def cmp(a, b, op):
        if a is None or b is None:
            return None
        return bool(op(a, b))

    checks = {
        "roa_positive": None if roa is None else roa > 0,
        "cfo_positive": None if cfo is None else cfo > 0,
        "roa_improved": cmp(roa, roa_p, lambda a, b: a > b),
        "cfo_exceeds_net_income": None if (cfo is None or ni is None) else cfo > ni,
        "leverage_fell": cmp(lev, lev_p, lambda a, b: a < b) if lev is not None and lev_p is not None else (True if lev == 0 and lev_p == 0 else None),
        "current_ratio_rose": cmp(cr, cr_p, lambda a, b: a > b),
        "no_dilution": cmp(sh, sh_p, lambda a, b: a <= b),
        "gross_margin_rose": cmp(gm, gm_p, lambda a, b: a > b),
        "asset_turnover_rose": cmp(turn, turn_p, lambda a, b: a > b),
    }
    evaluated = [v for v in checks.values() if v is not None]
    if len(evaluated) < 6:
        return None, checks
    return int(sum(1 for v in evaluated if v)), checks


# ---------------------------------------------------------------------------
# multiples
# ---------------------------------------------------------------------------

def price_to_earnings(market_cap, net_income) -> float | None:
    if not _ok(market_cap, net_income) or net_income <= 0:
        return None
    return float(market_cap) / float(net_income)


def price_to_book(market_cap, equity) -> float | None:
    if not _ok(market_cap, equity) or equity <= 0:
        return None
    return float(market_cap) / float(equity)


def ev_to_ebitda(ev, ebitda_value) -> float | None:
    if not _ok(ev, ebitda_value) or ebitda_value <= 0:
        return None
    return float(ev) / float(ebitda_value)


def ev_to_sales(ev, revenue) -> float | None:
    if not _ok(ev, revenue) or revenue <= 0:
        return None
    return float(ev) / float(revenue)


def price_to_fcf(market_cap, fcf) -> float | None:
    if not _ok(market_cap, fcf) or fcf <= 0:
        return None
    return float(market_cap) / float(fcf)


# ---------------------------------------------------------------------------
# value-trap filters
# ---------------------------------------------------------------------------

MAX_NET_DEBT_TO_EBITDA = 4.0
MIN_F_SCORE = 4
MAX_DILUTION_3Y = 0.10


def value_trap_flags(
    *,
    revenue_by_year: Mapping[int, float] | None,
    fcf_by_year: Mapping[int, float] | None,
    net_debt_ebitda: float | None,
    f_score: int | None,
    dilution_3y: float | None,
) -> list[str]:
    """Reasons to exclude a company before ranking. Empty list means none found."""
    flags: list[str] = []
    if revenue_by_year:
        years = sorted(revenue_by_year)[-4:]
        vals = [revenue_by_year[y] for y in years if _ok(revenue_by_year[y])]
        if len(vals) >= 4 and all(b < a for a, b in zip(vals, vals[1:])):
            flags.append("revenue fell in each of the last three years")
    if fcf_by_year:
        years = sorted(fcf_by_year)[-5:]
        negatives = sum(1 for y in years if _ok(fcf_by_year[y]) and fcf_by_year[y] < 0)
        if len(years) >= 5 and negatives >= 3:
            flags.append(f"negative free cash flow in {negatives} of the last 5 years")
    if net_debt_ebitda is not None and net_debt_ebitda > MAX_NET_DEBT_TO_EBITDA:
        flags.append(f"net debt / EBITDA {net_debt_ebitda:.1f} above {MAX_NET_DEBT_TO_EBITDA:.0f}")
    if f_score is not None and f_score < MIN_F_SCORE:
        flags.append(f"Piotroski F-score {f_score} at or below 3")
    if dilution_3y is not None and dilution_3y > MAX_DILUTION_3Y:
        flags.append(f"share count up {dilution_3y * 100:.0f}% over three years")
    return flags


# ---------------------------------------------------------------------------
# everything at once
# ---------------------------------------------------------------------------

@dataclass
class MetricSet:
    values: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __getitem__(self, key):
        return self.values.get(key)


def compute_all(
    latest: Mapping[str, float | None],
    previous: Mapping[str, float | None],
    history: Mapping[str, Mapping[int, float]],
    *,
    market_cap: float | None,
    annual_latest: Mapping[str, float | None] | None = None,
    annual_previous: Mapping[str, float | None] | None = None,
) -> MetricSet:
    """Every metric of section 6.2 from canonical fields.

    ``latest`` and ``previous`` are canonical fields for the latest period and
    the one a year earlier; ``history`` maps field name to ``{fiscal year: value}``
    for the fields that need a trend (revenue, gross_margin, operating_margin,
    net_income, fcf, shares_outstanding). The F-score compares like with like,
    so it uses ``annual_latest`` against ``annual_previous`` when given (two
    fiscal years) rather than a trailing-twelve-month figure against a year.
    """
    m = MetricSet()
    v = m.values
    debt = total_debt(latest.get("debt_current"), latest.get("debt_noncurrent"))
    cash = latest.get("cash")
    ebit = latest.get("operating_income")
    ev = enterprise_value(market_cap, debt, cash, latest.get("short_term_investments"))
    nd = net_debt(debt, cash, latest.get("short_term_investments")) if _ok(debt, cash) else None
    nwc = net_working_capital(latest.get("current_assets"), latest.get("current_liabilities"), cash,
                              latest.get("short_term_investments"), latest.get("debt_current"))
    fcf = free_cash_flow(latest.get("cfo"), latest.get("capex"))
    gm = gross_margin(latest.get("revenue"), latest.get("gross_profit"), latest.get("cost_of_revenue"))
    ebitda_value = ebitda(ebit, latest.get("depreciation"))

    v.update(
        market_cap=market_cap,
        enterprise_value=ev,
        total_debt=debt,
        net_debt=nd,
        ebit=ebit,
        ebitda=ebitda_value,
        earnings_yield=earnings_yield(ebit, ev),
        roic=roic(ebit, nwc, latest.get("ppe_net")),
        fcf=fcf,
        fcf_yield=fcf_yield(fcf, market_cap),
        gross_margin=gm,
        operating_margin=operating_margin(latest.get("revenue"), ebit),
        net_debt_to_ebitda=net_debt_to_ebitda(nd, ebitda_value),
        interest_coverage=interest_coverage(ebit, latest.get("interest_expense")),
        accruals=accruals_ratio(latest.get("net_income"), latest.get("cfo"), latest.get("total_assets"),
                                previous.get("total_assets")),
        shareholder_yield=shareholder_yield(latest.get("dividends"), latest.get("buybacks"), market_cap),
        pe=price_to_earnings(market_cap, latest.get("net_income")),
        pb=price_to_book(market_cap, latest.get("equity")),
        ev_ebitda=ev_to_ebitda(ev, ebitda_value),
        ev_sales=ev_to_sales(ev, latest.get("revenue")),
        p_fcf=price_to_fcf(market_cap, fcf),
    )
    working_capital = None
    if _ok(latest.get("current_assets"), latest.get("current_liabilities")):
        working_capital = float(latest["current_assets"]) - float(latest["current_liabilities"])
    v["altman_z"] = altman_z(working_capital, latest.get("retained_earnings"), ebit, market_cap,
                             latest.get("total_liabilities"), latest.get("revenue"), latest.get("total_assets"))

    gm_hist = history.get("gross_margin", {})
    om_hist = history.get("operating_margin", {})
    v["gross_margin_slope"] = slope_per_year({y: x for y, x in gm_hist.items() if _ok(x)}) if gm_hist else None
    v["operating_margin_slope"] = slope_per_year({y: x for y, x in om_hist.items() if _ok(x)}) if om_hist else None
    v["operating_margin_stability"] = stability(list(om_hist.values())) if om_hist else None

    def growth(field_name):
        series = {y: x for y, x in history.get(field_name, {}).items() if _ok(x)}
        years = sorted(series)
        if len(years) < 4:
            return None
        return cagr(series[years[-4]], series[years[-1]], years[-1] - years[-4])

    v["revenue_cagr_3y"] = growth("revenue")
    v["earnings_cagr_3y"] = growth("net_income")
    shares = {y: x for y, x in history.get("shares_outstanding", {}).items() if _ok(x)}
    years = sorted(shares)
    v["dilution_3y"] = share_count_change(shares[years[-1]], shares[years[-4]]) if len(years) >= 4 else None
    v["share_count_change_1y"] = share_count_change(latest.get("shares_outstanding"), previous.get("shares_outstanding"))

    f_latest = annual_latest if annual_latest is not None else latest
    f_previous = annual_previous if annual_previous is not None else previous
    latest_gm = gross_margin(f_latest.get("revenue"), f_latest.get("gross_profit"), f_latest.get("cost_of_revenue"))
    prev_gm = gross_margin(f_previous.get("revenue"), f_previous.get("gross_profit"), f_previous.get("cost_of_revenue"))
    score, checks = piotroski({**f_latest, "gross_margin": latest_gm}, {**f_previous, "gross_margin": prev_gm})
    v["f_score"] = score
    v["f_checks"] = checks
    v["value_trap_flags"] = value_trap_flags(
        revenue_by_year=history.get("revenue"),
        fcf_by_year=history.get("fcf"),
        net_debt_ebitda=v["net_debt_to_ebitda"],
        f_score=score,
        dilution_3y=v["dilution_3y"],
    )
    if market_cap is None:
        m.notes.append("no market cap: every price-based metric is missing")
    return m
