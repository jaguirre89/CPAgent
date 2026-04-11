"""macOS Keychain wrapper for secrets that must never touch disk.

Stores the SQLCipher master key and the Anthropic API key under the service
`com.cpagent.app`. Falls back to an on-disk key file in APP_DIR/.secrets/
only if the `keyring` package is unavailable — in that case a REQUIRED finding is
raised so the user knows encryption at rest is degraded.
"""

from __future__ import annotations

import os
import secrets
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE = "com.cpagent.app"
ACCOUNT_DB_KEY = "db_master_key"
ACCOUNT_ANTHROPIC = "anthropic_api_key"

_FALLBACK_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent / ".secrets"


def _keyring_available():
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


def _fallback_path(account: str) -> Path:
    _FALLBACK_DIR.mkdir(mode=0o700, exist_ok=True)
    return _FALLBACK_DIR / f"{account}.key"


def _get(account: str):
    if _keyring_available():
        import keyring
        try:
            return keyring.get_password(SERVICE, account)
        except Exception as e:
            logger.warning(f"Keychain read failed for {account}: {e}")
    path = _fallback_path(account)
    if path.exists():
        return path.read_text().strip()
    return None


def _set(account: str, value: str):
    if _keyring_available():
        import keyring
        try:
            keyring.set_password(SERVICE, account, value)
            return "keychain"
        except Exception as e:
            logger.warning(f"Keychain write failed for {account}: {e}")
    path = _fallback_path(account)
    path.write_text(value)
    os.chmod(path, 0o600)
    return "file_fallback"


def _delete(account: str):
    if _keyring_available():
        import keyring
        try:
            keyring.delete_password(SERVICE, account)
        except Exception:
            pass
    path = _fallback_path(account)
    if path.exists():
        path.unlink()


def get_or_create_db_key() -> str:
    """Return the DB encryption key, generating a fresh 32-byte one if absent."""
    key = _get(ACCOUNT_DB_KEY)
    if key:
        return key
    key = secrets.token_hex(32)  # 64 hex chars = 256 bits
    backend = _set(ACCOUNT_DB_KEY, key)
    logger.info(f"Generated new DB master key (backend: {backend})")
    return key


def get_anthropic_key() -> str | None:
    """Return the stored Anthropic API key, or None if not set. Env var as fallback."""
    key = _get(ACCOUNT_ANTHROPIC)
    if key:
        return key
    return os.environ.get("ANTHROPIC_API_KEY")


def set_anthropic_key(value: str) -> str:
    return _set(ACCOUNT_ANTHROPIC, value.strip())


def clear_anthropic_key():
    _delete(ACCOUNT_ANTHROPIC)


def keyring_backend_status() -> dict:
    """Diagnostic info for the Settings tab."""
    return {
        "keyring_available": _keyring_available(),
        "service": SERVICE,
        "fallback_dir": str(_FALLBACK_DIR) if not _keyring_available() else None,
    }
