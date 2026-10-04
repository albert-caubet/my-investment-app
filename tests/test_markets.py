"""The market charts: config/markets.toml and the arithmetic the Macro page plots."""

from datetime import date

import pandas as pd
import pytest

from invest.macro import markets
from invest.macro.markets import MarketsError, parse_markets


def _chart(*series, id="c") -> dict:
    return {"chart": [{"id": id, "title": "C", "series": list(series)}]}


# --- the file -----------------------------------------------------------------


def test_the_shipped_file_has_the_six_charts():
    charts = markets.load_markets()
    assert [c.title for c in charts] == [
        "World main indices", "World small and mid caps", "Europe main indices",
        "Europe mid caps", "Europe small caps", "Commodities",
    ]
    for chart in charts:
        assert len(chart.charted) <= markets.MAX_LINES
        assert all(s.currency for s in chart.charted), chart.id  # the hover prints it


def test_every_index_asked_for_has_a_line_or_a_reason():
    by_title = {c.title: c for c in markets.load_markets()}
    europe = [s.label for s in by_title["Europe main indices"].series]
    assert europe == ["FTSE 100", "CAC 40", "DAX 40", "SMI", "AEX", "OMXS30", "IBEX 35", "FTSE MIB", "OMXC25",
                      "BEL 20", "OMXH25", "OBX", "ATX", "ISEQ 20", "PSI", "WIG20", "ATHEX Composite", "PX", "BUX"]
    commodities = {s.label: s.symbol for s in by_title["Commodities"].series}
    assert commodities["Brent crude"] == "BZ=F" and commodities["European natural gas (TTF)"] == "TTF=F"
    for chart in by_title.values():
        for series in chart.series:
            assert series.symbol or series.note, series.label
            assert series.about, f"{series.label}: say what it covers"
    europe = {s.label: s.about for s in by_title["Europe main indices"].series}
    assert europe["ATHEX Composite"].startswith("Greece")
    assert {s.label: s.about for s in by_title["Europe small caps"].series}["sWIG80"].startswith("Poland")


def test_a_proxy_is_starred_in_the_legend():
    proxy, index = parse_markets(_chart(
        {"label": "MSCI EM", "symbol": "EEM", "proxy": "iShares MSCI Emerging Markets ETF"},
        {"label": "S&P 500", "symbol": "^GSPC"},
    ))[0].series
    assert proxy.legend == "MSCI EM*"
    assert index.legend == "S&P 500"


def test_a_series_without_a_symbol_is_kept_out_of_the_lines():
    chart = parse_markets(_chart({"label": "A", "symbol": "A"}, {"label": "B", "note": "no history"}))[0]
    assert [s.label for s in chart.charted] == ["A"]
    assert chart.symbols == ("A",)
    assert chart.series[1].note == "no history"


@pytest.mark.parametrize(
    "payload, message",
    [
        ({}, r"no \[\[chart\]\]"),
        ({"chart": [{"title": "t", "series": [{"label": "A", "symbol": "A"}]}]}, "no id"),
        (_chart({"label": "A"}), "no symbol and no note"),
        (_chart({"symbol": "A"}), "no label"),
        (_chart({"label": "A", "note": "n"}), "no series with a symbol"),
        (_chart({"label": "A", "symbol": "A"}, {"label": "A", "symbol": "B"}), "duplicate labels"),
        (_chart({"label": "A", "symbol": "X"}, {"label": "B", "symbol": "X"}), "duplicate symbols"),
        (_chart(*[{"label": str(i), "symbol": str(i)} for i in range(markets.MAX_LINES + 1)]), "at most 24"),
        ({"chart": _chart({"label": "A", "symbol": "A"})["chart"] * 2}, "duplicate chart ids"),
    ],
)
def test_a_broken_file_is_refused_with_the_reason(payload, message):
    with pytest.raises(MarketsError, match=message):
        parse_markets(payload)


def test_every_symbol_is_downloaded_once():
    charts = parse_markets({"chart": [
        {"id": "a", "series": [{"label": "A", "symbol": "^GSPC"}, {"label": "B", "symbol": "GC=F"}]},
        {"id": "b", "series": [{"label": "Gold", "symbol": "GC=F"}, {"label": "C", "symbol": "CL=F"}]},
    ]})
    assert markets.all_symbols(charts) == ("^GSPC", "GC=F", "CL=F")


# --- what a chart plots ---------------------------------------------------------


def _closes(values: dict) -> pd.Series:
    return pd.Series(list(values.values()), index=pd.DatetimeIndex(list(values)), dtype=float)


def test_rebase_is_the_change_since_the_first_close_in_the_range():
    closes = _closes({"2026-01-02": 50.0, "2026-01-05": 100.0, "2026-01-06": 110.0, "2026-01-07": 90.0})
    change = markets.rebase(closes, date(2026, 1, 3), date(2026, 1, 6))  # a Saturday: the next close is the base
    assert list(change.index.date) == [date(2026, 1, 5), date(2026, 1, 6)]
    assert list(change) == pytest.approx([0.0, 0.10])


def test_rebase_of_a_range_with_no_close_is_empty():
    closes = _closes({"2026-01-02": 50.0})
    assert markets.rebase(closes, date(2026, 2, 1), date(2026, 3, 1)).empty


def test_weekly_keeps_the_first_point_and_the_last_close_of_each_week():
    days = pd.bdate_range("2026-01-06", "2026-01-21")  # Tuesday to a Wednesday, three weeks
    series = pd.Series(range(len(days)), index=days, dtype=float)
    weekly = markets.weekly(series)
    assert list(weekly.index.date) == [
        date(2026, 1, 6),  # the first point, where a rebased line is 0
        date(2026, 1, 9), date(2026, 1, 16),  # Fridays
        date(2026, 1, 21),  # this week's last close, on its own date and not the coming Friday
    ]
    assert list(weekly) == [series[d] for d in weekly.index]


def test_weekly_of_a_holiday_friday_is_the_thursday():
    series = _closes({"2026-03-30": 1.0, "2026-04-02": 2.0, "2026-04-07": 3.0})  # Good Friday 3 April
    assert list(markets.weekly(series).index.date) == [date(2026, 3, 30), date(2026, 4, 2), date(2026, 4, 7)]


def test_a_weekend_quote_is_not_a_close():
    series = _closes({"2026-10-02": 4926.3, "2026-10-04": 4926.25})  # a Friday, then a Sunday
    assert list(markets.weekdays(series).index.date) == [date(2026, 10, 2)]


def test_the_common_start_is_the_latest_first_close():
    closes = {"old": _closes({"2000-01-03": 1.0, "2026-01-02": 2.0}), "young": _closes({"2021-12-14": 1.0})}
    assert markets.common_start(closes) == date(2021, 12, 14)
    assert markets.common_start({}) is None
