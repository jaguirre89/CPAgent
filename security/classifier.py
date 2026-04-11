"""Data classification registry.

Seeds the `data_classification` table on startup with the SOC2 / PII mapping
from the plan. The registry is advisory — the enforcement is in `scrubber.py`.
"""

from __future__ import annotations

import logging

import db

logger = logging.getLogger(__name__)

CLASSIFICATIONS = [
    ("parsed_data.transactions.ssn", "pii", "Social Security Number"),
    ("parsed_data.transactions.ein", "pii", "Employer Identification Number"),
    ("parsed_data.transactions.bank_account_number", "pii", "Full bank account number"),
    ("parsed_data.transactions.credit_card_number", "pii", "Full card number"),
    ("parsed_data.transactions.routing_number", "pii", "ACH routing number"),
    ("parsed_data.transactions.street_address", "pii", "Street address"),
    ("parsed_data.entities.legal_name", "confidential", "Entity legal name (tokenized before outbound)"),
    ("parsed_data.entities.owner_name", "pii", "Owner / spouse name (tokenized before outbound)"),
    ("parsed_data.transactions.vendor_name", "internal", "Vendor name — kept for useful categorization"),
    ("parsed_data.transactions.memo", "internal", "Transaction memo — scanned for PII patterns before outbound"),
    ("parsed_data.transactions.amount", "internal", "Dollar amount"),
    ("parsed_data.transactions.date", "internal", "Transaction date — truncated to month on outbound"),
    ("parsed_data.schedule_c.line", "public", "IRS Schedule C line number"),
]


def seed_classifications():
    inserted = 0
    for path, level, notes in CLASSIFICATIONS:
        try:
            db.execute(
                "INSERT OR IGNORE INTO data_classification (field_path, classification, notes) VALUES (?, ?, ?)",
                (path, level, notes),
            )
            inserted += 1
        except Exception as e:
            logger.warning(f"classification seed failed for {path}: {e}")
    return inserted


def get_level(field_path: str) -> str | None:
    rows = db.query(
        "SELECT classification FROM data_classification WHERE field_path = ?",
        (field_path,),
    )
    return rows[0]["classification"] if rows else None
