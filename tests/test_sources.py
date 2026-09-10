"""Source parsers on saved responses. No network anywhere in this file."""

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from invest.data import bis, ecb, eurostat, fred, oecd  # noqa: F401  (import check)
from invest.data.ecb import parse_period, parse_sdmx_csv, split_key
from invest.data.eurostat import parse_jsonstat
from invest.data.files import (
    parse_ebp_csv,
    parse_hlw_rows,
    parse_nyfed_recprob_rows,
    parse_philly_csv,
    parse_shiller_rows,
)
from invest.data.fred import parse_api_observations, parse_fredgraph_csv
from invest.data.releases import parse_releases

FIX = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _rows(name: str) -> list[list]:
    with (FIX / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


# --- FRED -------------------------------------------------------------------


def test_fredgraph_csv_drops_blank_holidays_and_keeps_order():
    frame = parse_fredgraph_csv(_read("fred_DGS10.csv"))
    assert list(frame.columns) == ["obs_date", "value"]
    assert frame.iloc[0]["obs_date"] == date(1962, 1, 2)
    assert frame.iloc[0]["value"] == 4.06
    # 2026-09-07 is blank in the file (Labor Day) and must not appear as 0 or NaN.
    assert date(2026, 9, 7) not in set(frame["obs_date"])
    assert frame["value"].notna().all()
    assert frame["obs_date"].is_monotonic_increasing


def test_fredgraph_rejects_html_error_pages():
    with pytest.raises(ValueError):
        parse_fredgraph_csv("<html><body>Not found</body></html>\n<p>x</p>")


def test_fred_api_observations_keep_vintage_dates():
    payload = {
        "observations": [
            {"realtime_start": "2026-02-06", "realtime_end": "2026-03-05", "date": "2026-01-01", "value": "150.0"},
            {"realtime_start": "2026-03-06", "realtime_end": "9999-12-31", "date": "2026-01-01", "value": "143.0"},
            {"realtime_start": "2026-03-06", "realtime_end": "9999-12-31", "date": "2026-02-01", "value": "."},
        ]
    }
    frame = parse_api_observations(payload)
    assert len(frame) == 2  # the "." is a not-yet-published value, dropped
    assert frame["vintage_date"].tolist() == [date(2026, 2, 6), date(2026, 3, 6)]
    assert frame["value"].tolist() == [150.0, 143.0]


# --- SDMX (ECB, OECD, BIS) --------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-09-08", date(2026, 9, 8)),
        ("2026-08", date(2026, 8, 1)),
        ("2026-Q3", date(2026, 7, 1)),
        ("2026Q1", date(2026, 1, 1)),
        ("2025", date(2025, 1, 1)),
        ("2026-S2", date(2026, 7, 1)),
    ],
)
def test_parse_period_returns_first_day(text, expected):
    assert parse_period(text) == expected


def test_split_key_accepts_slash_and_dotted_forms():
    assert split_key("ICP/M.U2.N.000000.4.ANR") == ("ICP", "M.U2.N.000000.4.ANR")
    assert split_key("ICP.M.U2.N.000000.4.ANR") == ("ICP", "M.U2.N.000000.4.ANR")


def test_ecb_deposit_rate_csv():
    frame = parse_sdmx_csv(_read("ecb_FM_DFR.csv"))
    assert frame.iloc[-1]["obs_date"] == date(2026, 6, 17)
    assert frame.iloc[-1]["value"] == 2.25
    assert frame["obs_date"].is_monotonic_increasing


def test_ecb_quarterly_bank_lending_survey_dates_by_quarter_start():
    frame = parse_sdmx_csv(_read("ecb_BLS.csv"))
    assert all(d.day == 1 and d.month in (1, 4, 7, 10) for d in frame["obs_date"])
    assert len(frame) >= 4


def test_ecb_hicp_monthly_dates_by_month_start():
    frame = parse_sdmx_csv(_read("ecb_ICP_U2_ANR.csv"))
    assert frame.iloc[0]["obs_date"] == date(2025, 1, 1)
    assert all(d.day == 1 for d in frame["obs_date"])


def test_oecd_cli_csv_uses_labelled_columns():
    frame = parse_sdmx_csv(_read("oecd_cli_usa.csv"))
    assert len(frame) >= 12
    assert 90 < frame["value"].iloc[-1] < 110  # an amplitude-adjusted index around 100


def test_bis_credit_gap_csv_is_quarterly():
    frame = parse_sdmx_csv(_read("bis_credit_gap_us.csv"))
    assert frame.iloc[0]["obs_date"] == date(2023, 1, 1)
    assert frame.iloc[0]["value"] == pytest.approx(-7.6235)


def test_sdmx_refuses_a_response_that_mixes_series():
    text = _read("oecd_cli_usa.csv")
    lines = text.splitlines()
    mixed = "\n".join(lines + [lines[1].replace("USA,United States", "EA20,Euro area")])
    with pytest.raises(ValueError, match="mixes"):
        parse_sdmx_csv(mixed)


# --- Eurostat ---------------------------------------------------------------


def test_eurostat_unemployment_jsonstat():
    frame = parse_jsonstat(json.loads(_read("eurostat_une_rt_m.json")))
    assert frame.iloc[0]["obs_date"] == date(2025, 1, 1)
    assert 4 < frame["value"].iloc[-1] < 10
    assert frame["obs_date"].is_monotonic_increasing


