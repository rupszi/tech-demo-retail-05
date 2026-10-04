"""Who is asking, and which products they may analyse.

In production the identity comes from SSO and the product scope from an entitlements service;
here both come from a JSON file per data backend (`config/users.<backend>.json`), because the mock
data and the real dataset have different brands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UserProfile:
    user_id: str
    name: str
    brands: tuple[str, ...] | None = None  # None = no brand restriction
    departments: tuple[str, ...] | None = None  # None = no department restriction

    @property
    def is_unrestricted(self) -> bool:
        return self.brands is None and self.departments is None

    def describe_scope(self) -> str:
        parts = []
        if self.brands is not None:
            parts.append("brands: " + (", ".join(self.brands) or "none"))
        if self.departments is not None:
            parts.append("departments: " + (", ".join(self.departments) or "none"))
        return "; ".join(parts) or "all products"


def _tuple_or_none(value: list[str] | None) -> tuple[str, ...] | None:
    return None if value is None else tuple(value)


def load_profiles(path: str | Path) -> dict[str, UserProfile]:
    data = json.loads(Path(path).read_text())
    return {
        u["user_id"]: UserProfile(
            user_id=u["user_id"],
            name=u["name"],
            brands=_tuple_or_none(u.get("brands")),
            departments=_tuple_or_none(u.get("departments")),
        )
        for u in data["users"]
    }
