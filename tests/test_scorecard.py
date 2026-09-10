"""Scorecard glue on a seeded temporary store: fallbacks, snapshots, changes, freshness."""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from invest.data.cache import Store
from invest.macro import scorecard as sc
from invest.macro.catalog import parse_catalog

CATALOG = parse_catalog(
    {
        "series": [
            {"id": "US_SAHM", "label": "Sahm", "group": "labor", "source": "fred", "key": "SAHMREALTIME",
             "frequency": "M", "lag_days": 7, "direction": "high_bad", "units": "pp", "critical": True},
            {"id": "VIX", "label": "VIX", "group": "internals", "source": "fred", "key": "VIXCLS",
             "frequency": "D", "lag_days": 1, "direction": "high_bad", "fallback": "VIX_YAHOO"},
            {"id": "VIX_YAHOO", "label": "VIX (Yahoo)", "group": "internals", "source": "yahoo", "key": "^VIX",
             "frequency": "D", "lag_days": 1, "direction": "high_bad", "show": False},
            {"id": "US_ISM_MFG_PMI", "label": "ISM", "group": "surveys", "source": "release", "frequency": "M",
             "direction": "low_bad"},
        ]
    }
)
TODAY = date(2026, 9, 10)


def monthly(values, start):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"), dtype=float)


def daily(values, start):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "s.duckdb") as s:
        rng = np.random.default_rng(1)
        # 151 months ending 2026-08-01, the newest a monthly series can be on 2026-09-10
        s.upsert_series("US_SAHM", monthly(list(rng.normal(0.1, 0.1, 150)) + [0.6], "2014-02-01"), source="fred")
        # the FRED VIX stopped two months ago; the Yahoo copy is current
        old_days = pd.bdate_range("2024-08-01", "2026-07-10")
        new_days = pd.bdate_range("2024-08-01", "2026-09-09")
        s.upsert_series("VIX", pd.Series(rng.normal(18, 3, len(old_days)), index=old_days), source="fred")
        s.upsert_series("VIX_YAHOO", pd.Series(rng.normal(18, 3, len(new_days)), index=new_days), source="yahoo")
        yield s


def test_readings_cover_shown_series_and_use_fallbacks(store):
    readings = sc.build_readings(store, CATALOG, today=TODAY)
    by_id = {r.id: r for r in readings}
    assert set(by_id) == {"US_SAHM", "VIX", "US_ISM_MFG_PMI"}  # VIX_YAHOO is hidden
    assert by_id["US_SAHM"].value == pytest.approx(0.6)
    assert by_id["US_SAHM"].concern is not None and by_id["US_SAHM"].concern > 2
    assert by_id["VIX"].note == "via fallback VIX_YAHOO"
    assert not by_id["VIX"].stale
    assert by_id["US_ISM_MFG_PMI"].value is None and by_id["US_ISM_MFG_PMI"].note == "no data"


def test_fallback_is_not_used_when_primary_is_fresh(store):
    fresh = daily([20.0] * 30, "2026-08-01")
    store.upsert_series("VIX", fresh, source="fred")
    readings = {r.id: r for r in sc.build_readings(store, CATALOG, today=TODAY)}
    assert readings["VIX"].note == ""


def test_regime_and_snapshot_round_trip(store):
    reg, results = sc.build_regime(store, CATALOG)
    assert reg.label in ("expansion", "late cycle", "stress", "recovery")
    fired = {r.rule.name for r in results if r.fired}
    assert "sahm" in fired
    readings = sc.build_readings(store, CATALOG, today=TODAY)
    payload = sc.build_snapshot(readings, reg, results)
    run = store.start_run("test")
    store.save_snapshot(run, "scorecard", payload)
    loaded = store.load_snapshot("scorecard")[2]
    assert loaded["regime"]["label"] == reg.label
    assert {r["id"] for r in loaded["readings"]} == {r.id for r in readings}


def test_changes_since_reports_new_observations_and_rule_flips(store):
    readings = sc.build_readings(store, CATALOG, today=TODAY)
    reg, results = sc.build_regime(store, CATALOG)
    previous = sc.build_snapshot(readings, reg, results)
    # rewrite history: last month the Sahm indicator was quiet and the rule was not firing
    for r in previous["readings"]:
        if r["id"] == "US_SAHM":
            r["obs_date"], r["value"], r["concern"] = "2026-05-01", 0.1, 0.0
    for r in previous["rules"]:
        if r["name"] == "sahm":
            r["fired"] = False
    changes = sc.changes_since(readings, results, previous)
    kinds = {(c.id, c.kind) for c in changes}
    assert ("US_SAHM", "new observation") in kinds
    assert ("US_SAHM", "concern crossed") in kinds
    assert ("sahm", "rule fired") in kinds
    assert sc.changes_since(readings, results, None) == []


def test_previous_snapshot_prefers_a_week_old_baseline(store):
    run = store.start_run("t")
    store.save_snapshot(run, "scorecard", {"n": 1})
    store.con.execute("UPDATE snapshots SET created_at = ? WHERE payload = ?", [datetime(2026, 8, 20), '{"n": 1}'])
    store.save_snapshot(run, "scorecard", {"n": 2})
    store.con.execute("UPDATE snapshots SET created_at = ? WHERE payload = ?", [datetime(2026, 9, 9), '{"n": 2}'])
    store.save_snapshot(run, "scorecard", {"n": 3})
    store.con.execute("UPDATE snapshots SET created_at = ? WHERE payload = ?", [datetime(2026, 9, 10), '{"n": 3}'])
    assert sc.previous_snapshot(store, "scorecard")[2] == {"n": 1}  # at least six days before the latest
    assert sc.previous_snapshot(store, "missing") is None


def test_previous_snapshot_falls_back_to_the_oldest_when_all_are_recent(store):
    run = store.start_run("t")
    store.save_snapshot(run, "scorecard", {"n": 1})
    assert sc.previous_snapshot(store, "scorecard") is None  # only one
    store.save_snapshot(run, "scorecard", {"n": 2})
    assert sc.previous_snapshot(store, "scorecard")[2] == {"n": 1}


def test_freshness_table_recomputes_age_and_marks_manual(store):
    table = sc.freshness_table(store, CATALOG, today=TODAY).set_index("id")
    assert table.loc["US_SAHM", "status"] == "ok"
    assert table.loc["VIX", "status"] == "stale"
    assert table.loc["US_ISM_MFG_PMI", "status"] == "manual"
    assert table.loc["US_SAHM", "age_days"] == (TODAY - date(2026, 8, 1)).days
    assert table.loc["US_SAHM", "critical"]
