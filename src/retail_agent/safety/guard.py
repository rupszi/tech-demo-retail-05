"""First, cheap check on what the user typed, before any model call is made.

These rules stop the obvious cases (prompt injection, requests for personal data, probing for
secrets, clearly unrelated requests) without spending tokens. They are not what keeps data safe:
that is the SQL gate and the scrubber, which hold even if a message gets past this file. Subtler
off-topic requests are caught by the intent router, which understands meaning.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MAX_INPUT_CHARS = 4000

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_PEOPLE = r"(?:customers?|users?|buyers?|shoppers?|clients?|people)"
_PII = (
    r"(?:names?|e-?mails?(?: addresse?s?)?|phone(?: number)?s?|addresse?s?"
    r"|contact (?:details|info\w*)|postal codes?|zip codes?|coordinates)"
)

_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "prompt_injection",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b.{0,40}\b(?:instructions?|rules?|prompts?"
            r"|guidelines?|restrictions?|polic(?:y|ies))\b"
            r"|\b(?:reveal|show|print|repeat|display|output|tell me|what (?:is|are))\b.{0,40}"
            r"\b(?:system|hidden|initial|developer|original)\b.{0,15}\b(?:prompt|instructions?"
            r"|message)\b"
            r"|\byou are (?:now|no longer)\b"
            r"|\b(?:pretend|act|behave|role-?play)\b.{0,20}\b(?:as|to be|like)\b.{0,40}\b(?:admin"
            r"\w*|root|developer|dba|unrestricted|another user|a different user)\b"
            r"|\b(?:jailbreak|dan mode|developer mode|god mode|sudo mode)\b"
            r"|\b(?:bypass|disable|turn off|circumvent|get around)\b.{0,30}\b(?:safety|filters?"
            r"|guardrails?|restrictions?|masking|redaction|access controls?|permissions?)\b"
        ),
    ),
    (
        "secrets_probe",
        re.compile(
            r"\b(?:api[ _-]?keys?|passwords?|credentials|access tokens?|service account"
            r"|environment variables?)\b|(?<!\w)\.env\b"
        ),
    ),
    (
        "pii_request",
        re.compile(
            rf"\b{_PII}\s+(?:of|for)\s+(?:(?:the|our|all|top|these|those|each|every|\d+)\s+)*"
            rf"{_PEOPLE}\b"
            rf"|\b{_PEOPLE}(?:'s|s')?\s+(?:(?:real|full|first|last|home|street|e-?mail|phone"
            rf"|contact|personal)\s+)*{_PII}\b"
            r"|\b(?:list|show|give|export|dump|send)\b.{0,40}\b(?:e-?mail addresse?s?|e-?mails"
            r"|phone numbers?|home addresse?s?|street addresse?s?)\b"
        ),
    ),
    (
        "off_topic",
        re.compile(
            r"\b(?:write|compose|tell|sing)\b.{0,20}\b(?:poem|song|story|joke|essay|limerick"
            r"|haiku|lyrics)\b|\b(?:recipe|weather forecast|horoscope|lottery numbers)\b"
        ),
    ),
]

_MESSAGES = {
    "too_long": "That message is too long for me to process. Please shorten it and try again.",
    "prompt_injection": (
        "I can't change how I work or share my instructions. "
        "I can help with questions about sales, customers and products."
    ),
    "secrets_probe": "I can't share system configuration or credentials.",
    "pii_request": (
        "I can't show personal details such as names, emails or addresses. "
        "I can identify customers by their customer ID and show their age, gender and location."
    ),
    "off_topic": "I can only help with analysis of our retail data: sales, customers and products.",
}


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    category: str | None = None
    message: str | None = None  # what to tell the user when blocked


def _normalise(text: str) -> str:
    """Fold look-alike characters and spacing so simple obfuscation does not slip through."""
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    return re.sub(r"\s+", " ", text).strip().lower()


def check_input(text: str) -> GuardResult:
    if len(text) > MAX_INPUT_CHARS:
        return GuardResult(False, "too_long", _MESSAGES["too_long"])
    normalised = _normalise(text)
    for category, pattern in _RULES:
        if pattern.search(normalised):
            return GuardResult(False, category, _MESSAGES[category])
    return GuardResult(True)
