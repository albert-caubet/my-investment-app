"""The refresh job on a fake fetcher: idempotence, failure reporting, derived series, releases."""

from datetime import date

import pandas as pd
import pytest

from invest.data.cache import Store
from invest.data.releases import Release
from invest.data.sources import SourceError
from invest.jobs.refresh import refresh
from invest.macro.catalog import parse_catalog

CATALOG = parse_catalog(
    {
        "series": [
            {"id": "A", "label": "a", "group": "rates", "source": "fred", "key": "A", "frequency": "M",
             "lag_days": 10, "critical": True},
            {"id": "B", "label": "b", "group": "rates", "source": "fred", "key": "B", "frequency": "M",
             "critical": True, "fallback": "A"},
            {"id": "C", "label": "c", "group": "rates", "source": "fred", "key": "C", "frequency": "D"},
            {"id": "SPREAD", "label": "s", "group": "rates", "source": "derived", "formula": "spread",
             "inputs": ["A", "C"], "frequency": "M"},
            {"id": "NEEDS_B", "label": "n", "group": "rates", "source": "derived", "formula": "yoy",
             "inputs": ["B"], "frequency": "M"},
            {"id": "ISM", "label": "ism", "group": "surveys", "source": "release", "frequency": "M"},
            {"id": "ISM2", "label": "ism2", "group": "surveys", "source": "release", "frequency": "M"},
            {"id": "ISM_SPREAD", "label": "d", "group": "surveys", "source": "derived", "formula": "spread",
             "inputs": ["ISM", "ISM2"], "frequency": "M"},
            {"id": "AVG", "label": "avg", "group": "surveys", "source": "derived", "formula": "mean_available",
             "inputs": ["A", "C", "ISM"], "frequency": "M"},
        ]
    }
)

TODAY = date(2026, 9, 10)


def _frame(values: dict) -> pd.DataFrame:
    return pd.DataFrame({"obs_date": [pd.Timestamp(k).date() for k in values], "value": list(values.values())})


class FakeFetcher:
    def __init__(self, data: dict):
        self.data = data
        self.yahoo_frames = {}
        self.downloads = []
        self.calls = []

    def prefetch_yahoo(self, specs):
        pass

    def fetch(self, spec):
        self.calls.append(spec.id)
        value = self.data[spec.id]
        if isinstance(value, Exception):
            raise value
        return value


GOOD = {
    "A": _frame({"2026-06-01": 1.0, "2026-07-01": 2.0, "2026-08-01": 3.0}),
    "B": SourceError("B: HTTP 500"),
    "C": _frame({"2026-08-01": 0.5, "2026-09-09": 0.7}),
}


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "r.duckdb") as s:
        yield s


def test_first_run_stores_series_and_reports_failures(store):
    report = refresh(store, CATALOG, FakeFetcher(GOOD), today=TODAY, log=None)
    by_id = {r.id: r for r in report.rows}
    assert by_id["A"].status == "ok"
    assert by_id["C"].status == "ok"
    assert by_id["B"].status == "fallback"  # failed, but its fallback A is fresh
    assert "HTTP 500" in by_id["B"].message
    assert by_id["SPREAD"].status == "ok"
    assert by_id["NEEDS_B"].status == "failed" and "missing inputs" in by_id["NEEDS_B"].message
    assert by_id["ISM"].status == "manual"
    # waiting only on hand-entered releases is not a failure of the job
    assert by_id["ISM_SPREAD"].status == "manual"
    # an average of whatever exists runs on the two available inputs and says so
    assert by_id["AVG"].status == "ok" and "without ISM" in by_id["AVG"].message
    assert not store.read_series("AVG").empty
    assert report.n_ok == 2 and report.n_failed == 1
    assert report.exit_code == 0  # the critical failure is covered by a fallback
    spread = store.read_series("SPREAD")
    assert spread.loc["2026-08-01"] == pytest.approx(3.0 - 0.5)
    # the monthly A is carried onto the daily C's later date; nothing is invented before A exists
    assert spread.iloc[-1] == pytest.approx(3.0 - 0.7)
    assert spread.index[0] == pd.Timestamp("2026-08-01")


