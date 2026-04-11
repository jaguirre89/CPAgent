"""Detect unknown cards/accounts from GL + bank transaction memos.

When the GL shows an outbound payment to "APPLECARD GS BANK" but no Apple Card
account is configured in `app_config.json`, we flag that as a REQUIRED finding
and persist the discovery in `unknown_accounts`. The Dismiss action suppresses
re-flagging for future parses.
"""

from __future__ import annotations

import re
import logging
from collections import defaultdict

import db

logger = logging.getLogger(__name__)

ISSUER_PATTERNS = [
    ("Apple Card / Goldman Sachs", re.compile(r"\b(apple\s?card|gs\s?bank|goldman\s*sachs)\b", re.IGNORECASE)),
    ("Amazon Store Card / Synchrony", re.compile(r"\b(amazon.*(card|prime)|synchrony\s*bank|syncb)\b", re.IGNORECASE)),
    ("Chase Card",                 re.compile(r"\bchase\s*(card|bank\s*card|epay|credit)\b", re.IGNORECASE)),
    ("American Express",           re.compile(r"\b(amex|american\s*express|ae\s*epay|americanexp)\b", re.IGNORECASE)),
    ("Capital One",                re.compile(r"\bcap(ital)?\s*one\b", re.IGNORECASE)),
    ("Discover Card",              re.compile(r"\bdiscover\s*(card|e[- ]?pay|bank)?\b", re.IGNORECASE)),
    ("Barclay Card",               re.compile(r"\bbarclay\w*\s*(card|bank)?\b", re.IGNORECASE)),
    ("Wells Fargo Card",           re.compile(r"\bwells\s*fargo\s*(card|credit)\b", re.IGNORECASE)),
    ("Citi Card",                  re.compile(r"\b(citibank|citi\s*(card|cc|business))\b", re.IGNORECASE)),
    ("Bank of America Card",       re.compile(r"\b(bank\s*of\s*america|bofa)\s*(card|cc|credit)\b", re.IGNORECASE)),
    ("US Bank Card",               re.compile(r"\bus\s*bank\s*(card|credit|nat)\b", re.IGNORECASE)),
    ("PNC Card",                   re.compile(r"\bpnc\s*(card|credit|bank)\b", re.IGNORECASE)),
    ("Target REDcard",             re.compile(r"\btarget\s*(red|card|credit)\b", re.IGNORECASE)),
    ("Walmart Card",               re.compile(r"\bwalmart\s*(card|credit|mc)\b", re.IGNORECASE)),
    ("Costco Visa / Citi",         re.compile(r"\bcostco\s*(anywhere|visa|card)\b", re.IGNORECASE)),
    ("Navy Federal Card",          re.compile(r"\bnavy\s*(fed|federal)\s*(card|credit)?\b", re.IGNORECASE)),
    ("USAA Card",                  re.compile(r"\busaa\s*(card|credit)\b", re.IGNORECASE)),
    # Generic fallback: any "CARD" autopay/epay/payment to a 4-digit suffix that
    # isn't caught above. Captures things like "AUTOPAY FOO CARD 1234".
    ("Generic unknown card",       re.compile(r"\b(?:card|cc)\s*(?:payment|pmt|epay|pymt|autopay|online)\b|\bautopay\s+\w+\s+(?:card|cc)\s*\d{3,4}\b", re.IGNORECASE)),
]


def _known_account_names(config: dict) -> set[str]:
    known = set()
    for entity in config.get("entities", []):
        for bank in entity.get("bank_accounts", []):
            for key in ("name", "institution"):
                if bank.get(key):
                    known.add(bank[key].lower())
        for cc in entity.get("credit_cards", []):
            for key in ("name", "institution"):
                if cc.get(key):
                    known.add(cc[key].lower())
    return known


def _dismissed_names() -> set[str]:
    rows = db.query("SELECT detected_name FROM unknown_accounts WHERE status = 'dismissed'")
    return {r["detected_name"] for r in rows}


