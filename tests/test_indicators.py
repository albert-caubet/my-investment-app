"""Indicator readings: z-score, percentile, change and the sentence, hand-computed."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from invest.macro.catalog import parse_catalog
from invest.macro.indicators import (
    change_over_months,
    concern_score,
    format_value,
    indicator,
    percentile,
    reading_sentence,
    recession_spans,
    zscore,
)


def monthly(values, start="2016-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"), dtype=float)


def spec(**over):
    base = {"id": "X", "label": "Thing", "group": "rates", "source": "fred", "key": "X", "frequency": "M",
            "units": "%", "direction": "high_bad"}
    base.update(over)
    return parse_catalog({"series": [base]})["X"]


def test_zscore_hand_computed():
    s = monthly([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 24.0])
    z, n = zscore(s, window_years=10)
    values = np.array(s)
    expected = (24.0 - values.mean()) / values.std(ddof=1)
    assert n == 12
    assert z == pytest.approx(expected)


def test_zscore_needs_enough_observations_and_dispersion():
    assert zscore(monthly([1.0] * 5))[0] is None
    z, n = zscore(monthly([3.0] * 30))
    assert z is None and n == 30


def test_zscore_window_excludes_older_history():
    old = monthly([100.0] * 60, start="2000-01-01")           # far outside a 10-year window
    recent = monthly([1.0] * 23 + [2.0], start="2024-01-01")   # the window itself
    s = pd.concat([old, recent])
    z, n = zscore(s, window_years=10)
    assert n == 24
    assert z == pytest.approx((2.0 - recent.mean()) / recent.std(ddof=1))


def test_percentile_is_a_mid_rank():
    s = monthly(list(range(1, 25)) + [12.0])  # 25 observations, latest equals one earlier value
    pct, n = percentile(s)
    # 11 values below 12, two equal (the earlier 12 and itself): (11 + 0.5 * 2) / 25
    assert n == 25
    assert pct == pytest.approx((11 + 1.0) / 25 * 100)
    assert percentile(monthly([1.0] * 10))[0] is None


def test_change_over_months_uses_the_calendar():
    s = monthly([10.0, 11.0, 12.0, 13.0, 14.0])
    assert change_over_months(s, 3) == pytest.approx(14.0 - 11.0)
    assert change_over_months(monthly([1.0, 2.0]), 3) is None
    # a gap: the "three months ago" observation is a year stale, so no change is reported
    sparse = pd.Series([1.0, 5.0], index=pd.to_datetime(["2024-01-01", "2025-06-01"]))
    assert change_over_months(sparse, 3) is None


def test_concern_score_follows_direction():
    assert concern_score(1.5, "high_bad") == 1.5
    assert concern_score(1.5, "low_bad") == -1.5
    assert concern_score(1.5, "neutral") is None
    assert concern_score(None, "high_bad") is None


@pytest.mark.parametrize(
    "value,units,text",
    [
        (4.8, "%", "4.80%"),
        (0.88, "pp", "+0.88 pp"),
        (0.16, "prob", "16%"),
        (0.0107, "frac", "1.07%"),
        (206000.0, "persons", "206,000"),
        (1.87, "x", "1.87x"),
        (None, "%", "–"),
        (float("nan"), "%", "–"),
        (7636.36, "", "7,636"),
        (15.72, "", "15.72"),
    ],
)
def test_format_value(value, units, text):
    assert format_value(value, units) == text


def test_indicator_reading_end_to_end():
    s = monthly([100.0 * 1.03 ** (m / 12) for m in range(37)], start="2023-01-01")  # 3% a year
    reading = indicator(s, spec(transform="yoy", units="%"), today=date(2026, 2, 15), stale_after_days=69)
    assert reading.value == pytest.approx(3.0, abs=1e-6)
    assert reading.obs_date == date(2026, 1, 1)
    assert reading.age_days == 45 and not reading.stale
    assert reading.transform == "yoy"
    assert reading.n_history == 25  # 37 months minus the first 12
    assert reading.z is None  # a constant growth series has no dispersion
    assert reading.percentile is not None
    assert "3.00% as of 2026-01-01" in reading.reading
    assert "Higher is the concern." in reading.reading


def test_indicator_flags_staleness_and_missing_data():
    stale = indicator(monthly([1.0] * 30), spec(), today=date(2026, 9, 10), stale_after_days=30)
    assert stale.stale and "Stale" in stale.reading
    empty = indicator(pd.Series(dtype=float), spec())
    assert empty.value is None and empty.note == "no data"
    short = indicator(monthly([1.0, 2.0]), spec(transform="yoy"))
    assert short.value is None and "not enough history" in short.note


def test_indicator_as_of_truncates_history():
    s = monthly(list(range(1, 31)), start="2024-01-01")
    reading = indicator(s, spec(units="", direction="neutral"), as_of=date(2025, 6, 30))
    assert reading.obs_date == date(2025, 6, 1)
    assert reading.value == 18.0
    assert reading.concern is None


def test_reading_sentence_without_stats():
    text = reading_sentence(4.8, date(2026, 9, 8), "%", None, 10, None, 0, None, None, "neutral", False)
    assert text == "4.80% as of 2026-09-08."


def test_recession_spans():
    idx = pd.date_range("2007-10-01", periods=24, freq="MS")
    values = [0] * 2 + [1] * 18 + [0] * 4  # Dec 2007 to May 2009 like the GFC
    spans = recession_spans(pd.Series(values, index=idx, dtype=float))
    assert spans == [(date(2007, 12, 1), date(2009, 5, 1))]
    open_ended = recession_spans(pd.Series([0, 1, 1], index=idx[:3], dtype=float))
    assert open_ended == [(date(2007, 11, 1), date(2007, 12, 1))]
    assert recession_spans(pd.Series(dtype=float)) == []
