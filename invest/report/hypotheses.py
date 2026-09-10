"""The hypothesis register: ``config/hypotheses.md`` parsed into dated entries.

Each hypothesis is a level-two heading followed by a bullet list of fields
(``Status``, ``Next review``, ``Measurement``, ``Data``, ``Caveat``, ``Outcome``).
The report lists the ones due for review with their current readings, so the
reading of the evidence does not drift with the mood of the week.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from invest.paths import CONFIG_DIR

HYPOTHESES_PATH = CONFIG_DIR / "hypotheses.md"
_HEADING = re.compile(r"^##\s+(?P<id>H\d+)\.\s*(?P<title>.+?)\s*$")
_FIELD = re.compile(r"^-\s+\*\*(?P<key>[A-Za-z ]+):\*\*\s*(?P<value>.*)$")


@dataclass
class Hypothesis:
    id: str
    title: str
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.fields.get("Status", "open").strip().lower()

    @property
    def next_review(self) -> date | None:
        raw = self.fields.get("Next review", "").strip()
        try:
            return date.fromisoformat(raw[:10]) if raw else None
        except ValueError:
            return None

    @property
    def indicators(self) -> list[str]:
        raw = self.fields.get("Indicators", "")
        return [x.strip() for x in raw.split(",") if x.strip()]

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, **self.fields}


def parse_hypotheses(text: str) -> list[Hypothesis]:
    out: list[Hypothesis] = []
    current: Hypothesis | None = None
    for line in text.splitlines():
        heading = _HEADING.match(line.strip())
        if heading:
            current = Hypothesis(heading.group("id"), heading.group("title"))
            out.append(current)
            continue
        if current is None:
            continue
        matched = _FIELD.match(line.strip())
        if matched:
            current.fields[matched.group("key").strip()] = matched.group("value").strip()
    ids = [h.id for h in out]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate hypothesis ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    return out


def load_hypotheses(path: Path = HYPOTHESES_PATH) -> list[Hypothesis]:
    if not path.exists():
        return []
    return parse_hypotheses(path.read_text(encoding="utf-8"))


def due(hypotheses: list[Hypothesis], on: date) -> list[Hypothesis]:
    """Open hypotheses whose next review is on or before ``on`` (or has no date)."""
    return [h for h in hypotheses if h.status == "open" and (h.next_review is None or h.next_review <= on)]
