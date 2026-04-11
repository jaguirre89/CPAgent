"""Persistent parsed-year cache.

Stores the full parsed-year dict in the encrypted SQLite DB, keyed by year.
A lightweight signature based on file mtimes of the year folder is used to
invalidate the cache when any source file changes. This lets the server skip
the expensive parse on startup and on every hot-path request.
"""

from __future__ import annotations

import os
import json
import hashlib
import logging
from pathlib import Path

import db

logger = logging.getLogger(__name__)


def _source_signature(year_path: str) -> str:
    """Compute a stable signature of all source files in the year folder.

    Based on relative path + mtime + size of every file below `year_path`.
    Any edit / new file / delete changes the signature deterministically.
    """
    if not os.path.isdir(year_path):
        return "missing"
    hasher = hashlib.sha256()
    entries = []
    for root, dirs, files in os.walk(year_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.startswith("."):
                continue
            full = os.path.join(root, f)
            try:
                st = os.stat(full)
                rel = os.path.relpath(full, year_path)
                entries.append(f"{rel}|{int(st.st_mtime)}|{st.st_size}")
            except OSError:
                continue
    entries.sort()
    for e in entries:
        hasher.update(e.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()[:32]


def get_cached(year: int, year_path: str) -> dict | None:
    """Return cached parsed data if the on-disk signature still matches."""
    rows = db.query("SELECT data_json, source_signature FROM parsed_cache WHERE year = ?", (year,))
    if not rows:
        return None
    current_sig = _source_signature(year_path)
    if rows[0]["source_signature"] != current_sig:
        logger.info(f"parsed_cache stale for year {year} — signature changed")
        return None
    try:
        return json.loads(rows[0]["data_json"])
    except Exception as e:
        logger.warning(f"parsed_cache decode failed for {year}: {e}")
        return None


def set_cached(year: int, year_path: str, data: dict):
    """Persist parsed data with a fresh source signature."""
    try:
        sig = _source_signature(year_path)
        payload = json.dumps(data, default=str)
        db.execute(
            """INSERT INTO parsed_cache (year, data_json, source_signature, cached_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(year) DO UPDATE SET
                 data_json = excluded.data_json,
                 source_signature = excluded.source_signature,
                 cached_at = datetime('now')""",
            (year, payload, sig),
        )
        logger.info(f"parsed_cache saved year {year} ({len(payload)} bytes)")
    except Exception as e:
        logger.warning(f"parsed_cache save failed for {year}: {e}")


def invalidate(year: int | None = None):
    if year is None:
        db.execute("DELETE FROM parsed_cache")
    else:
        db.execute("DELETE FROM parsed_cache WHERE year = ?", (year,))


def warm_all() -> list[int]:
    """Return the list of years with a valid cache entry — used at startup
    logging so you can see what's preloaded without hitting any routes."""
    rows = db.query("SELECT year FROM parsed_cache ORDER BY year DESC")
    return [r["year"] for r in rows]
