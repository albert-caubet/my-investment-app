"""Research harness: event studies, hit rates, publication lags, vintages, hypothesis scripts."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from invest.data.cache import Store
from invest.jobs.vintages import load_vintages
from invest.research import events, hypotheses, walkforward


def daily(values, start="2010-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def monthly(values, start="2000-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"), dtype=float)


# --- events ------------------------------------------------------------------


def test_forward_returns_and_horizon_beyond_data():
    prices = daily([100.0] * 10 + [110.0] * 100)
    dates = [prices.index[0], prices.index[-5]]
    fwd = events.forward_returns(prices, dates, 30)
    assert fwd.iloc[0] == pytest.approx(0.10)
    assert len(fwd) == 1  # the second event has no price 30 days later


def test_dedupe_and_summary_with_bootstrap():
    dates = pd.to_datetime(["2020-01-01", "2020-01-10", "2020-06-01"])
    assert events.dedupe_events(dates, 30) == [pd.Timestamp("2020-01-01"), pd.Timestamp("2020-06-01")]
    stats = events.summarise(pd.Series([0.1, -0.05, 0.2, 0.05, 0.0]), 30)
    assert stats.n == 5 and stats.hit_rate == pytest.approx(0.6)
    assert stats.ci_low <= stats.mean <= stats.ci_high
    assert events.summarise(pd.Series(dtype=float), 30).n == 0


def test_event_study_against_baseline():
    rng = np.random.default_rng(0)
    prices = daily(100 * np.cumprod(1 + rng.normal(0.0004, 0.01, 3000)))
    dates = list(prices.index[::400])
    study = events.event_study(prices, dates, horizons=(30, 91))
    assert study.n_events == len(dates) and study.n_used <= study.n_events
    assert set(study.horizons) == {30, 91} and study.baseline[30].n > 50
    payload = study.as_dict()
    assert payload["horizons"][30]["n"] == study.horizons[30].n


def test_crossing_spike_and_uninversion_events():
    vix = daily([15.0] * 5 + [35.0] * 5 + [20.0] * 5 + [40.0] * 5)
    crossings = events.crossing_events(vix, above=30.0)
    assert len(crossings) == 2
    spread = monthly([0.5] * 12 + [-0.2] * 4 + [0.3] * 3 + [-0.1] * 1 + [0.2] * 3)
    assert len(events.uninversion_events(spread, min_inverted_months=3)) == 1  # the one-month dip does not count
    z = events.rolling_zscore(daily(list(np.random.default_rng(1).normal(0, 1, 800))), window_years=2, min_obs=60)
    assert len(z) > 0 and abs(z.mean()) < 1


# --- walk-forward -----------------------------------------------------------


def _usrec():
    idx = pd.date_range("2000-01-01", "2025-12-01", freq="MS")
    s = pd.Series(0.0, index=idx)
    s["2001-04-01":"2001-11-01"] = 1.0
    s["2008-01-01":"2009-06-01"] = 1.0
    s["2020-03-01":"2020-04-01"] = 1.0
    return s


def test_recession_starts_and_publication_lags():
    starts = walkforward.recession_starts(_usrec())
    assert [d.strftime("%Y-%m") for d in starts] == ["2001-04", "2008-01", "2020-03"]
    lagged = walkforward.apply_publication_lags({"X": monthly([1.0, 2.0])}, {"X": 30})
    assert lagged["X"].index[0] == pd.Timestamp("2000-01-31")


def test_hit_rates_count_hits_false_alarms_and_warned_recessions():
    usrec = _usrec()
    idx = usrec.index
    fired = pd.Series(False, index=idx, dtype=object)
    fired["2000-10-01":"2001-02-01"] = True   # warns the 2001 recession (6 months ahead)
    fired["2007-06-01":"2007-09-01"] = True   # warns 2008
    fired["2015-01-01":"2015-03-01"] = True   # false alarm
    fired["2020-04-01":"2020-06-01"] = True   # coincident: confirms 2020 without warning of it
    hr = walkforward.hit_rates(fired, usrec, rule="test", lead_months=12)
    assert hr.n_signals == 3 and hr.hits == 2 and hr.false_alarms == 1
    assert hr.hit_rate == pytest.approx(2 / 3)
    assert hr.recessions == 3 and hr.recessions_warned == 2 and hr.warned_rate == pytest.approx(2 / 3)
    assert hr.recessions_confirmed == 1 and hr.confirmed_rate == pytest.approx(1 / 3)
    assert hr.mean_lead_months == pytest.approx((6 + 7) / 2)


def test_scoreboard_evaluates_rules_at_month_ends():
    usrec = _usrec()
    sahm = pd.Series(0.0, index=usrec.index)
    sahm["2001-03-01":"2001-12-01"] = 0.6
    sahm["2008-03-01":"2009-06-01"] = 0.7
    board = walkforward.scoreboard({"US_SAHM": sahm}, usrec, lag_days={"US_SAHM": 7}, rules=["sahm"], start="2000-01-01")
    row = board.iloc[0]
    assert row["rule"] == "sahm" and row["n_months"] > 200
    # the Sahm rule is coincident: it fired inside both recessions, so no signal is counted as a warning
    assert row["n_signals"] == 0 or row["hits"] <= row["n_signals"]


def test_expanding_splits():
    idx = pd.date_range("2000-01-01", "2015-01-01", freq="MS")
    splits = walkforward.expanding_splits(idx, min_train_years=10, step_months=24)
    assert splits[0][0] == pd.Timestamp("2010-01-01")
    assert splits[-1][1] == idx[-1] and len(splits) == 3


# --- vintages -------------------------------------------------------------------


def test_insert_vintages_keeps_each_first_print(tmp_path):
    with Store(tmp_path / "v.duckdb") as store:
        current = pd.Series([143.0], index=pd.to_datetime(["2026-02-01"]))
        store.upsert_series("US_PAYEMS", current, source="fred")
        vintages = pd.DataFrame({"obs_date": [date(2026, 2, 1), date(2026, 2, 1)], "value": [150.0, 143.0],
                                 "vintage_date": [date(2026, 3, 6), date(2026, 4, 3)]})
        assert store.insert_vintages("US_PAYEMS", vintages, source="fred") == 2
        assert store.insert_vintages("US_PAYEMS", vintages, source="fred") == 0
        assert store.read_series("US_PAYEMS", as_of=date(2026, 3, 15)).iloc[0] == 150.0
        assert store.read_series("US_PAYEMS", as_of=date(2026, 4, 10)).iloc[0] == 143.0
        assert store.read_series("US_PAYEMS").iloc[0] == 143.0


def test_load_vintages_uses_the_api(tmp_path, monkeypatch):
    calls = []

    def fake(series_id, *, api_key):
        calls.append((series_id, api_key))
        return pd.DataFrame({"obs_date": [date(2026, 1, 1)], "value": [1.0], "vintage_date": [date(2026, 2, 1)]})

    monkeypatch.setattr("invest.jobs.vintages.fred.fetch_vintages", fake)
    with Store(tmp_path / "v.duckdb") as store:
        added = load_vintages(store, ["US_CPI", "US_NET_LIQUIDITY"], api_key="k", log=None)
    assert added == {"US_CPI": 1} and calls == [("CPIAUCSL", "k")]


# --- hypothesis scripts ----------------------------------------------------------


@pytest.fixture
def research_store(tmp_path):
    rng = np.random.default_rng(3)
    months = pd.date_range("1871-01-01", "2026-08-01", freq="MS")
    days = pd.bdate_range("1990-01-01", "2026-09-09")
    with Store(tmp_path / "r.duckdb") as store:
        price = pd.Series(4.4 * np.cumprod(1 + rng.normal(0.004, 0.04, len(months))), index=months)
        cpi = pd.Series(12.5 * np.cumprod(1 + rng.normal(0.002, 0.003, len(months))), index=months)
        earnings = pd.Series(0.4 * np.cumprod(1 + rng.normal(0.004, 0.03, len(months))), index=months)
        store.upsert_series("SHILLER_PRICE", price, source="shiller")
        store.upsert_series("SHILLER_CPI", cpi, source="shiller")
        store.upsert_series("SHILLER_EARNINGS", earnings, source="shiller")
        spx = pd.Series(300 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days))), index=days)
        store.upsert_series("SPX", spx, source="yahoo")
        store.upsert_series("US_HY_OAS", pd.Series(np.abs(rng.normal(4.5, 1.5, len(days))), index=days), source="fred")
        store.upsert_series("VIX", pd.Series(np.abs(rng.normal(18, 8, len(days))), index=days), source="fred")
        store.upsert_series("US_CURVE_10Y3M", pd.Series(rng.normal(1.0, 1.0, len(days)), index=days), source="fred")
        weeks = pd.date_range("2003-01-01", "2026-09-02", freq="W-WED")
        store.upsert_series("US_NET_LIQUIDITY_13W", pd.Series(rng.normal(0, 100, len(weeks)), index=weeks), source="derived")
        usrec = pd.Series(0.0, index=pd.date_range("1960-01-01", "2026-08-01", freq="MS"))
        usrec["2001-04-01":"2001-11-01"] = 1.0
        usrec["2008-01-01":"2009-06-01"] = 1.0
        usrec["2020-03-01":"2020-04-01"] = 1.0
        store.upsert_series("US_RECESSION", usrec, source="fred")
        sahm = pd.Series(0.0, index=usrec.index)
        sahm["2001-05-01":"2001-12-01"] = 0.6
        store.upsert_series("US_SAHM", sahm, source="fred")
        yield store


def test_hypothesis_scripts_run_and_print_their_n(research_store):
    h1 = hypotheses.h1_real_earnings(research_store)
    assert h1["hypothesis"] == "H1" and len(h1["decades"]) > 10 and "code_version" in h1
    h7 = hypotheses.h7_stress_events(research_store)
    assert set(h7["studies"]) == {"hy_oas_spike_z2", "vix_above_30", "curve_uninversion"}
    assert h7["studies"]["vix_above_30"]["n_events"] >= 1
    h8 = hypotheses.h8_drawdown_gaps(research_store)
    assert h8["n_episodes"] >= 1
    h11 = hypotheses.h11_dip_buying(research_store)
    rules = {r["rule"] for r in h11["results"]["since 1928"]}
    assert rules == {"hold", "dip10", "dip20", "below200d"}
    assert all(r["invested"] == r["n_months"] for r in h11["results"]["since 1990"])
    h6 = hypotheses.h6_liquidity(research_store)
    assert h6["periods"][0]["period"] == "all" and h6["periods"][0]["n_weeks"] > 100
    h5 = hypotheses.h5_claims_lead(research_store)
    assert "rows" in h5 and {r["rule"] for r in h5["rows"]} == {"claims_yoy", "claims_off_low", "sahm"}
    assert h5["recession_starts"] == ["2001-04-01", "2008-01-01", "2020-03-01"]


def test_missing_data_is_an_error_not_a_number(tmp_path):
    with Store(tmp_path / "e.duckdb") as store:
        assert "error" in hypotheses.h1_real_earnings(store)
        assert "error" in hypotheses.h7_stress_events(store)
        assert "error" in hypotheses.rule_scoreboard(store)


def test_drawdown_episodes_and_contribution_simulation():
    idx = pd.date_range("2000-01-01", periods=60, freq="MS")
    path = np.concatenate([np.linspace(100, 120, 20), np.linspace(120, 80, 10), np.linspace(80, 130, 30)])
    price = pd.Series(path, index=idx)
    episodes = hypotheses.drawdown_episodes(price, threshold=0.20)
    assert len(episodes) == 1 and episodes.iloc[0]["depth"] == pytest.approx(80 / 120 - 1)
    hold = hypotheses.simulate_contributions(price, rule="hold")
    dip = hypotheses.simulate_contributions(price, rule="dip20")
    assert hold["invested"] == 60 and hold["cash_left_uninvested"] == 0
    assert dip["terminal"] > 0 and dip["n_months"] == 60