def test_second_identical_run_adds_no_rows(store):
    refresh(store, CATALOG, FakeFetcher(GOOD), today=TODAY, log=None)
    before = store.list_series().set_index("series_id")["n_rows"].to_dict()
    refresh(store, CATALOG, FakeFetcher(GOOD), today=TODAY, log=None)
    after = store.list_series().set_index("series_id")["n_rows"].to_dict()
    assert before == after
    assert len(store.runs("refresh")) == 2


def test_uncovered_critical_failure_sets_exit_code(store):
    data = dict(GOOD)
    data["A"] = SourceError("A: timeout")
    report = refresh(store, CATALOG, FakeFetcher(data), today=TODAY, log=None)
    assert report.exit_code == 1
    assert {r.id for r in report.critical_failures} == {"A", "B"}
    assert store.latest_fetches().set_index("series_id").loc["A", "status"] == "failed"


def test_stale_critical_series_is_flagged_and_taints_derived_series(store):
    data = dict(GOOD)
    data["A"] = _frame({"2026-01-01": 1.0, "2026-02-01": 2.0})  # monthly, last obs 7 months ago
    report = refresh(store, CATALOG, FakeFetcher(data), today=TODAY, log=None)
    rows = {r.id: r for r in report.rows}
    assert rows["A"].status == "stale"
    assert rows["A"].age_days == (TODAY - date(2026, 2, 1)).days
    assert report.exit_code == 1
    # SPREAD = A - C is dated by C (fresh) but rests on a stale A
    assert rows["SPREAD"].status == "stale"
    assert "stale input: A" in rows["SPREAD"].message


def test_fresh_monthly_value_is_not_stale_just_before_the_next_release(store):
    """On September 30th, August's monthly value (dated August 1st) is the newest there is."""
    data = dict(GOOD)
    data["A"] = _frame({"2026-07-01": 1.0, "2026-08-01": 2.0})
    report = refresh(store, CATALOG, FakeFetcher(data), today=date(2026, 9, 30), log=None)
    assert {r.id: r.status for r in report.rows}["A"] == "ok"


def test_releases_are_ingested_with_their_release_date_as_vintage(store):
    releases = [Release("ISM", date(2026, 8, 1), date(2026, 9, 1), 48.7, "ISM")]
    refresh(store, CATALOG, FakeFetcher(GOOD), releases=releases, today=TODAY, log=None)
    assert store.read_series("ISM").iloc[-1] == 48.7
    assert store.read_series("ISM", as_of=date(2026, 8, 31)).empty
    vint = store.read_vintages("ISM")
    assert str(vint.iloc[0]["vintage_date"])[:10] == "2026-09-01"


def test_release_for_unknown_series_is_reported_not_raised(store):
    releases = [Release("NOPE", date(2026, 8, 1), date(2026, 9, 1), 1.0)]
    report = refresh(store, CATALOG, FakeFetcher(GOOD), releases=releases, today=TODAY, log=None)
    assert {r.id: r.status for r in report.rows}["ISM"] == "failed"


def test_only_limits_the_fetch_and_marks_the_rest_skipped(store):
    fetcher = FakeFetcher(GOOD)
    report = refresh(store, CATALOG, fetcher, only={"A"}, today=TODAY, log=None)
    assert fetcher.calls == ["A"]
    assert {r.id: r.status for r in report.rows}["C"] == "skipped"


def test_freshness_snapshot_is_saved(store):
    report = refresh(store, CATALOG, FakeFetcher(GOOD), today=TODAY, log=None)
    run_id, _, payload = store.load_snapshot("freshness")
    assert run_id == report.run_id
    assert {row["id"] for row in payload} == {s.id for s in CATALOG}
    assert "series" in report.table()
