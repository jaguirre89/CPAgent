"""Audit log — every mutating route and every LLM call leaves a trace.

Payloads are hashed (SHA-256) and never stored raw, per CC7.2 + P6.1. The
`@audited` decorator is the ergonomic entry point for Flask routes; `log_action`
is the low-level call for non-HTTP actors (file watcher, scheduled jobs,
LLM coach).
"""

from __future__ import annotations

import json
import hashlib
import logging
from functools import wraps
from typing import Any

import db

logger = logging.getLogger(__name__)


def _hash(payload: Any) -> str:
    if payload is None:
        return ""
    try:
        raw = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload, sort_keys=True, default=str).encode()
    except Exception:
        raw = str(payload).encode()
    return hashlib.sha256(raw).hexdigest()


def log_action(actor: str, action: str, target: str, payload: Any = None, result: str = "ok", ip: str = "") -> int:
    try:
        return db.execute(
            "INSERT INTO audit_log (actor, action, target, payload_hash, result, ip) VALUES (?, ?, ?, ?, ?, ?)",
            (actor, action, target, _hash(payload), result, ip),
        )
    except Exception as e:
        logger.warning(f"audit log_action failed: {e}")
        return -1


def audited(action: str, target: str | None = None):
    """Decorator for Flask routes. Records the action after the handler returns."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                from flask import request
                ip = request.remote_addr or ""
                body = request.get_json(silent=True) if request.method != "GET" else None
            except Exception:
                ip = ""
                body = None
            result_status = "ok"
            try:
                out = fn(*args, **kwargs)
                return out
            except Exception as e:
                result_status = f"error:{type(e).__name__}"
                raise
            finally:
                log_action(
                    actor="user",
                    action=action,
                    target=target or fn.__name__,
                    payload=body,
                    result=result_status,
                    ip=ip,
                )
        return wrapper
    return decorator


def recent(limit: int = 100) -> list:
    rows = db.query(
        "SELECT id, timestamp, actor, action, target, payload_hash, result, ip "
        "FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]
