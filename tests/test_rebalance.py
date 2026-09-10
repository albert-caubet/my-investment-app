"""Rebalancing: drift, bands, contribution first, trades to target or band edge."""

import pytest

from invest.portfolio import rebalance as rb

TARGETS = rb.parse_targets(
    {
        "targets": {"equity": 60, "bonds": 30, "cash": 10},
        "bands": {"absolute": 5, "relative": 25, "trade_to": "target", "min_trade_eur": 100},
        "contribution": {"amount_eur": 0},
        "tilts": {"enabled": False, "max_points": 5},
        "map": {"category": {"ETF": "equity", "Fund": "equity", "Bonds": "bonds", "Cash/Money Market": "cash"},
                "asset": {"XYZ": "bonds"}},
    }
)


def holdings(equity=6000.0, bonds=3000.0, cash=1000.0, fund_share=0.5):
    return [
        rb.Holding("E1", "World ETF", "ETF", "equity", equity * (1 - fund_share)),
        rb.Holding("F1", "World Fund", "Fund", "equity", equity * fund_share),
        rb.Holding("B1", "Bond Fund", "Fund", "bonds", bonds),
        rb.Holding("C1", "Money market", "Cash/Money Market", "cash", cash),
    ]


def test_shipped_targets_file_loads_and_sums_to_100():
    targets = rb.load_targets()
    assert sum(targets.weights.values()) == pytest.approx(100.0)
    assert targets.bucket_of("Stock", "ANY") == "equity"
    assert targets.bucket_of("Cash/Money Market", "X") == "cash"
    assert targets.bucket_of("Mystery", "X") == "other"


def test_parse_targets_validates():
    with pytest.raises(rb.TargetsError, match="sum"):
        rb.parse_targets({"targets": {"a": 50, "b": 40}})
    with pytest.raises(rb.TargetsError, match="trade_to"):
        rb.parse_targets({"targets": {"a": 100}, "bands": {"trade_to": "middle"}})
    with pytest.raises(rb.TargetsError):
        rb.parse_targets({})


def test_asset_override_beats_category():
    assert TARGETS.bucket_of("ETF", "XYZ") == "bonds"
    assert TARGETS.bucket_of("ETF", "OTHER") == "equity"


def test_drift_table_on_target_is_inside_band():
    rows = {r.bucket: r for r in rb.drift_table(rb.bucket_values(holdings()), TARGETS)}
    assert rows["equity"].weight == pytest.approx(60) and rows["equity"].drift == pytest.approx(0)
    assert not any(r.out_of_band for r in rows.values())
    # band is the tighter of 5 points and 25% of target: equity 5, bonds 5 (7.5 capped), cash 2.5
    assert rows["equity"].upper == pytest.approx(65) and rows["cash"].upper == pytest.approx(12.5)


def test_relative_band_binds_for_small_buckets():
    # cash at 13% of 10,000: 3 points over, inside the 5-point absolute band but outside 25% relative
    rows = {r.bucket: r for r in rb.drift_table({"equity": 5700, "bonds": 3000, "cash": 1300}, TARGETS)}
    assert rows["cash"].out_of_band
    assert rows["cash"].drift_rel == pytest.approx(30)


def test_unknown_bucket_gets_zero_target():
    rows = {r.bucket: r for r in rb.drift_table({"equity": 6000, "bonds": 3000, "cash": 500, "other": 500}, TARGETS)}
    assert rows["other"].target == 0 and rows["other"].drift_rel is None
    assert not rows["other"].out_of_band  # 5% held against a 0% target with a 5-point band: on the edge, not out
    assert rows["other"].upper == pytest.approx(5)
    more = {r.bucket: r for r in rb.drift_table({"equity": 6000, "bonds": 3000, "cash": 400, "other": 600}, TARGETS)}
    assert more["other"].out_of_band


def test_contribution_goes_to_the_most_underweight_first():
    values = {"equity": 5000, "bonds": 3000, "cash": 1000}  # 9,000 total; equity 55.6%
    alloc = rb.allocate_contribution(values, TARGETS, 1000)
    # after the contribution the total is 10,000: targets 6,000 / 3,000 / 1,000 -> all of it to equity
    assert alloc == {"equity": 1000.0}
    alloc2 = rb.allocate_contribution({"equity": 6000, "bonds": 3000, "cash": 1000}, TARGETS, 1000)
    # already on target: spread by weight
    assert alloc2 == {"equity": 600.0, "bonds": 300.0, "cash": 100.0}
    assert rb.allocate_contribution(values, TARGETS, 0) == {}


