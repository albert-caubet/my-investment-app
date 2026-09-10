"""Back up the DuckDB file and the report archive; keep the last N copies.

    python -m invest.jobs.backup [--dest DIR] [--keep N]

The database is checkpointed and copied through DuckDB itself (``COPY`` of a
consistent snapshot is not needed: a checkpoint followed by a file copy is safe
while no other writer holds the file, and the jobs are the only writers). The
reports directory is zipped. Old copies beyond ``--keep`` are deleted.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import duckdb

from invest.paths import data_dir, db_path, reports_dir

DEFAULT_KEEP = 14


def backup(*, dest: Path | None = None, keep: int = DEFAULT_KEEP, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    dest = dest or (data_dir() / "backups")
    dest.mkdir(parents=True, exist_ok=True)
    stamp = f"{now:%Y%m%d_%H%M%S}"
    result = {"database": None, "reports": None, "removed": []}

    source = db_path()
    if source.exists():
        con = duckdb.connect(str(source))
        try:
            con.execute("CHECKPOINT")
        finally:
            con.close()
        target = dest / f"market_{stamp}.duckdb"
        shutil.copy2(source, target)
        result["database"] = target

    reports = reports_dir()
    if reports.exists() and any(reports.iterdir()):
        target = dest / f"reports_{stamp}.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(reports.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(reports))
        result["reports"] = target

    for pattern in ("market_*.duckdb", "reports_*.zip"):
        copies = sorted(dest.glob(pattern))
        for old in copies[:-keep] if keep > 0 else []:
            old.unlink()
            result["removed"].append(old)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the market database and the report archive.")
    parser.add_argument("--dest", type=Path, default=None)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    args = parser.parse_args(argv)
    result = backup(dest=args.dest, keep=args.keep)
    for key in ("database", "reports"):
        print(f"{key}: {result[key] or 'nothing to back up'}")
    if result["removed"]:
        print(f"removed {len(result['removed'])} old copies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
