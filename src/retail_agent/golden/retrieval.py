"""Finds past analyst work that resembles the question being asked.

The prototype matches on shared words, which needs no extra service and no model calls. The design
replaces this with an embedding index; the interface (`find_similar`) stays the same.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_STOPWORDS = frozenset(
    "a an and are as at by did do does for from how i in is it me my of on or our show tell that "
    "the their this to us was we what which who why with you your".split()
)


@dataclass(frozen=True)
class Trio:
    name: str
    question: str
    sql: str
    report: str
    tags: tuple[str, ...] = ()


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOPWORDS and len(w) > 2}


def load_trios(directory: str | Path) -> list[Trio]:
    trios = []
    for path in sorted(Path(directory).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        trios.append(
            Trio(
                path.stem,
                data["question"],
                data["sql"],
                data["report"],
                tuple(data.get("tags", [])),
            )
        )
    return trios


def find_similar(question: str, trios: list[Trio], limit: int = 2) -> list[Trio]:
    asked = _words(question)
    scored = []
    for trio in trios:
        known = _words(trio.question) | set(trio.tags)
        overlap = len(asked & known)
        if overlap:
            scored.append((overlap / len(asked | known), trio.name, trio))
    return [trio for _, _, trio in sorted(scored, reverse=True)[:limit]]