def test_plan_proposes_no_trade_when_inside_bands():
    plan = rb.rebalance(holdings(), TARGETS)
    assert plan.trades == []
    assert any("inside its band" in n for n in plan.notes)


def test_plan_trades_to_target_and_flags_traspaso():
    # equity ran up to 8,000 of 11,500 (69.6%, outside 65) while bonds fell to 21.7% (below 25):
    # sell equity to 60% and buy bonds to 30%; cash at 8.7% is inside its 7.5 to 12.5 band
    plan = rb.rebalance(holdings(equity=8000.0, bonds=2500.0, cash=1000.0), TARGETS)
    total = 11500.0
    by = {t.bucket: t for t in plan.trades}
    assert by["equity"].action == "sell"
    assert by["equity"].amount_eur == pytest.approx(8000 - 0.60 * total, abs=0.01)
    assert by["bonds"].action == "buy"
    assert by["bonds"].amount_eur == pytest.approx(0.30 * total - 2500, abs=0.01)
    assert "cash" not in by
    # candidates list funds first, and both legs can be funds
    assert by["equity"].candidates[0].startswith("World Fund")
    assert by["equity"].traspaso_possible and by["bonds"].traspaso_possible
    assert any("traspaso" in n for n in plan.notes)


def test_plan_trades_to_band_edge_when_configured():
    edge = rb.parse_targets({**{"targets": {"equity": 60, "bonds": 30, "cash": 10}},
                             "bands": {"absolute": 5, "relative": 25, "trade_to": "band_edge", "min_trade_eur": 0}})
    plan = rb.rebalance(holdings(equity=8000.0, bonds=3000.0, cash=1000.0), edge)
    total = 12000.0
    sell = next(t for t in plan.trades if t.bucket == "equity")
    # to the upper edge (65%), not to the target (60%)
    assert sell.amount_eur == pytest.approx(8000 - 0.65 * total, abs=0.01)
    assert "band edge" in sell.reason


def test_minimum_trade_size_drops_small_trades():
    tiny = rb.parse_targets({"targets": {"equity": 60, "bonds": 30, "cash": 10},
                             "bands": {"absolute": 1, "relative": 5, "min_trade_eur": 500}})
    # equity 6,300 of 10,300 is 61.2%, outside a 1-point band, but the trade back is 120 EUR
    plan = rb.rebalance(holdings(equity=6300.0, bonds=3000.0, cash=1000.0), tiny)
    assert plan.trades == []
    assert any("below the minimum" in n for n in plan.notes)


def test_new_cash_first_avoids_a_sale():
    # equity is overweight (63.6%); 1,500 of new cash goes mostly to bonds and cash and no sale is needed
    plan = rb.rebalance(holdings(equity=7000.0, bonds=3000.0, cash=1000.0), TARGETS, contribution_eur=1500.0)
    # after the contribution the total is 12,500: shortfalls are bonds 750, equity 500, cash 250
    assert plan.contribution_allocation == {"bonds": 750.0, "cash": 250.0, "equity": 500.0}
    assert plan.trades == []
    assert not any(t.action == "sell" for t in plan.trades)
    assert any("contribution allocated before any sale" in n for n in plan.notes)


def test_tilt_is_off_by_default_and_bounded_when_on():
    same, notes = rb.apply_tilt(TARGETS, {"equity": 10})
    assert same.weights == TARGETS.weights and notes == []
    enabled = rb.parse_targets({"targets": {"equity": 60, "bonds": 30, "cash": 10},
                                "tilts": {"enabled": True, "max_points": 5}})
    tilted, notes = rb.apply_tilt(enabled, {"equity": -10})
    assert tilted.weights["equity"] == pytest.approx(55 / 95 * 100)  # bounded to -5, then renormalised
    assert sum(tilted.weights.values()) == pytest.approx(100)
    assert "tilt equity -5.0" in notes[0]


def test_empty_portfolio_is_reported():
    plan = rb.rebalance([], TARGETS)
    assert plan.trades == [] and "no valued holdings" in plan.notes[0]
    assert plan.as_dict()["total_eur"] == 0.0