def test_eurostat_quarterly_gdp_jsonstat():
    frame = parse_jsonstat(json.loads(_read("eurostat_namq_10_gdp.json")))
    assert frame.iloc[0]["obs_date"] == date(2024, 1, 1)
    assert len(frame) == 10


def test_eurostat_refuses_unpinned_dimensions():
    payload = json.loads(_read("eurostat_une_rt_m.json"))
    payload["size"][payload["id"].index("geo")] = 2
    with pytest.raises(ValueError, match="not pinned"):
        parse_jsonstat(payload)


# --- files: Shiller, EBP, Philly Fed, NY Fed ---------------------------------


def test_shiller_rows_parse_dates_and_columns():
    frame = parse_shiller_rows(_rows("shiller_ie_data_rows.csv"))
    first = frame.iloc[0]
    assert first["obs_date"] == date(1871, 1, 1)
    assert first["price"] == pytest.approx(4.44)
    assert first["cpi"] == pytest.approx(12.46406116)
    assert frame["cape"].iloc[0] != frame["cape"].iloc[0]  # NA before ten years of data -> NaN
    last = frame.iloc[-1]
    assert last["obs_date"] == date(2026, 9, 1)
    assert 30 < last["cape"] < 60
    assert 0 < last["excess_cape_yield"] < 0.05
    # October is written 2026.1 in the sheet and must not become January.
    october = frame[frame["obs_date"] == date(2025, 10, 1)]
    assert len(october) == 1


def test_shiller_rejects_a_sheet_with_no_data_rows():
    with pytest.raises(ValueError):
        parse_shiller_rows([["Date", "P"], ["note", ""]])


def test_ebp_csv_parses_month_starts():
    frame = parse_ebp_csv(_read("fed_ebp.csv"))
    assert frame.iloc[0]["obs_date"] == date(1973, 1, 1)
    assert frame.iloc[0]["ebp"] == pytest.approx(-0.046854494)
    assert frame.iloc[-1]["obs_date"] == date(2026, 7, 1)
    assert set(frame.columns) >= {"gz_spread", "ebp", "est_prob"}


def test_philly_csv_pivots_two_digit_years_correctly():
    frame = parse_philly_csv(_read("philly_bos_dif.csv"))
    assert frame.iloc[0]["obs_date"] == date(1968, 5, 1)  # "May-68" is 1968, not 2068
    assert frame.iloc[0]["GAC"] == pytest.approx(32.2)
    assert frame.iloc[-1]["obs_date"].year >= 2026
    assert {"GAC", "NOC", "NEC", "PPC"} <= set(frame.columns)


def test_nyfed_recprob_rows_shift_target_month_back_a_year():
    frame = parse_nyfed_recprob_rows(_rows("nyfed_rec_prob_rows.csv")[1:])
    first = frame.iloc[0]
    assert first["target_month"] == date(1959, 1, 1)  # Excel serial 21581 = 1959-01-31, month end
    assert first["obs_date"] == date(1958, 1, 1)
    assert first["spread"] == pytest.approx(1.1403059, abs=1e-6)
    last = frame.dropna(subset=["rec_prob"]).iloc[-1]
    assert 0 <= last["rec_prob"] <= 1
    assert last["target_month"].year - last["obs_date"].year == 1


def test_hlw_rows_locate_rstar_columns_by_title():
    rows = _rows("hlw_estimates_rows.csv")
    frame = parse_hlw_rows(rows)
    assert {"rstar_us", "rstar_ea", "g_us"} <= set(frame.columns)
    assert frame.iloc[0]["obs_date"] == date(1961, 1, 1)
    assert frame.iloc[0]["rstar_us"] == pytest.approx(5.47734664, abs=1e-6)
    assert frame.iloc[0]["rstar_ea"] != frame.iloc[0]["rstar_ea"]  # "NA" -> NaN
    assert frame.iloc[-1]["obs_date"] == date(2026, 4, 1)
    assert -1 < frame.iloc[-1]["rstar_us"] < 3


def test_hlw_rejects_a_sheet_without_a_header():
    with pytest.raises(ValueError):
        parse_hlw_rows([["x", "y"], ["1", "2"]])


# --- releases -----------------------------------------------------------------


def test_releases_parse_and_validate():
    payload = {
        "release": [
            {"series": "US_ISM_MFG_PMI", "period": "2026-08", "released": date(2026, 9, 1), "value": 48.7},
            {"series": "US_ISM_MFG_PMI", "period": "2026-07", "released": "2026-08-01", "value": 49.1, "note": "x"},
        ]
    }
    releases = parse_releases(payload)
    assert releases[0].period == date(2026, 8, 1)
    assert releases[0].released == date(2026, 9, 1)
    assert releases[1].note == "x"


def test_release_before_its_period_is_refused():
    with pytest.raises(ValueError, match="released before"):
        parse_releases({"release": [{"series": "X", "period": "2026-08", "released": "2026-07-01", "value": 1}]})


def test_release_without_value_is_refused():
    with pytest.raises(ValueError):
        parse_releases({"release": [{"series": "X", "period": "2026-08", "released": "2026-09-01"}]})
