"""Downloaded files: Shiller's data, the excess bond premium, regional Fed surveys,
NY Fed model outputs. Each download is kept on disk with its SHA-256 and fetch
time, and each parser is a pure function over rows so it can be tested on a CSV
fixture instead of a binary workbook.

Every parser returns frames dated by the **first day** of the period, like the
SDMX and FRED parsers, so the catalog can mix them.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from invest.data.http import FetchError, fetch_bytes, fetch_text
from invest.paths import downloads_dir

SHILLER_PAGE = "https://shillerdata.com/"
SHILLER_FALLBACK_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
EBP_URL = "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv"
PHILLY_URL = (
    "https://www.philadelphiafed.org/-/media/frbp/assets/surveys-and-data/mbos/"
    "historical-data/diffusion-indexes/bos_dif.csv"
)
NYFED_RECPROB_URL = "https://www.newyorkfed.org/medialibrary/media/research/capital_markets/allmonth.xls"
HLW_URL = (
    "https://www.newyorkfed.org/medialibrary/media/research/economists/williams/data/"
    "Holston_Laubach_Williams_current_estimates.xlsx"
)
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; my-investment-app research tool)"}


@dataclass(frozen=True)
class DownloadResult:
    name: str
    url: str
    path: Path
    sha256: str
    n_bytes: int
    fetched_at: datetime


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str, name: str, *, dest_dir: Path | None = None, headers: dict | None = None) -> DownloadResult:
    """Fetch ``url`` to ``dest_dir/name`` and return its checksum and size."""
    data = fetch_bytes(url, headers=headers or BROWSER_HEADERS)
    if len(data) < 200:
        raise FetchError(f"{url}: response too small to be the file ({len(data)} bytes)")
    dest = (dest_dir or downloads_dir())
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / name
    path.write_bytes(data)
    return DownloadResult(
        name=name, url=url, path=path, sha256=sha256_of(data), n_bytes=len(data), fetched_at=datetime.now()
    )


# ---------------------------------------------------------------------------
# Shiller: S&P price, dividends, earnings, CPI, GS10 and CAPE since 1871
# ---------------------------------------------------------------------------

#: Column positions in the ``Data`` sheet of ``ie_data.xls``, stable for years.
SHILLER_COLUMNS = {
    0: "date",
    1: "price",
    2: "dividend",
    3: "earnings",
    4: "cpi",
    6: "gs10",
    7: "real_price",
    8: "real_dividend",
    9: "real_tr_price",
    10: "real_earnings",
    11: "real_tr_scaled_earnings",
    12: "cape",
    14: "tr_cape",
    16: "excess_cape_yield",
}


def shiller_download_url() -> str:
    """The current ``ie_data.xls`` link on shillerdata.com, else the old Yale URL.

    The Yale copy stopped updating in 2023; the new site changes the link's
    version string with every update, so it is scraped rather than hard-coded.
    """
    try:
        page = fetch_text(SHILLER_PAGE, headers=BROWSER_HEADERS)
    except FetchError:
        return SHILLER_FALLBACK_URL
    match = re.search(r'href="([^"]*ie_data\.xls[^"]*)"', page)
    if not match:
        return SHILLER_FALLBACK_URL
    url = match.group(1)
    if url.startswith("//"):
        url = "https:" + url
    return url


def _shiller_month(raw) -> date | None:
    """``2026.1`` is October (the trailing zero is lost in the float), ``2026.01`` January."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    year = int(value)
    month = int(round((value - year) * 100))
    if month == 0:
        month = 10  # "2026.10" read back as 2026.1 -> 0.1 * 100 = 10; guard for float noise
    if not (1871 <= year <= 2200 and 1 <= month <= 12):
        return None
    return date(year, month, 1)


