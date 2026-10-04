"""Second line of defence for PII: mask anything that looks like personal data in output.

The SQL gate already keeps PII columns out of results. This pass exists so that a mistake in that
layer, or personal data typed into free text, still does not reach the screen or a saved report.
It is pattern based, so it is a backstop and not the primary control; in production the same hook
calls a managed inspection service (see docs/DECISIONS.md).
"""

from __future__ import annotations

import re
from collections import Counter

import pandas as pd

# Street suffixes that are also ordinary words (Drive, Way, Place, Court) are left out on purpose:
# masking "3 Key Factors Drive Growth" would damage a report more than it protects anyone.
_STREET = (
    "Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Parkway|Pkwy|Highway|Hwy|Terrace|Trail"
)

# The patterns are deliberately narrow: a report is full of numbers, and masking a price or an
# order count would do more harm than a missed match, which the SQL gate has already prevented.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
    # Needs separators between the groups, and may not start inside a number such as 1.555.
    "phone": re.compile(
        r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?!\w)"
    ),
    # A house number, one to three capitalised words, then a street suffix.
    "street_address": re.compile(rf"\b\d{{1,5}}\s+(?:[A-Z][\w'.-]*\s+){{1,3}}(?:{_STREET})\b\.?"),
    # Two numbers with at least four decimals each: a pair of prices never looks like this.
    "coordinates": re.compile(r"-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}"),
    "card_number": re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
}


def scrub_text(text: str) -> tuple[str, dict[str, int]]:
    """Return the masked text and how many values of each kind were masked."""
    hits: Counter[str] = Counter()
    for kind, pattern in _PATTERNS.items():
        # The replacement says what was removed, so a masked answer still reads sensibly.
        text, count = pattern.subn(f"[{kind.replace('_', ' ')} removed]", text)
        if count:
            hits[kind] += count
    return text, dict(hits)


def scrub_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Mask personal data in every text cell of a result set."""
    hits: Counter[str] = Counter()

    def clean(value: object) -> object:
        if not isinstance(value, str):
            return value
        masked, found = scrub_text(value)
        hits.update(found)
        return masked

    # Numbers and dates cannot hold a name or an address, so only text columns are scanned.
    text_columns = frame.select_dtypes(include=["object", "string"]).columns
    if len(text_columns) == 0:
        return frame, {}
    cleaned = frame.copy()  # the caller's frame is left as it was
    for column in text_columns:
        cleaned[column] = cleaned[column].map(clean)
    return cleaned, dict(hits)
