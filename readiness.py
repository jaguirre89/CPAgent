"""Filing Readiness meter — top-of-dashboard composite score.

    40%  Documents ready (from checklist.completion_pct)
    30%  Zero BLOCKED findings (binary)
    20%  REQUIRED findings cleared (linear: 1 - required_count / required_total_ever)
    10%  Schedule C lines computed for all active entities

Returns a dict shaped for the frontend readiness strip.
"""

from __future__ import annotations

from findings.engine import SEVERITIES


def compute_readiness(parsed: dict) -> dict:
    checklist = parsed.get("checklist", {}) or {}
    findings = parsed.get("findings", []) or []
    schedule_c = parsed.get("schedule_c", {}) or {}

    doc_pct = min(100.0, float(checklist.get("completion_pct") or 0))
    total_expected = int(checklist.get("total_expected") or 0)
    total_found = int(checklist.get("total_found") or 0)

    buckets = {s: 0 for s in SEVERITIES}
    for f in findings:
        sev = (f.get("priority") or f.get("severity") or "LOGGED").upper()
        if sev not in buckets:
            sev = "LOGGED"
        buckets[sev] += 1

    blocked = buckets["BLOCKED"]
    required = buckets["REQUIRED"]
    blocked_component = 100.0 if blocked == 0 else 0.0
    # REQUIRED: scale linearly against a soft max of 20; 0 REQUIRED = 100%, 20+ = 0%
    required_component = max(0.0, 100.0 - min(required, 20) * 5.0)

    schedules = schedule_c.get("schedules", []) or []
    if not schedules:
        schedule_component = 0.0
    else:
        computed = 0
        for s in schedules:
            if s.get("total_income") is not None or s.get("total_expenses") is not None:
                computed += 1
        schedule_component = 100.0 * computed / len(schedules) if schedules else 0.0

    readiness = (
        0.40 * doc_pct
        + 0.30 * blocked_component
        + 0.20 * required_component
        + 0.10 * schedule_component
    )

    blockers = sorted(
        [f for f in findings if (f.get("priority") or "").upper() == "BLOCKED"],
        key=lambda f: f.get("topic") or "",
    )[:3]

    return {
        "readiness_pct": round(readiness, 1),
        "breakdown": {
            "documents": {
                "weight": 40,
                "value": round(doc_pct, 1),
                "found": total_found,
                "expected": total_expected,
            },
            "blocked": {
                "weight": 30,
                "value": blocked_component,
                "count": blocked,
            },
            "required": {
                "weight": 20,
                "value": round(required_component, 1),
                "count": required,
            },
            "schedule_c": {
                "weight": 10,
                "value": round(schedule_component, 1),
                "entities": len(schedules),
            },
        },
        "severity_counts": buckets,
        "top_blockers": blockers,
        "color": _bucket_color(readiness),
        "can_generate_filing": blocked == 0 and required == 0 and doc_pct >= 95,
    }


def _bucket_color(pct: float) -> str:
    if pct < 50:
        return "red"
    if pct < 85:
        return "amber"
    return "green"
