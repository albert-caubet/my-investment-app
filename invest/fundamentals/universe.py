"""The screener universe: tickers from ``config/universe.toml``, resolved to CIKs.

The shipped list is today's members of an index, so it carries survivorship
bias: companies that left the index (often by failing) are not in it, and any
backtest over it is flattered. The report says so wherever the universe is named.
A maintained constituents CSV with a ``ticker`` column can replace the list.
"""

from __future__ import annotations

import csv
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from invest.paths import CONFIG_DIR, REPO_ROOT

UNIVERSE_PATH = CONFIG_DIR / "universe.toml"
SURVIVORSHIP_NOTE = (
    "Universe is a list of current index members: survivorship bias, since companies that "
    "left the index are absent. Not a point-in-time constituent history."
)


@dataclass(frozen=True)
class Universe:
    name: str
    label: str
    tickers: tuple[str, ...]
    note: str = ""
    csv: str | None = None
    point_in_time: bool = True  # False for yfinance-fundamentals coverage


def parse_universe(payload: dict) -> dict[str, Universe]:
    out: dict[str, Universe] = {}
    for name, body in (payload.get("universe") or {}).items():
        tickers = tuple(str(t).strip().upper() for t in body.get("tickers", []) if str(t).strip())
        csv_path = body.get("csv")
        if not tickers and not csv_path:
            raise ValueError(f"universe {name!r} has neither tickers nor a csv")
        out[name] = Universe(
            name=name,
            label=str(body.get("label", name)),
            tickers=tickers,
            note=str(body.get("note", "")),
            csv=str(csv_path) if csv_path else None,
            point_in_time=bool(body.get("point_in_time", True)),
        )
    if not out:
        raise ValueError("no [universe.*] tables")
    return out


def load_tickers_csv(path: Path) -> list[str]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "ticker" not in [f.strip().lower() for f in reader.fieldnames]:
            raise ValueError(f"{path}: needs a 'ticker' column")
        key = next(f for f in reader.fieldnames if f.strip().lower() == "ticker")
        return [row[key].strip().upper() for row in reader if row.get(key, "").strip()]


def load_universe(path: Path = UNIVERSE_PATH) -> dict[str, Universe]:
    with Path(path).open("rb") as handle:
        universes = parse_universe(tomllib.load(handle))
    resolved: dict[str, Universe] = {}
    for name, uni in universes.items():
        tickers = list(uni.tickers)
        if uni.csv:
            csv_path = Path(uni.csv)
            if not csv_path.is_absolute():
                csv_path = REPO_ROOT / csv_path
            if csv_path.exists():
                for t in load_tickers_csv(csv_path):
                    if t not in tickers:
                        tickers.append(t)
        resolved[name] = Universe(name, uni.label, tuple(tickers), uni.note, uni.csv, uni.point_in_time)
    return resolved


def _variants(ticker: str) -> list[str]:
    """``BRK-B``, ``BRK.B`` and ``BRK/B`` are the same share class written three ways."""
    base = ticker.upper()
    seen = [base]
    for a, b in (("-", "."), (".", "-"), ("/", "-"), ("-", "/")):
        alt = base.replace(a, b)
        if alt not in seen:
            seen.append(alt)
    return seen


def resolve_ciks(tickers, tickers_frame: pd.DataFrame) -> tuple[dict[str, int], list[str]]:
    """``{ticker: cik}`` from the SEC ticker list, plus the tickers it does not know."""
    lookup = {str(t).upper(): int(c) for t, c in zip(tickers_frame["ticker"], tickers_frame["cik"])}
    found: dict[str, int] = {}
    missing: list[str] = []
    for ticker in tickers:
        cik = next((lookup[v] for v in _variants(ticker) if v in lookup), None)
        if cik is None:
            missing.append(ticker)
        else:
            found[ticker] = cik
    return found, missing


SIC_SECTORS = (
    ((100, 999), "agriculture"),
    ((1000, 1499), "mining and energy"),
    ((1500, 1799), "construction"),
    ((2000, 2099), "food and beverages"),
    ((2100, 2199), "tobacco"),
    ((2800, 2829), "chemicals"),
    ((2830, 2836), "pharmaceuticals and biotech"),
    ((2840, 2899), "chemicals"),
    ((2900, 2999), "mining and energy"),
    ((3570, 3579), "technology hardware"),
    ((3670, 3679), "semiconductors"),
    ((3800, 3851), "medical equipment"),
    ((3700, 3799), "transportation equipment"),
    ((2000, 3999), "manufacturing"),
    ((4000, 4799), "transportation"),
    ((4800, 4899), "communications"),
    ((4900, 4999), "utilities"),
    ((5000, 5199), "wholesale"),
    ((5200, 5999), "retail"),
    ((6020, 6199), "banks"),
    ((6200, 6299), "securities and exchanges"),
    ((6300, 6499), "insurance"),
    ((6500, 6599), "real estate"),
    ((6798, 6798), "real estate"),
    ((6000, 6799), "financials"),
    ((7370, 7379), "software and services"),
    ((7000, 8999), "services"),
    ((8000, 8099), "health services"),
    ((9100, 9999), "public administration"),
)


def sector_from_sic(sic) -> str:
    """A coarse sector label from the SEC SIC code; ``unknown`` when absent."""
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return "unknown"
    for (low, high), label in SIC_SECTORS:
        if low <= code <= high:
            return label
    return "unknown"


def is_financial_sic(sic) -> bool:
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return False
    return 6000 <= code <= 6799
