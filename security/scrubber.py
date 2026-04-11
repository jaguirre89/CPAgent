"""PII scrubber for outbound API calls.

Every byte sent to the Anthropic API passes through `scrub()`. The returned
tuple contains (scrubbed_text, redaction_map). The redaction map is retained
locally and used by `unscrub()` to un-tokenize the API response before display.

Redactions:
  SSN         XXX-XX-XXXX regex           -> [SSN_REDACTED]
  EIN         XX-XXXXXXX regex            -> [EIN_REDACTED]
  Credit card 13-19 digit + Luhn          -> [CC_REDACTED]
  Routing    9-digit + bank context      -> [ROUT_REDACTED]
  Account    8-17 digit + acct context   -> [ACCT_REDACTED]
  Address    USPS street regex            -> [ADDR_REDACTED]
  Entity     exact-match from config      -> Entity1, Entity2, ...
  Person     exact-match from config      -> Person1, Person2, ...

Dollar amounts and vendor names are kept (they are required for useful
categorization and are not PII on their own).
"""

from __future__ import annotations

import re
import json
from typing import Any

SSN_RE = re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
EIN_RE = re.compile(r"\b\d{2}-\d{7}\b")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
ROUTING_RE = re.compile(r"\b(?:routing|aba|rtn)[^\d]{0,10}(\d{9})\b", re.IGNORECASE)
ACCT_RE = re.compile(r"\b(?:acct|account|a/c)[^\d]{0,10}(\d{4,17})\b", re.IGNORECASE)
ADDR_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9\.\s]{2,40}\s+"
    r"(Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Circle|Cir|Place|Pl)\b",
    re.IGNORECASE,
)


def _luhn(card: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", card)]
    if len(digits) < 13:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _redact_cards(text: str) -> str:
    def _sub(m: re.Match) -> str:
        candidate = m.group(0)
        if _luhn(candidate):
            return "[CC_REDACTED]"
        return candidate
    return CARD_RE.sub(_sub, text)


def _build_name_map(config: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    idx = 1
    for e in config.get("entities", []):
        name = (e.get("name") or "").strip()
        if name:
            mapping[name] = f"Entity{idx}"
            idx += 1
    person_idx = 1
    for name_field in ("owner_name", "spouse_name"):
        val = config.get(name_field)
        if val:
            mapping[val] = f"Person{person_idx}"
            person_idx += 1
    return mapping


def scrub(payload: Any, config: dict | None = None) -> tuple[str, dict]:
    """Scrub a payload for outbound transmission.

    Accepts str, dict, or list. Returns (scrubbed_json_text, redaction_map).
    """
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    name_map = _build_name_map(config or {})
    for real, token in sorted(name_map.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(re.escape(real), token, text, flags=re.IGNORECASE)

    text = SSN_RE.sub("[SSN_REDACTED]", text)
    text = EIN_RE.sub("[EIN_REDACTED]", text)
    text = _redact_cards(text)
    text = ROUTING_RE.sub("[ROUT_REDACTED]", text)
    text = ACCT_RE.sub("[ACCT_REDACTED]", text)
    text = ADDR_RE.sub("[ADDR_REDACTED]", text)

    return text, name_map


def unscrub(text: str, name_map: dict[str, str]) -> str:
    """Restore tokens to real names in an API response — local-only."""
    for real, token in name_map.items():
        text = text.replace(token, real)
    return text


def audit_leaks(text: str) -> list[str]:
    """Return any remaining PII patterns found in scrubbed text — for unit tests."""
    leaks = []
    if SSN_RE.search(text):
        leaks.append("SSN")
    if EIN_RE.search(text):
        leaks.append("EIN")
    for m in CARD_RE.finditer(text):
        if _luhn(m.group(0)):
            leaks.append(f"CARD:{m.group(0)}")
    if ROUTING_RE.search(text):
        leaks.append("ROUTING")
    if ADDR_RE.search(text):
        leaks.append("ADDRESS")
    return leaks
