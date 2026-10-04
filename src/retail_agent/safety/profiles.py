"""Who is asking, and which brands they may analyse.

In production the web front end signs the user in and sends a signed JWT with every request. The
API verifies the token and passes its claims to `UserProfile.from_claims`. Nothing else decides
what a user may see: not the request body, and not the model.

The prototype has no front end, so `config/users.<backend>.json` holds sample token payloads and
the CLI picks one with `--user`. There is one file per data backend because the mock data and the
real dataset have different brands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BRAND_SCOPE = "brand:"
ALL_BRANDS = "*"


@dataclass(frozen=True)
class UserProfile:
    user_id: str
    name: str
    brands: tuple[str, ...] = ()  # the brands this user may analyse
    all_brands: bool = False  # an explicit grant, for example for the CEO

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> UserProfile:
        """Build a profile from verified token claims.

        Access is denied by default: a token with no brand scope describes a user who may see
        nothing. Seeing every brand takes the explicit scope `brand:*`. Scopes of other kinds are
        ignored, so a token issued for another purpose grants nothing here.
        """
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("The token has no subject (sub).")
        scopes = claims.get("scopes", [])
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            raise ValueError("The token's scopes must be a list of strings.")
        granted = [s[len(BRAND_SCOPE) :].strip() for s in scopes if s.startswith(BRAND_SCOPE)]
        name = claims.get("name")
        return cls(
            user_id=subject,
            name=name if isinstance(name, str) and name else subject,
            brands=tuple(dict.fromkeys(b for b in granted if b and b != ALL_BRANDS)),
            all_brands=ALL_BRANDS in granted,
        )

    def describe_scope(self) -> str:
        if self.all_brands:
            return "all brands"
        return "brands: " + ", ".join(self.brands) if self.brands else "no brands"


def load_profiles(path: str | Path) -> dict[str, UserProfile]:
    """Read sample token payloads and return a profile for each, keyed by subject."""
    payloads = json.loads(Path(path).read_text(encoding="utf-8"))["users"]
    profiles = [UserProfile.from_claims(claims) for claims in payloads]
    return {profile.user_id: profile for profile in profiles}
