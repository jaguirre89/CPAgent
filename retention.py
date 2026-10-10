"""Retention purge for findings_history, audit_log, llm_suggestions.

Runs at startup. Window configurable per-table via app_config.json:
    {"retention": {"findings_history_days": 730, "audit_log_days": 730, "llm_suggestions_days": 730}}
"""

from __future__ import annotations

import logging

import db

logger = logging.getLogger(__name__)

DEFAULTS = {
    "findings_history_days": 730,
    "audit_log_days": 730,
    "llm_suggestions_days": 730,
}

TABLES = {
    "findings_history_days": ("findings_history", "last_seen"),
    "audit_log_days": ("audit_log", "timestamp"),
    "llm_suggestions_days": ("llm_suggestions", "created_at"),
}


def purge(config: dict | None = None) -> dict:
    cfg = (config or {}).get("retention", {})
    totals = {}
    for key, (table, col) in TABLES.items():
        days = int(cfg.get(key, DEFAULTS[key]))
        try:
            before = db.query(f"SELECT COUNT(*) as n FROM {table}")[0]["n"]
            db.execute(
                f"DELETE FROM {table} WHERE {col} < datetime('now', ?)",
                (f"-{days} days",),
            )
            after = db.query(f"SELECT COUNT(*) as n FROM {table}")[0]["n"]
            totals[table] = before - after
        except Exception as e:
            logger.warning(f"retention purge failed for {table}: {e}")
            totals[table] = 0
    if any(totals.values()):
        logger.info(f"retention purge deleted: {totals}")
    return totals
