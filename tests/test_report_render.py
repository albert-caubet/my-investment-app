"""Renderer, sparklines, hypothesis register and the portfolio snapshot."""

import json
from datetime import date
from pathlib import Path

import pytest

from invest.portfolio.rebalance import load_targets
from invest.portfolio.snapshot import value_portfolio
from invest.report import hypotheses as H
from invest.report.render import markdown_to_html, md_table, sparkline_svg

ROOT = Path(__file__).resolve().parents[1]


def test_sparkline_is_inline_svg_with_a_baseline():
    svg = sparkline_svg([1, 2, 3, 2, 4], baseline=2.5)
    assert svg.startswith("<svg") and "polyline" in svg and "stroke-dasharray" in svg
    assert sparkline_svg([1.0]) == ""
    assert sparkline_svg([float("nan"), 1.0, 2.0]).count(",") >= 2


def test_markdown_to_html_is_self_contained_with_tables():
    md = "# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n" + sparkline_svg([1, 2, 3])
    html = markdown_to_html(md, title="Weekly report")
    assert html.startswith("<!DOCTYPE html>") and "<table>" in html and "<svg" in html
    assert "<title>Weekly report</title>" in html and "<style>" in html


def test_md_table_escapes_and_formats():
    text = md_table([["a|b", 1234.5678, None, 2.0e7]], ["x", "y", "z", "w"])
    assert "a\\|b" in text and "1,234.57" in text and "–" in text and "20,000,000" in text
    assert md_table([], ["x"]) == "_none_\n"


def test_shipped_hypotheses_parse_and_due_dates_work():
    items = H.load_hypotheses()
    assert len(items) == 11
    assert items[0].id == "H1" and items[0].status == "open"
    assert items[0].next_review == date(2026, 12, 1)
    assert "SP500_CAPE" in items[0].indicators
    due = H.due(items, date(2026, 12, 1))
    assert {h.id for h in due} >= {"H1", "H2", "H4", "H5", "H6", "H11"}
    assert not any(h.id == "H3" for h in due)  # due 2027-03-01
    assert H.due(items, date(2026, 9, 10)) == []


def test_hypotheses_parser_rejects_duplicates_and_reads_fields():
    text = "## H1. A\n- **Status:** closed\n- **Next review:** bad-date\n## H2. B\n- **Status:** open\n"
    items = H.parse_hypotheses(text)
    assert items[0].status == "closed" and items[0].next_review is None
    assert H.due(items, date(2030, 1, 1)) == [items[1]]  # no date means always due when open
    with pytest.raises(ValueError):
        H.parse_hypotheses("## H1. A\n## H1. B\n")


def test_value_portfolio_with_injected_prices():
    docs = json.loads((ROOT / "tests" / "fixtures" / "transactions_sample.json").read_text("utf-8"))
    targets = load_targets()
    spot = {"ES0000000001": 12.0, "ES0000000002": 9.0, "USST": 14.04, "IE0000000003": 30.0, "EXIT": 31.0,
            "LU0000000004": 55.0, "FEES": 110.0}
    currencies = {s: ("USD" if s == "USST" else "EUR") for s in spot}
    snap = value_portfolio(docs, targets, source="fixture", today=date(2026, 9, 10), spot=spot,
                           fx={"EUR": 1.0, "USD": 1.17}, currencies=currencies)
    assert snap.totals.market_value_eur == pytest.approx(11724.5313, abs=1e-3)  # the golden portfolio
    assert snap.unvalued == []
    assert sum(snap.weights.values()) == pytest.approx(100.0)
    buckets = {h.bucket for h in snap.holdings}
    assert buckets <= {"equity", "bonds", "cash", "crypto", "other"}
    assert snap.prices_eur["USST"] == pytest.approx(14.04 / 1.17)
    payload = snap.as_dict()
    assert payload["market_value_eur"] == pytest.approx(11724.5313, abs=1e-3)


def test_value_portfolio_reports_unvalued_positions():
    docs = json.loads((ROOT / "tests" / "fixtures" / "transactions_sample.json").read_text("utf-8"))
    snap = value_portfolio(docs, load_targets(), source="fixture", today=date(2026, 9, 10), spot={},
                           fx={"EUR": 1.0}, currencies={})
    assert snap.unvalued and snap.holdings == []
    assert len(snap.unvalued) == 6  # every open position; the closed one needs no price
    assert snap.totals.n_unvalued >= len(snap.unvalued)