def detect_unknown_accounts(parsed: dict, config: dict) -> dict:
    """Scan parsed transactions for card issuer patterns not in the configured accounts."""
    known = _known_account_names(config)
    dismissed = _dismissed_names()
    hits = defaultdict(lambda: {"count": 0, "total_amount": 0.0, "samples": []})

    transactions = _collect_transaction_memos(parsed)
    for memo, amount in transactions:
        for issuer_name, pattern in ISSUER_PATTERNS:
            if not pattern.search(memo):
                continue
            if any(issuer_name.lower().startswith(k) or k in issuer_name.lower() for k in known):
                continue
            if issuer_name in dismissed:
                continue
            bucket = hits[issuer_name]
            bucket["count"] += 1
            bucket["total_amount"] += abs(amount)
            if len(bucket["samples"]) < 3:
                bucket["samples"].append(memo[:120])

    findings = []
    for issuer_name, agg in hits.items():
        _upsert_unknown(issuer_name, agg)
        findings.append({
            "topic": "Unknown Card Detected",
            "priority": "REQUIRED",
            "finding": (
                f"Unknown card detected: {issuer_name} — ${agg['total_amount']:.2f} "
                f"paid across {agg['count']} transactions. No statements in folder. "
                f"Upload statements or dismiss via the Unknown Accounts card."
            ),
            "source_file": "gl_inference",
            "detected_name": issuer_name,
            "evidence_count": agg["count"],
            "evidence_amount": round(agg["total_amount"], 2),
            "samples": agg["samples"],
        })

    return {"unknown_accounts": [dict(detected_name=k, **v) for k, v in hits.items()], "findings": findings}


def _collect_transaction_memos(parsed: dict):
    """Yield (memo_text, amount) tuples from wherever parsed data holds transactions."""
    # Wave accounting GL export may be exposed via entity_data or schedule_c
    schedule_c = parsed.get("schedule_c", {}) or {}
    for sched in schedule_c.get("schedules", []) or []:
        for line in sched.get("income_lines", []) or []:
            yield str(line.get("description") or ""), float(line.get("amount") or 0)
        for line in sched.get("expense_lines", []) or []:
            yield str(line.get("description") or ""), float(line.get("amount") or 0)
        for acc in sched.get("unmapped_accounts", []) or []:
            yield str(acc.get("account_name") or ""), float(acc.get("amount") or 0)
    # Bank recon statements may have memos buried under accounts/months
    for acct in (parsed.get("bank_recon", {}) or {}).get("accounts", []):
        for month in acct.get("months", []) or []:
            memo = str(month.get("source_file") or "")
            if memo:
                yield memo, 0.0


def _upsert_unknown(name: str, agg: dict):
    existing = db.query("SELECT id FROM unknown_accounts WHERE detected_name = ?", (name,))
    if existing:
        db.execute(
            """UPDATE unknown_accounts
               SET evidence_count = ?, evidence_amount = ?, last_seen = datetime('now'),
                   status = CASE WHEN status = 'dismissed' THEN 'dismissed' ELSE 'pending' END
               WHERE detected_name = ?""",
            (agg["count"], round(agg["total_amount"], 2), name),
        )
    else:
        db.execute(
            """INSERT INTO unknown_accounts (detected_name, evidence_count, evidence_amount, status)
               VALUES (?, ?, ?, 'pending')""",
            (name, agg["count"], round(agg["total_amount"], 2)),
        )


def list_unknown() -> list[dict]:
    rows = db.query("SELECT * FROM unknown_accounts ORDER BY id DESC")
    return [dict(r) for r in rows]


def set_status(detected_name: str, status: str):
    if status not in ("pending", "dismissed", "resolved"):
        raise ValueError(f"invalid status: {status}")
    db.execute(
        "UPDATE unknown_accounts SET status = ?, last_seen = datetime('now') WHERE detected_name = ?",
        (status, detected_name),
    )
