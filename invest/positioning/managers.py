"""The 13F filers to track, from ``config/managers.toml``."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from invest.paths import CONFIG_DIR

MANAGERS_PATH = CONFIG_DIR / "managers.toml"


@dataclass(frozen=True)
class Manager:
    cik: int
    name: str


def parse_managers(payload: dict) -> list[Manager]:
    out = []
    for i, entry in enumerate(payload.get("manager") or []):
        try:
            out.append(Manager(int(entry["cik"]), str(entry["name"]).strip()))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"manager entry {i + 1} is not usable: {exc}") from exc
    ciks = [m.cik for m in out]
    if len(set(ciks)) != len(ciks):
        raise ValueError("duplicate manager CIKs")
    return out


def load_managers(path: Path = MANAGERS_PATH) -> list[Manager]:
    if not path.exists():
        return []
    with path.open("rb") as handle:
        return parse_managers(tomllib.load(handle))
