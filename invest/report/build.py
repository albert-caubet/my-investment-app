"""Assemble the weekly report as Markdown (PLAN.md section 7.3).

Every section is built inside a guard: a section that cannot be produced is
replaced by a line naming what is missing, and the reason lands in the
"Missing and failed" section. No figure appears without its date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

import pandas as pd

from invest.macro.catalog import GROUPS, Catalog
from invest.macro.indicators import IndicatorReading, format_value
from invest.macro.regimes import Regime, RuleResult
from invest.macro.scorecard import Change
from invest.portfolio.rebalance import Plan, Targets
from invest.portfolio.snapshot import PortfolioSnapshot
from invest.positioning.insiders import InsiderSummary
from invest.report.hypotheses import Hypothesis
from invest.report.render import md_table, sparkline_svg

TOP_N = 10

DEFINITIONS = [
    ("Regime label", "Summarises which transparent rules fire in how many groups; stress needs three groups, late cycle two. It predicts nothing."),
    ("z-score", "(latest minus mean) / standard deviation over the trailing window, ten years unless the catalog says otherwise."),
    ("Percentile", "Mid-rank of the latest value within the whole history of the series."),
    ("Concern", "The z-score signed in the direction of concern; positive is worrying."),
    ("Sahm rule", "The 3-month average unemployment rate rises 0.5 points or more above its low of the previous 12 months."),
    ("Net liquidity", "Fed balance sheet minus the Treasury General Account minus reverse repo."),
    ("Policy stance", "Real Fed funds (Fisher, against core PCE) minus the Holston-Laubach-Williams r*; positive is restrictive."),
    ("Excess bond premium", "The corporate spread left after removing the part explained by expected defaults."),
    ("CAPE", "Real price over the 10-year average of real earnings. Excess CAPE yield: 1 / CAPE minus the real 10-year yield."),
    ("Buffett indicator", "Nonfinancial corporate equities over nominal GDP, read against its own log-linear trend."),
    ("Earnings yield / ROIC", "EBIT / EV and EBIT / (net working capital + net fixed assets), Greenblatt's two measures."),
    ("Margin of safety", "1 minus price / intrinsic value, against the lower of the earnings power value and a conservative DCF."),
    ("Drift", "Actual bucket weight minus target; a trade is proposed beyond the absolute or relative band, whichever binds first."),
    ("Money-weighted return", "The IRR of the dated cash flows closed with today's value."),
    ("13F", "Quarterly long US positions of large managers, filed up to 45 days after quarter end. No shorts, cash or foreign listings."),
]


@dataclass
class ReportContext:
    run_date: date
    run_id: str = ""
    catalog: Catalog | None = None
    readings: list[IndicatorReading] = field(default_factory=list)
    regime: Regime | None = None
    results: list[RuleResult] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    previous_at: datetime | None = None
    freshness: pd.DataFrame | None = None
    sparklines: dict[str, list[float]] = field(default_factory=dict)
    portfolio: PortfolioSnapshot | None = None
    targets: Targets | None = None
    plan: Plan | None = None
    sale_estimates: list[dict] = field(default_factory=list)
    screener: dict | None = None
    screen_top: dict[str, pd.DataFrame] = field(default_factory=dict)
    entrants: dict[str, tuple[list[str], list[str]]] = field(default_factory=dict)
    excluded: pd.DataFrame | None = None
    watchlist: pd.DataFrame | None = None
    holdings_summaries: list[str] = field(default_factory=list)
    insider_summaries: list[InsiderSummary] = field(default_factory=list)
    hypotheses_due: list[Hypothesis] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _section(ctx: ReportContext, title: str, fn: Callable[[ReportContext], str]) -> str:
    try:
        body = fn(ctx)
    except Exception as exc:  # the report must come out; the failure is named
        ctx.failures[title] = f"{type(exc).__name__}: {exc}"
        body = f'<div class="missing">Section not produced: {type(exc).__name__}: {exc}</div>\n'
    return f"## {title}\n\n{body}\n"


def _eur(value) -> str:
    return f"€{value:,.0f}" if value is not None else "–"


def _pct(value, digits: int = 1) -> str:
    return f"{value:.{digits}f}%" if value is not None and pd.notna(value) else "–"


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def header(ctx: ReportContext) -> str:
    lines = []
    p = ctx.portfolio
    if p is not None and p.totals is not None:
        mwr = f"{p.mwr.rate * 100:.2f}% a year" if p.mwr and p.mwr.credible else f"not computed ({p.mwr.note if p.mwr else 'no cash flows'})"
        lines.append(f"Portfolio value {_eur(p.totals.market_value_eur)} on {p.as_of.isoformat()} "
                     f"(source: {p.source}); total P&L {_eur(p.totals.total_pnl_eur)} ({_pct(p.totals.pnl_pct)} of cost deployed); "
                     f"money-weighted return {mwr}.")
        if p.unvalued:
            lines.append(f"Not valued and excluded from the total: {', '.join(p.unvalued)}.")
    else:
        lines.append("Portfolio: not available this run (see Missing and failed).")
    if ctx.regime is not None:
        lines.append(f"Macro regime: **{ctx.regime.label}** as of {ctx.regime.as_of}. {len(ctx.regime.firing)} counted rule(s) firing.")
    if ctx.freshness is not None and not ctx.freshness.empty:
        counts = ctx.freshness["status"].value_counts().to_dict()
        lines.append("Data freshness: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ".")
        bad = ctx.freshness[ctx.freshness["status"].isin(["stale", "failed", "missing"]) & ctx.freshness["critical"]]
        if not bad.empty:
            lines.append("Critical series stale or failed: " + ", ".join(bad["id"]) + ".")
    return "\n\n".join(lines) + "\n"


def portfolio_section(ctx: ReportContext) -> str:
    p, plan = ctx.portfolio, ctx.plan
    if p is None or p.totals is None:
        raise RuntimeError("no portfolio valuation")
    out = []
    if plan is None:
        raise RuntimeError("no rebalancing plan")
    rows = [(d.bucket, _eur(d.value_eur), _pct(d.weight), _pct(d.target, 0), f"{d.drift:+.1f}",
             f"{d.lower:.1f}% to {d.upper:.1f}%", "yes" if d.out_of_band else "") for d in plan.drifts]
    out.append("**Weights against targets**\n\n" + md_table(rows, ["Bucket", "Value", "Weight", "Target", "Drift (pts)", "Band", "Out of band"]))
    if plan.trades:
        rows = [(t.action, t.bucket, _eur(t.amount_eur), t.reason, "; ".join(t.candidates[:3]) or "–",
                 "possible" if t.traspaso_possible else "") for t in plan.trades]
        out.append("**Proposed trades** (bucket level; pick the holding)\n\n" +
                   md_table(rows, ["Action", "Bucket", "Amount", "Why", "Candidates (funds first)", "Traspaso"]))
    else:
        out.append("**Proposed trades:** none. " + " ".join(n for n in plan.notes if "band" in n))
    if ctx.sale_estimates:
        rows = [(e["holding"], f"{e['quantity']:,.2f}", _eur(e["proceeds_eur"]), _eur(e["gain_eur"]), e.get("note", ""))
                for e in ctx.sale_estimates]
        out.append("**Taxable gain of the proposed sales, FIFO lots** (Spain matches sales against the oldest purchases; "
                   "the app measures performance with average cost, so the two splits differ while the total is the same)\n\n"
                   + md_table(rows, ["Holding", "Units", "Proceeds", "FIFO gain", "Note"]))
    if plan.contribution_eur > 0:
        rows = [(b, _eur(v)) for b, v in plan.contribution_allocation.items()]
        out.append(f"**Next contribution ({_eur(plan.contribution_eur)})** goes to the most underweight buckets first:\n\n"
                   + md_table(rows, ["Bucket", "Amount"]))
    else:
        from invest.portfolio.rebalance import allocate_contribution, bucket_values

        illustrative = allocate_contribution(bucket_values(p.holdings), ctx.targets, 1000.0) if ctx.targets else {}
        if illustrative:
            out.append("**Where new cash would go** (illustrative 1,000 EUR; no contribution is configured): "
                       + ", ".join(f"{b} {_eur(v)}" for b, v in illustrative.items()) + ".")
    notes = [n for n in plan.notes if "band" not in n or "traspaso" in n]
    if notes:
        out.append("Notes: " + " ".join(notes))
    if p.problems:
        out.append(f"Data quality notes on transactions: {len(p.problems)} (see the app).")
    return "\n\n".join(out) + "\n"


def macro_section(ctx: ReportContext) -> str:
    if ctx.regime is None or not ctx.readings:
        raise RuntimeError("no scorecard")
    out = [f"**Regime: {ctx.regime.label}** (as of {ctx.regime.as_of}). {ctx.regime.explanation}"]
    fired = [r for r in ctx.results if r.fired]
    if fired:
        rows = [(r.rule.description, r.rule.group, "counted" if r.rule.counts else "informational", r.as_of, r.detail) for r in fired]
        out.append("**Rules firing**\n\n" + md_table(rows, ["Rule", "Group", "Weight", "As of", "Detail"]))
    missing = [r.rule.name for r in ctx.results if r.fired is None]
    if missing:
        out.append("Not evaluated for lack of data: " + ", ".join(missing) + ".")
    if ctx.changes:
        rows = [(c.label, c.kind, c.before, c.after, c.detail) for c in ctx.changes]
        when = f" since {ctx.previous_at:%Y-%m-%d}" if isinstance(ctx.previous_at, datetime) else ""
        out.append(f"**Changed{when}**\n\n" + md_table(rows, ["What", "Change", "Before", "After", "Detail"]))
    else:
        out.append("No threshold crossed and no rule flipped since the previous run." if ctx.previous_at else
                   "No earlier snapshot to compare with; changes appear from the next run.")
    groups = ctx.catalog.groups() if ctx.catalog else sorted({r.group for r in ctx.readings})
    for group in groups:
        rows = []
        for r in ctx.readings:
            if r.group != group or r.value is None:
                continue
            spark = sparkline_svg(ctx.sparklines.get(r.id, []))
            rows.append((r.label + (" *" if r.stale else ""), r.value_text, r.obs_date.isoformat() if r.obs_date else "–",
                         f"{r.z:+.1f}" if r.z is not None else "–", f"{r.percentile:.0f}" if r.percentile is not None else "–",
                         f"{r.concern:+.1f}" if r.concern is not None else "–", spark))
        if rows:
            out.append(f"**{GROUPS.get(group, group)}**\n\n" + md_table(rows, ["Indicator", "Value", "As of", "z", "Pct", "Concern", "2y"]))
    out.append("An asterisk marks a series older than its publication schedule allows. Sparklines show the last two years of the transformed series.")
    return "\n\n".join(out) + "\n"


def positioning_section(ctx: ReportContext) -> str:
    out = []
    rows = []
    for r in ctx.readings:
        if r.group == "positioning" and r.value is not None:
            rows.append((r.label, r.value_text, r.obs_date.isoformat() if r.obs_date else "–",
                         f"{r.z:+.1f}" if r.z is not None else "–"))
    if rows:
        out.append("**Positioning and sentiment readings**\n\n" + md_table(rows, ["Indicator", "Value", "As of", "z"]))
    else:
        out.append("No positioning series has data yet (COT arrives with the refresh; AAII, NAAIM and flows are hand-entered).")
    if ctx.holdings_summaries:
        out.append("**What the tracked managers did** (13F: long US positions only, up to 45 days late)\n\n" +
                   "\n\n".join(ctx.holdings_summaries))
    else:
        out.append("No 13F holdings stored; run `python -m invest.jobs.filings`.")
    if ctx.insider_summaries:
        rows = [(s.issuer_cik, s.n_buys, s.distinct_buyers, s.n_sells, "yes" if s.cluster_buying else "", s.window_start, s.window_end)
                for s in ctx.insider_summaries]
        out.append("**Insider open-market activity, watchlist companies**\n\n" +
                   md_table(rows, ["Issuer CIK", "Buys", "Buyers", "Sells", "Cluster", "From", "To"]) +
                   "\nClusters of buying are a known modest positive; selling says little.")
    return "\n\n".join(out) + "\n"


def opportunities_section(ctx: ReportContext) -> str:
    if not ctx.screener:
        raise RuntimeError("no screener table stored; run python -m invest.jobs.fundamentals")
    out = [f"Universe {ctx.screener.get('universe_label')} ({ctx.screener.get('n_companies')} companies), table built "
           f"{ctx.screener.get('built', 'at the last fundamentals run')}"
           + (f", as of {ctx.screener['as_of']}" if ctx.screener.get("as_of") else ", latest filings")
           + ". Current index members only: survivorship bias."]
    for name, frame in ctx.screen_top.items():
        cols = [c for c in ("rank", "ticker", "name", "earnings_yield", "roic", "fcf_yield", "f_score", "pb", "mos_conservative", "period_end") if c in frame.columns]
        rows = []
        for _, r in frame.head(TOP_N).iterrows():
            rows.append(tuple(
                (f"{r[c]:.1%}" if c in ("earnings_yield", "roic", "fcf_yield") and pd.notna(r[c]) else
                 f"{r[c]:+.0%}" if c == "mos_conservative" and pd.notna(r[c]) else
                 f"{r[c]:.1f}" if c == "pb" and pd.notna(r[c]) else
                 f"{r[c]:.0f}" if c in ("rank", "f_score") and pd.notna(r[c]) else
                 (r[c] if pd.notna(r[c]) else "–"))
                for c in cols))
        labels = {"rank": "#", "ticker": "Ticker", "name": "Name", "earnings_yield": "EBIT/EV", "roic": "ROIC", "fcf_yield": "FCF yield",
                  "f_score": "F", "pb": "P/B", "mos_conservative": "Margin of safety", "period_end": "Data to"}
        block = f"**{name}**\n\n" + md_table(rows, [labels[c] for c in cols])
        entrants, dropouts = ctx.entrants.get(name, ([], []))
        if entrants or dropouts:
            block += f"\nTop {TOP_N} since the previous table: entered {', '.join(entrants) or 'none'}; left {', '.join(dropouts) or 'none'}.\n"
        out.append(block)
    if ctx.excluded is not None and not ctx.excluded.empty:
        out.append(f"Excluded as possible value traps: " + ", ".join(f"{r.ticker} ({r.reason})" for r in ctx.excluded.head(12).itertuples()) + ".")
    if ctx.watchlist is not None and not ctx.watchlist.empty:
        rows = [(r.get("symbol"), r.get("price"), r.get("value_conservative"),
                 f"{r['mos_conservative']:+.0%}" if pd.notna(r.get("mos_conservative")) else "–", r.get("note"))
                for _, r in ctx.watchlist.iterrows()]
        out.append("**Watchlist** (price against the conservative value)\n\n" + md_table(rows, ["Symbol", "Price", "Value", "Margin of safety", "Note"]))
    return "\n\n".join(out) + "\n"


def hypotheses_section(ctx: ReportContext) -> str:
    if not ctx.hypotheses_due:
        return "No hypothesis is due for review this week.\n"
    by_id = {r.id: r for r in ctx.readings}
    out = []
    for h in ctx.hypotheses_due:
        readings = []
        for sid in h.indicators:
            r = by_id.get(sid)
            if r is not None and r.value is not None:
                readings.append(f"{r.label}: {r.value_text} ({r.obs_date})")
            else:
                readings.append(f"{sid}: no data")
        out.append(f"**{h.id}. {h.title}** (due {h.next_review or 'unscheduled'}; {h.fields.get('Outcome', 'no outcome recorded')})\n\n"
                   f"Measurement: {h.fields.get('Measurement', '–')}\n\nCurrent readings: " + "; ".join(readings) + ".")
    return "\n\n".join(out) + "\n"


def missing_section(ctx: ReportContext) -> str:
    out = []
    if ctx.failures:
        rows = [(k, v) for k, v in ctx.failures.items()]
        out.append(md_table(rows, ["What", "Why it is missing"]))
    if ctx.freshness is not None and not ctx.freshness.empty:
        bad = ctx.freshness[ctx.freshness["status"].isin(["stale", "failed", "missing"])]
        if not bad.empty:
            rows = [(r.id, r.status, r.last_obs, r.age_days, r.limit_days, "critical" if r.critical else "", r.message)
                    for r in bad.itertuples()]
            out.append("**Series stale, failed or missing** (their rows above carry the last value with its date, or are absent)\n\n"
                       + md_table(rows, ["Series", "Status", "Last obs", "Age (days)", "Limit", "", "Message"]))
    if ctx.notes:
        out.append("Notes: " + " ".join(ctx.notes))
    return ("\n\n".join(out) + "\n") if out else "Nothing failed and nothing is missing.\n"


def appendix(ctx: ReportContext) -> str:
    rows = [(term, text) for term, text in DEFINITIONS]
    out = ["**Definitions**\n\n" + md_table(rows, ["Term", "Meaning"])]
    if ctx.catalog is not None:
        rows = []
        for spec in ctx.catalog.scorecard():
            rows.append((spec.label, spec.source, spec.key or spec.formula or "", spec.frequency, spec.lag_days))
        out.append("**Sources**\n\n" + md_table(rows, ["Indicator", "Source", "Key", "Freq", "Lag (days)"]))
    return "\n\n".join(out) + "\n"


def build_markdown(ctx: ReportContext) -> str:
    parts = [f"# Weekly report, {ctx.run_date.isoformat()}\n",
             f"Run {ctx.run_id or '(none)'}. Every figure carries the date it refers to; nothing is filled in.\n",
             "[TOC]\n"]
    parts.append(_section(ctx, "Header", header))
    parts.append(_section(ctx, "Portfolio", portfolio_section))
    parts.append(_section(ctx, "Macro", macro_section))
    parts.append(_section(ctx, "Positioning", positioning_section))
    parts.append(_section(ctx, "Opportunities", opportunities_section))
    parts.append(_section(ctx, "Hypothesis register", hypotheses_section))
    parts.append(_section(ctx, "Missing and failed", missing_section))
    parts.append(_section(ctx, "Appendix", appendix))
    return "\n".join(parts)
