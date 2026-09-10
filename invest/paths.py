"""Where things live on disk. One place, overridable by environment.

``INVEST_DATA_DIR`` moves the DuckDB file and the report archive together, which
is what a container volume or a CI cache needs. ``INVEST_DB`` overrides the
database path alone, which is what a test needs.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"


def data_dir() -> Path:
    return Path(os.environ.get("INVEST_DATA_DIR", REPO_ROOT / "data"))


def db_path() -> Path:
    return Path(os.environ.get("INVEST_DB", data_dir() / "market.duckdb"))


def reports_dir() -> Path:
    return Path(os.environ.get("INVEST_REPORTS_DIR", data_dir() / "reports"))


def downloads_dir() -> Path:
    """Raw files as downloaded (Shiller, EBP, COT zips), kept for provenance."""
    return data_dir() / "downloads"
