"""Categorization rules store backed by SQLite.

Wraps the `categorization_rules` table. Seeds the 50+ hardcoded
`ACCOUNT_TO_SCHEDULE_C` mappings from `parser.csv_parser` on first run, then
serves all subsequent lookups from the DB so user corrections and LLM-suggested
rules are persistent and indistinguishable from seed rules at runtime.
"""

from __future__ import annotations

import re
import logging
from typing import Optional

import db

logger = logging.getLogger(__name__)

_CACHE = None  # type: ignore


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "pattern_type": row["pattern_type"],
        "pattern": row["pattern"],
        "schedule_c_category": row["schedule_c_category"],
        "entity_scope": row["entity_scope"],
        "confidence": row["confidence"],
        "source": row["source"],
        "reasoning": row["reasoning"],
        "created_at": row["created_at"],
    }


def invalidate_cache():
    global _CACHE
    _CACHE = None


def load_rules(force: bool = False) -> list[dict]:
    global _CACHE
    if _CACHE is None or force:
        rows = db.query(
            "SELECT * FROM categorization_rules ORDER BY "
            "CASE source WHEN 'user' THEN 0 WHEN 'llm_suggested' THEN 1 ELSE 2 END, "
            "length(pattern) DESC, id ASC"
        )
        _CACHE = [_row_to_dict(r) for r in rows]
    return _CACHE


def add_rule(
    pattern_type: str,
    pattern: str,
    schedule_c_category: Optional[str],
    source: str = "user",
    entity_scope: Optional[str] = None,
    confidence: float = 1.0,
    reasoning: Optional[str] = None,
) -> int:
    if pattern_type not in ("account_name", "memo", "vendor"):
        raise ValueError(f"Invalid pattern_type: {pattern_type}")
    if source not in ("seed", "user", "llm_suggested"):
        raise ValueError(f"Invalid source: {source}")
    rule_id = db.execute(
        """INSERT INTO categorization_rules
           (pattern_type, pattern, schedule_c_category, entity_scope, confidence, source, reasoning)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(pattern_type, pattern, entity_scope) DO UPDATE SET
             schedule_c_category = excluded.schedule_c_category,
             confidence = excluded.confidence,
             source = excluded.source,
             reasoning = excluded.reasoning,
             updated_at = datetime('now')""",
        (pattern_type, pattern, schedule_c_category, entity_scope, confidence, source, reasoning),
    )
    invalidate_cache()
    return rule_id


def delete_rule(rule_id: int):
    db.execute("DELETE FROM categorization_rules WHERE id = ?", (rule_id,))
    invalidate_cache()


def match(
    account_name: Optional[str] = None,
    memo: Optional[str] = None,
    vendor: Optional[str] = None,
    entity_scope: Optional[str] = None,
) -> Optional[dict]:
    """Return the best matching rule for the given signals, or None.

    Preference order:
      1. Exact match on account_name (entity-scoped first, then global)
      2. Exact match on vendor
      3. Substring match on memo
    Rules with source='user' outrank 'llm_suggested' which outrank 'seed'
    at the same specificity (enforced by load_rules ORDER BY).
    """
    rules = load_rules()
    if account_name:
        normalized = _normalize(account_name)
        for r in rules:
            if r["pattern_type"] != "account_name":
                continue
            if entity_scope and r["entity_scope"] and r["entity_scope"] != entity_scope:
                continue
            if _normalize(r["pattern"]) == normalized:
                return r
        stripped = _strip_qbo_code(account_name)
        if stripped and stripped != account_name:
            for r in rules:
                if r["pattern_type"] == "account_name" and _normalize(r["pattern"]) == _normalize(stripped):
                    return r
    if vendor:
        for r in rules:
            if r["pattern_type"] == "vendor" and _normalize(r["pattern"]) == _normalize(vendor):
                return r
    if memo:
        memo_low = memo.lower()
        for r in rules:
            if r["pattern_type"] == "memo" and r["pattern"].lower() in memo_low:
                return r
    return None


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _strip_qbo_code(account_name: str) -> Optional[str]:
    m = re.match(r"^\d{4}\s+(.+)", account_name or "")
    return m.group(1) if m else None


def seed_from_hardcoded(force: bool = False) -> int:
    """Import ACCOUNT_TO_SCHEDULE_C into categorization_rules with source='seed'.

    Idempotent: only inserts rules that don't already exist. Returns the number
    of rules inserted on this call.
    """
    from parser.csv_parser import ACCOUNT_TO_SCHEDULE_C
    inserted = 0
    for account_name, category in ACCOUNT_TO_SCHEDULE_C.items():
        existing = db.query(
            "SELECT id FROM categorization_rules "
            "WHERE pattern_type = 'account_name' AND pattern = ? AND entity_scope IS NULL",
            (account_name,),
        )
        if existing and not force:
            continue
        try:
            db.execute(
                """INSERT INTO categorization_rules
                   (pattern_type, pattern, schedule_c_category, entity_scope, confidence, source, reasoning)
                   VALUES ('account_name', ?, ?, NULL, 1.0, 'seed', 'Imported from csv_parser.ACCOUNT_TO_SCHEDULE_C')
                   ON CONFLICT(pattern_type, pattern, entity_scope) DO NOTHING""",
                (account_name, category),
            )
            inserted += 1
        except Exception as e:
            logger.warning(f"Seed insert failed for {account_name}: {e}")
    if inserted:
        invalidate_cache()
        logger.info(f"Seeded {inserted} rules from ACCOUNT_TO_SCHEDULE_C")
    return inserted


def count_by_source() -> dict:
    rows = db.query(
        "SELECT source, COUNT(*) as n FROM categorization_rules GROUP BY source"
    )
    return {r["source"]: r["n"] for r in rows}
