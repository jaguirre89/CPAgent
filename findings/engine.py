"""Findings engine — stable fingerprints, delta detection, persistence.

The parsers populate `findings` with dicts like
`{topic, priority, finding, source_file?}`. The engine normalizes them into
`Finding` dataclass instances, assigns a stable fingerprint, diffs the current
set against `findings_history`, and emits transition events
(severity changes + RESOLVED for disappeared fingerprints).

RESOLVED entries are held for 7 days after the resolution timestamp so the
CoWork-style summary can show "was BLOCKED, now RESOLVED".
"""

from __future__ import annotations

import json
import hashlib
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Iterable

import db

logger = logging.getLogger(__name__)

SEVERITIES = ("BLOCKED", "REQUIRED", "REVIEW", "RESOLVED", "LOGGED")
RESOLVED_TTL_DAYS = 7

LEGACY_MAP = {
    "BLOCKING": "BLOCKED",
    "CRITICAL": "REQUIRED",
    "HIGH": "REQUIRED",
    "MEDIUM": "REVIEW",
    "LOW": "REVIEW",
    "INFO": "LOGGED",
}


def normalize_severity(value: str | None) -> str:
    if not value:
        return "LOGGED"
    v = value.upper().strip()
    if v in SEVERITIES:
        return v
    return LEGACY_MAP.get(v, "LOGGED")


@dataclass
class Finding:
    fingerprint: str
    severity: str
    topic: str
    title: str
    body: str = ""
    source_file: str = ""
    year: int = 0
    previous_severity: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["priority"] = self.severity  # backwards-compat with existing frontend
        d["finding"] = self.body or self.title  # old shape
        extras = d.pop("extras", None) or {}
        for k, v in extras.items():
            d.setdefault(k, v)
        return d


def _fingerprint(topic: str, subject: str, source_file: str = "") -> str:
    raw = f"{topic}||{subject}||{source_file}".lower()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


_CORE_FIELDS = {"topic", "priority", "severity", "finding", "title", "body", "source_file"}


def raw_to_finding(raw: dict, year: int) -> Finding:
    topic = raw.get("topic", "General")
    subject = raw.get("finding") or raw.get("title") or raw.get("body") or ""
    source_file = raw.get("source_file", "")
    extras = {k: v for k, v in raw.items() if k not in _CORE_FIELDS}
    return Finding(
        fingerprint=_fingerprint(topic, subject, source_file),
        severity=normalize_severity(raw.get("priority") or raw.get("severity")),
        topic=topic,
        title=subject[:200],
        body=subject,
        source_file=source_file,
        year=year,
        extras=extras,
    )


def compute_findings(parsed_findings: Iterable[dict], year: int) -> list[Finding]:
    return [raw_to_finding(r, year) for r in parsed_findings]


def load_previous(year: int) -> dict[str, dict]:
    """Return a dict of fingerprint -> row for the given year, excluding RESOLVED that aged out."""
    cutoff = (datetime.utcnow() - timedelta(days=RESOLVED_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db.query(
        "SELECT * FROM findings_history WHERE year = ? AND (resolved_at IS NULL OR resolved_at > ?)",
        (year, cutoff),
    )
    return {r["fingerprint"]: dict(r) for r in rows}


def diff_and_persist(current: list[Finding], year: int) -> dict:
    """Persist current findings, detect transitions, return summary."""
    previous = load_previous(year)
    current_by_fp = {f.fingerprint: f for f in current}

    new_entries = []
    severity_changes = []
    resolved = []
    unchanged = 0

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Collect fingerprints that were user-dismissed — don't re-activate them
    dismissed_fps = set()

    # Upsert current findings
    for fp, finding in current_by_fp.items():
        prev = previous.get(fp)
        if prev is None:
            # Check if this was previously dismissed by the user
            dismissed = db.query(
                "SELECT 1 FROM findings_history WHERE fingerprint = ? AND year = ? AND severity = 'RESOLVED' AND resolved_at IS NOT NULL",
                (fp, year),
            )
            if dismissed:
                dismissed_fps.add(fp)
                continue
            db.execute(
                """INSERT INTO findings_history
                   (fingerprint, year, severity, previous_severity, topic, title, body, source_file, first_seen, last_seen)
                   VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
                (fp, year, finding.severity, finding.topic, finding.title, finding.body, finding.source_file, now, now),
            )
            finding.first_seen = now
            finding.last_seen = now
            new_entries.append(finding)
        else:
            # If previously RESOLVED by user (has resolved_at), don't re-activate
            if prev["severity"] == "RESOLVED" and prev.get("resolved_at"):
                dismissed_fps.add(fp)
                continue
            finding.first_seen = prev["first_seen"]
            finding.last_seen = now
            if prev["severity"] != finding.severity:
                db.execute(
                    """UPDATE findings_history
                       SET previous_severity = ?, severity = ?, last_seen = ?, resolved_at = NULL
                       WHERE fingerprint = ? AND year = ?""",
                    (prev["severity"], finding.severity, now, fp, year),
                )
                finding.previous_severity = prev["severity"]
                severity_changes.append(finding)
            else:
                db.execute(
                    "UPDATE findings_history SET last_seen = ? WHERE fingerprint = ? AND year = ?",
                    (now, fp, year),
                )
                unchanged += 1

    # Mark absent fingerprints as RESOLVED
    for fp, prev in previous.items():
        if fp in current_by_fp:
            continue
        if prev["severity"] == "RESOLVED":
            continue
        db.execute(
            """UPDATE findings_history
               SET previous_severity = severity, severity = 'RESOLVED', resolved_at = ?, last_seen = ?
               WHERE fingerprint = ? AND year = ?""",
            (now, now, fp, year),
        )
        ghost = Finding(
            fingerprint=fp,
            severity="RESOLVED",
            topic=prev["topic"],
            title=prev["title"],
            body=prev["body"] or "",
            source_file=prev["source_file"] or "",
            year=year,
            previous_severity=prev["severity"],
            first_seen=prev["first_seen"],
            last_seen=now,
        )
        resolved.append(ghost)

    # Filter out user-dismissed findings from current output
    active_current = [f for f in current if f.fingerprint not in dismissed_fps]

    transitions = severity_changes + resolved
    summary = _format_summary(new_entries, transitions, unchanged)
    return {
        "current": [f.to_dict() for f in active_current],
        "transitions": [t.to_dict() for t in transitions],
        "new": [f.to_dict() for f in new_entries],
        "resolved": [f.to_dict() for f in resolved],
        "summary": summary,
        "counts": _bucket(active_current),
    }


def _bucket(findings: list[Finding]) -> dict:
    buckets = {s: 0 for s in SEVERITIES}
    for f in findings:
        buckets[f.severity] = buckets.get(f.severity, 0) + 1
    return buckets


def _format_summary(new_entries: list[Finding], transitions: list[Finding], unchanged: int) -> str:
    n = len(new_entries) + len(transitions)
    if not n and unchanged == 0:
        return "No findings yet."
    if not n:
        return f"{unchanged} findings unchanged."
    return f"Updated findings ({n} new entries)"


def recent_history(year: int, limit: int = 50) -> list[dict]:
    rows = db.query(
        "SELECT * FROM findings_history WHERE year = ? ORDER BY last_seen DESC LIMIT ?",
        (year, limit),
    )
    return [dict(r) for r in rows]
