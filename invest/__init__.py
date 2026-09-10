"""Analysis package: macro regime, value screener, rebalancing report.

Companion to the portfolio app in the repository root. The principles are the
same ones that made the portfolio engine trustworthy, restated in PLAN.md:

- pure core, I/O at the edges: every indicator, metric and rule is a function
  over data frames, tested against hand-computed fixtures; fetching lives in
  ``invest.data``;
- every number carries its date, source and fetch time;
- no silent defaults: a missing series is reported missing, never filled in.
"""