def _num(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if text in ("", "NA", "N/A", "nan", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_shiller_rows(rows: Iterable[Sequence]) -> pd.DataFrame:
    """Rows of the ``Data`` sheet into a monthly frame indexed by month start.

    Header rows and the trailing note row are skipped by requiring a parseable
    ``YYYY.MM`` in the first column. The most recent month is usually partial
    (price is a month-to-date average, earnings are blank until reported), and is
    kept as-is: the column's own NaN says what is not yet known.
    """
    records = []
    for row in rows:
        if not row:
            continue
        month = _shiller_month(row[0])
        if month is None:
            continue
        record = {"obs_date": month}
        for position, name in SHILLER_COLUMNS.items():
            if name == "date":
                continue
            record[name] = _num(row[position]) if position < len(row) else None
        records.append(record)
    if not records:
        raise ValueError("no Shiller data rows found")
    frame = pd.DataFrame(records).drop_duplicates("obs_date", keep="last")
    return frame.sort_values("obs_date").reset_index(drop=True)


def read_shiller_xls(path: Path) -> pd.DataFrame:
    import xlrd

    sheet = xlrd.open_workbook(str(path)).sheet_by_name("Data")
    rows = ([sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows))
    return parse_shiller_rows(rows)


# ---------------------------------------------------------------------------
# Excess bond premium (Gilchrist-Zakrajsek, Federal Reserve FEDS Notes)
# ---------------------------------------------------------------------------

def parse_ebp_csv(text: str) -> pd.DataFrame:
    """``date,gz_spread,ebp,est_prob`` (dates as ``M/D/YYYY``) into a monthly frame."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "ebp" not in [f.strip() for f in reader.fieldnames]:
        raise ValueError(f"unexpected EBP header: {reader.fieldnames}")
    records = []
    for row in reader:
        raw = (row.get("date") or "").strip()
        if not raw:
            continue
        stamp = pd.to_datetime(raw, format="%m/%d/%Y", errors="coerce")
        if pd.isna(stamp):
            stamp = pd.to_datetime(raw, errors="coerce")
        if pd.isna(stamp):
            continue
        records.append(
            {
                "obs_date": date(stamp.year, stamp.month, 1),
                "gz_spread": _num(row.get("gz_spread")),
                "ebp": _num(row.get("ebp")),
                "est_prob": _num(row.get("est_prob")),
            }
        )
    return pd.DataFrame(records).sort_values("obs_date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Philadelphia Fed Manufacturing Business Outlook Survey
# ---------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def _philly_date(text: str) -> date | None:
    """``May-68`` style. The survey began in 1968, so two-digit years pivot at 50."""
    match = re.match(r"^([A-Za-z]{3})-(\d{2,4})$", text.strip())
    if not match:
        return None
    month = _MONTHS.get(match.group(1).title())
    if month is None:
        return None
    year = int(match.group(2))
    if year < 100:
        year += 1900 if year >= 50 else 2000
    return date(year, month, 1)


def parse_philly_csv(text: str) -> pd.DataFrame:
    """The diffusion-index CSV: ``DATE,GAC,NOC,...``. Column names kept as published.

    ``GAC`` is the current general activity index, ``NOC`` new orders, ``NEC``
    employment, ``PPC`` prices paid; the ``F`` suffix marks six-month expectations.
    """
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "GAC" not in [f.strip() for f in reader.fieldnames]:
        raise ValueError(f"unexpected Philly Fed header: {reader.fieldnames}")
    records = []
    for row in reader:
        day = _philly_date(row.get("DATE") or "")
        if day is None:
            continue
        record = {"obs_date": day}
        for key, value in row.items():
            if key and key.strip() != "DATE":
                record[key.strip()] = _num(value)
        records.append(record)
    return pd.DataFrame(records).sort_values("obs_date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# NY Fed: recession probability from the term spread (allmonth.xls)
# ---------------------------------------------------------------------------

def _excel_serial_to_date(serial) -> date | None:
    value = _num(serial)
    if value is None or value < 1000:
        return None
    return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(value))).date()


def parse_nyfed_recprob_rows(rows: Iterable[Sequence]) -> pd.DataFrame:
    """Sheet ``rec_prob``: Date, 10y, 3m, 3m (bond-equivalent), spread, Rec_prob, NBER_Rec.

    ``Rec_prob`` on row ``D`` is the model's probability that month ``D`` is in
    recession, computed from the spread twelve months earlier, so the file runs a
    year past the last spread. The frame keeps both views: ``target_month`` as
    written, and ``obs_date`` twelve months before it, which is when the reading
    became known. Dating the value by ``obs_date`` keeps freshness checks honest.
    """
    records = []
    for row in rows:
        if not row:
            continue
        target = _excel_serial_to_date(row[0])
        if target is None:
            continue
        target = date(target.year, target.month, 1)
        known = date(target.year - 1, target.month, 1)
        records.append(
            {
                "obs_date": known,
                "target_month": target,
                "spread": _num(row[4]) if len(row) > 4 else None,
                "rec_prob": _num(row[5]) if len(row) > 5 else None,
                "nber_rec": _num(row[6]) if len(row) > 6 else None,
            }
        )
    if not records:
        raise ValueError("no NY Fed recession-probability rows found")
    return pd.DataFrame(records).sort_values("obs_date").reset_index(drop=True)


def read_nyfed_recprob_xls(path: Path) -> pd.DataFrame:
    import xlrd

    sheet = xlrd.open_workbook(str(path)).sheet_by_name("rec_prob")
    rows = ([sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(1, sheet.nrows))
    return parse_nyfed_recprob_rows(rows)


# ---------------------------------------------------------------------------
# NY Fed: Holston-Laubach-Williams r* (xlsx)
# ---------------------------------------------------------------------------

def parse_hlw_rows(rows: Sequence[Sequence]) -> pd.DataFrame:
    """Sheet ``HLW Estimates``: quarterly r*, trend growth and output gap for US, Canada, euro area.

    The layout has a section-title row (``Natural Rate (r*)`` ...) above a row of
    region names; both are located by content, not position.
    """
    rows = [list(r) for r in rows]
    header_idx = next((i for i, r in enumerate(rows) if r and str(r[0]).strip() == "Date"), None)
    if header_idx is None or header_idx == 0:
        raise ValueError("HLW sheet: no 'Date' header row")
    titles = rows[header_idx - 1]
    regions = rows[header_idx]
    sections: dict[int, str] = {}
    current = None
    for col in range(len(regions)):
        title = str(titles[col]).strip() if col < len(titles) and titles[col] is not None else ""
        if title:
            lowered = title.lower()
            if "natural rate" in lowered or "r*" in lowered:
                current = "rstar"
            elif "trend growth" in lowered:
                current = "g"
            elif "output gap" in lowered:
                current = "gap"
            elif "other determinants" in lowered:
                current = "z"
            else:
                current = None
        region = str(regions[col]).strip().lower() if regions[col] is not None else ""
        if current and region in ("us", "euro area", "canada"):
            suffix = {"us": "us", "euro area": "ea", "canada": "ca"}[region]
            sections[col] = f"{current}_{suffix}"
    if not any(name.startswith("rstar_") for name in sections.values()):
        raise ValueError("HLW sheet: no r* columns found")

    records = []
    for row in rows[header_idx + 1:]:
        if not row or row[0] in (None, ""):
            continue
        stamp = pd.to_datetime(str(row[0]), errors="coerce")
        if pd.isna(stamp):
            continue
        record = {"obs_date": date(stamp.year, stamp.month, 1)}
        for col, name in sections.items():
            record[name] = _num(row[col]) if col < len(row) else None
        records.append(record)
    return pd.DataFrame(records).sort_values("obs_date").reset_index(drop=True)


def read_hlw_xlsx(path: Path) -> pd.DataFrame:
    import openpyxl

    book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    sheet = book["HLW Estimates"]
    rows = [list(r) for r in sheet.iter_rows(values_only=True)]
    return parse_hlw_rows(rows)
