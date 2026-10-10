"""Encrypted SQLite connection helper.

Opens the dashboard database at `data/taxdash.db` using SQLCipher when the
`sqlcipher3` package is available. Falls back to stdlib `sqlite3` otherwise,
logging a loud warning and recording a finding so the Checklist / Readiness
surface reflects the degraded posture.
"""

from __future__ import annotations

import os
import logging
from contextlib import contextmanager
from pathlib import Path

from security.keyring import get_or_create_db_key

logger = logging.getLogger(__name__)

APP_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = APP_DIR / "data" / "taxdash.db"
SCHEMA_PATH = APP_DIR / "data" / "schema.sql"

_ENCRYPTION_ACTIVE = False
_SQLITE_MODULE = None


def _load_driver():
    global _SQLITE_MODULE, _ENCRYPTION_ACTIVE
    if _SQLITE_MODULE is not None:
        return _SQLITE_MODULE
    try:
        from pysqlcipher3 import dbapi2 as driver  # type: ignore
        _ENCRYPTION_ACTIVE = True
        logger.info("SQLCipher driver loaded (pysqlcipher3) — encryption at rest ACTIVE")
    except ImportError:
        try:
            import sqlcipher3 as driver  # type: ignore
            _ENCRYPTION_ACTIVE = True
            logger.info("SQLCipher driver loaded (sqlcipher3) — encryption at rest ACTIVE")
        except ImportError:
            import sqlite3 as driver
            _ENCRYPTION_ACTIVE = False
            logger.warning(
                "SQLCipher not installed — falling back to plaintext sqlite3. "
                "FileVault is OFF on this machine; DB is unencrypted at rest. "
                "Install `pysqlcipher3` (needs `brew install sqlcipher`)."
            )
    _SQLITE_MODULE = driver
    return driver


def encryption_active() -> bool:
    return _ENCRYPTION_ACTIVE


def _connect_raw():
    driver = _load_driver()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = driver.connect(str(DB_PATH))
    conn.row_factory = driver.Row
    if _ENCRYPTION_ACTIVE:
        key = get_or_create_db_key()
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        conn.execute("PRAGMA cipher_page_size = 4096")
        conn.execute("PRAGMA kdf_iter = 64000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connect():
    conn = _connect_raw()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if missing, set the schema version, return diagnostic info."""
    if not SCHEMA_PATH.exists():
        raise RuntimeError(f"Schema file missing at {SCHEMA_PATH}")
    schema_sql = SCHEMA_PATH.read_text()
    with connect() as conn:
        conn.executescript(schema_sql)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', '1')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('encryption_active', ?)",
            ("1" if _ENCRYPTION_ACTIVE else "0",),
        )
        # Migrations — add columns that may not exist in older schemas
        for migration in [
            "ALTER TABLE llm_budget ADD COLUMN llm_enabled INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE checklist_overrides ADD COLUMN reference_file TEXT",
            "ALTER TABLE checklist_overrides ADD COLUMN reference_amount REAL",
        ]:
            try:
                conn.execute(migration)
            except Exception:
                pass

        # Remove CHECK constraint from checklist_overrides to allow 'referenced_in' status
        try:
            # Check if constraint exists by trying a test value
            conn.execute("INSERT INTO checklist_overrides (year,category,doc_name,status) VALUES (0,'_test','_test','referenced_in')")
            conn.execute("DELETE FROM checklist_overrides WHERE year = 0 AND category = '_test'")
        except Exception:
            # Constraint blocks 'referenced_in' — rebuild table without it
            for stmt in [
                """CREATE TABLE IF NOT EXISTS checklist_overrides_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    year INTEGER NOT NULL, category TEXT NOT NULL, doc_name TEXT NOT NULL,
                    status TEXT NOT NULL, reason TEXT, reference_file TEXT, reference_amount REAL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE (year, category, doc_name))""",
                "INSERT OR IGNORE INTO checklist_overrides_new SELECT id, year, category, doc_name, status, reason, reference_file, reference_amount, created_at FROM checklist_overrides",
                "DROP TABLE checklist_overrides",
                "ALTER TABLE checklist_overrides_new RENAME TO checklist_overrides",
            ]:
                conn.execute(stmt)
                pass  # Column already exists
    logger.info(
        f"DB initialized at {DB_PATH} (encryption_active={_ENCRYPTION_ACTIVE})"
    )
    return {
        "db_path": str(DB_PATH),
        "encryption_active": _ENCRYPTION_ACTIVE,
    }


def query(sql: str, params: tuple = ()):
    with connect() as conn:
        return list(conn.execute(sql, params).fetchall())


def execute(sql: str, params: tuple = ()):
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid
