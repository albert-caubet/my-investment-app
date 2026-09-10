"""The ``release`` source: headline figures recorded by hand on release day.

ISM, the S&P Global and HCOB PMIs, the Conference Board LEI, GDPNow and the
other nowcasts publish their headline for free but license the history. The
catalog marks such series ``source = "release"`` and the numbers live in
``config/releases.toml``, one entry per print::

    [[release]]
    series = "US_ISM_MFG_PMI"
    period = "2026-08"          # the month the reading describes
    released = 2026-09-01       # the day it was published
    value = 48.7
    note = "ISM Report on Business"

The refresh job loads the file into the ``series`` table with the release date
as the vintage, so the history accrues from the first month and a backtest can
still ask what was known when. Nothing is invented: a month with no entry is a
month with no value.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from invest.data.ecb import parse_period
from invest.paths import CONFIG_DIR

RELEASES_PATH = CONFIG_DIR / "releases.toml"


@dataclass(frozen=True)
class Release:
    series_id: str
    period: date
    released: date
    value: float
    note: str = ""


def parse_releases(payload: dict) -> list[Release]:
    entries = payload.get("release") or []
    out: list[Release] = []
    for i, entry in enumerate(entries):
        try:
            series_id = str(entry["series"]).strip()
            period = parse_period(str(entry["period"]))
            released = entry["released"]
            if not isinstance(released, date):
                released = parse_period(str(released))
            value = float(entry["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"release entry {i + 1} is not usable: {exc}") from exc
        if released < period:
            raise ValueError(f"release entry {i + 1} ({series_id} {period}): released before its period")
        out.append(Release(series_id, period, released, value, str(entry.get("note") or "")))
    return out


def load_releases(path: Path = RELEASES_PATH) -> list[Release]:
    if not path.exists():
        return []
    with path.open("rb") as handle:
        return parse_releases(tomllib.load(handle))


def release_toml(release: Release) -> str:
    """One ``[[release]]`` block, as the file expects it."""
    note = release.note.replace("\\", "\\\\").replace('"', '\\"')
    lines = [
        "[[release]]",
        f'series = "{release.series_id}"',
        f'period = "{release.period.isoformat()}"',
        f"released = {release.released.isoformat()}",
        f"value = {release.value!r}",
    ]
    if note:
        lines.append(f'note = "{note}"')
    return "\n".join(lines) + "\n"


def append_release(release: Release, path: Path = RELEASES_PATH) -> None:
    """Append one entry to the releases file, creating it if needed.

    The file is re-parsed afterwards so a malformed entry is refused before it
    can break the next refresh.
    """
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    text = existing.rstrip("\n") + ("\n\n" if existing.strip() else "") + release_toml(release)
    parse_releases(tomllib.loads(text))
    path.write_text(text, encoding="utf-8")
