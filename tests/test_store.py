"""DuckDB store: append-only series with provenance, and the other tables."""

from datetime import date, datetime

import pandas as pd
import pytest

from invest.data.cache import Store, observations_frame


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "test.duckdb") as s:
        yield s


def _series(values: dict) -> pd.Series:
    return pd.Series(list(values.values()), index=pd.to_datetime(list(values.keys())))


def test_observations_frame_drops_blanks_and_orders():
    frame = observations_frame(
        pd.DataFrame({"observation_date": ["2026-01-03", "2026-01-02", "2026-01-04"], "X": [1.0, "", "nan"]})
    )
    assert list(frame["obs_date"]) == [date(2026, 1, 3)]
    assert frame["value"].tolist() == [1.0]


def test_second_identical_refresh_adds_no_rows(store):
    data = _series({"2026-01-01": 1.0, "2026-02-01": 2.0})
    assert store.upsert_series("X", data, source="test") == 2
    assert store.upsert_series("X", data, source="test") == 0
    assert store.series_meta("X").n_obs == 2


def test_new_observation_is_appended(store):
    store.upsert_series("X", _series({"2026-01-01": 1.0}), source="test")
    added = store.upsert_series("X", _series({"2026-01-01": 1.0, "2026-02-01": 2.0}), source="test")
    assert added == 1
    assert store.read_series("X").tolist() == [1.0, 2.0]


def test_revision_keeps_the_old_vintage_and_reads_the_new_one(store):
    first = datetime(2026, 3, 1, 12, 0)
    later = datetime(2026, 4, 1, 12, 0)
    store.upsert_series("PAYEMS", _series({"2026-02-01": 150.0}), source="fred", fetched_at=first)
    added = store.upsert_series("PAYEMS", _series({"2026-02-01": 143.0}), source="fred", fetched_at=later)
    assert added == 1  # a revision is a new row, not an overwrite

    assert store.read_series("PAYEMS").iloc[-1] == 143.0
    # What a reader knew on March 15th: the first print.
    assert store.read_series("PAYEMS", as_of=date(2026, 3, 15)).iloc[-1] == 150.0
    # Before any vintage existed: nothing.
    assert store.read_series("PAYEMS", as_of=date(2026, 2, 15)).empty
    assert len(store.read_vintages("PAYEMS")) == 2


def test_explicit_vintage_date_wins_over_fetch_time(store):
    """A release entered late still counts from its release date, not the entry date."""
    store.upsert_series(
        "ISM", _series({"2026-08-01": 48.7}), source="release",
        fetched_at=datetime(2026, 9, 20), vintage_date=date(2026, 9, 1),
    )
    assert store.read_series("ISM", as_of=date(2026, 9, 2)).iloc[-1] == 48.7
    assert store.read_series("ISM", as_of=date(2026, 8, 31)).empty


def test_read_series_has_datetime_index_and_float_values(store):
    store.upsert_series("X", {"2026-01-01": 1, "2026-01-02": 2}, source="test")
    s = store.read_series("X")
    assert isinstance(s.index, pd.DatetimeIndex)
    assert s.dtype == float
    assert store.read_series("NOPE").empty


def test_series_meta_reports_latest_value_and_dates(store):
    store.upsert_series("X", _series({"2025-12-01": 5.0, "2026-01-01": 7.0}), source="test")
    meta = store.series_meta("X")
    assert (meta.first_obs, meta.last_obs, meta.last_value, meta.source) == (
        date(2025, 12, 1), date(2026, 1, 1), 7.0, "test",
    )
    assert store.series_meta("NOPE").n_obs == 0


def test_fetch_log_keeps_latest_attempt_per_series(store):
    store.log_fetch("X", source="fred", key="DGS10", status="failed", message="timeout",
                    finished=datetime(2026, 1, 1))
    store.log_fetch("X", source="fred", key="DGS10", status="ok", n_rows=10,
                    finished=datetime(2026, 1, 2))
    latest = store.latest_fetches()
    assert len(latest) == 1
    assert latest.iloc[0]["status"] == "ok"


def test_prices_replace_on_same_date(store):
    idx = pd.to_datetime(["2026-01-02", "2026-01-05"])
    store.upsert_prices("SPY", pd.DataFrame({"close": [100.0, 101.0], "adj_close": [99.0, 100.5]}, index=idx),
                        currency="USD")
    store.upsert_prices("SPY", pd.DataFrame({"close": [100.0, 102.0]}, index=idx), currency="USD")
    assert store.read_prices("SPY").tolist() == [100.0, 102.0]
    panel = store.read_price_panel(["SPY"], adjusted=False)
    assert list(panel.columns) == ["SPY"] and len(panel) == 2
    assert store.price_symbols() == ["SPY"]


def test_snapshots_latest_and_offset(store):
    run = store.start_run("test")
    store.save_snapshot(run, "scorecard", {"a": 1})
    store.save_snapshot(run, "scorecard", {"a": 2})
    assert store.load_snapshot("scorecard")[2] == {"a": 2}
    assert store.load_snapshot("scorecard", offset=1)[2] == {"a": 1}
    assert store.load_snapshot("missing") is None
    store.finish_run(run, ok=3, failed=1, notes="fine")
    runs = store.runs("test")
    assert runs.iloc[0]["sources_failed"] == 1


def test_watchlist_round_trip(store):
    store.add_watch("aapl", cik=320193, note="cheap?")
    assert store.watchlist().iloc[0]["symbol"] == "AAPL"
    store.remove_watch("AAPL")
    assert store.watchlist().empty


def test_facts_as_of_filters_by_filing_date(store):
    frame = pd.DataFrame(
        [
            {"fact_id": "a", "cik": 1, "taxonomy": "us-gaap", "tag": "Revenues", "unit": "USD",
             "start_date": date(2024, 1, 1), "end_date": date(2024, 12, 31), "value": 100.0,
             "fy": 2024, "fp": "FY", "form": "10-K", "filed_at": date(2025, 2, 1),
             "accession": "x-1", "frame": "CY2024"},
            {"fact_id": "b", "cik": 1, "taxonomy": "us-gaap", "tag": "Revenues", "unit": "USD",
             "start_date": date(2025, 1, 1), "end_date": date(2025, 12, 31), "value": 120.0,
             "fy": 2025, "fp": "FY", "form": "10-K", "filed_at": date(2026, 2, 1),
             "accession": "x-2", "frame": "CY2025"},
        ]
    )
    assert store.upsert_facts(frame) == 2
    assert store.upsert_facts(frame) == 2  # replace, not duplicate
    assert len(store.read_facts(1)) == 2
    assert store.read_facts(1, as_of=date(2025, 6, 1))["value"].tolist() == [100.0]
    assert store.facts_ciks() == [1]


def test_read_only_store_cannot_write(tmp_path):
    path = tmp_path / "ro.duckdb"
    with Store(path) as s:
        s.upsert_series("X", {"2026-01-01": 1.0}, source="test")
    with Store(path, read_only=True) as ro:
        assert ro.read_series("X").iloc[0] == 1.0
        with pytest.raises(Exception):
            ro.upsert_series("X", {"2026-01-02": 2.0}, source="test")
