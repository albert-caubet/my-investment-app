"""Load ALFRED vintages for the revision-prone series (needs a FRED API key).

    python -m invest.jobs.vintages [--db PATH] [--series US_CPI,US_UNRATE,...]

Every (observation, value, first-published date) triple is stored, so
``read_series(as_of=D)`` returns the print a reader saw on D. The current
refresh keeps writing the latest vintage; this adds the history behind it.
"""

from __future__ import annotations

import argparse
import sys

from invest.data import fred
from invest.data.cache import Store
from invest.macro.catalog import load_catalog
from invest.secrets import fred_api_key

DEFAULT_SERIES = ("US_CPI", "US_CORE_PCE", "US_UNRATE", "US_CLAIMS", "US_PAYEMS", "US_GDP", "US_GDP_REAL", "US_PERMITS")


def load_vintages(store: Store, series_ids, *, api_key: str, log=print) -> dict[str, int]:
    catalog = load_catalog()
    added = {}
    for sid in series_ids:
        spec = catalog[sid]
        if spec.source != "fred":
            if log:
                log(f"  {sid}: not a FRED series, skipped")
            continue
        frame = fred.fetch_vintages(spec.key, api_key=api_key)
        n = store.insert_vintages(sid, frame, source="fred")
        added[sid] = n
        if log:
            log(f"  {sid}: {len(frame)} vintage rows, {n} new")
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Store ALFRED vintages for revision-prone series.")
    parser.add_argument("--db", default=None)
    parser.add_argument("--series", default=",".join(DEFAULT_SERIES))
    args = parser.parse_args(argv)
    key = fred_api_key()
    if not key:
        print("FRED_API_KEY is not set; vintages need the API (the keyless CSV has none).")
        return 2
    with Store(args.db) as store:
        load_vintages(store, [s.strip() for s in args.series.split(",") if s.strip()], api_key=key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
