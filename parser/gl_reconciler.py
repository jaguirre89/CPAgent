"""GL-level reconciliation for uncategorized P&L buckets.

The Wave P&L rolls transactions into categories. When $17k lands in
"Uncategorized Expense", the P&L alone can't tell us what's inside — we need the
GL (`accounting.csv`) to see the individual transactions that went into that
bucket. This module:

1. Reads `accounting.csv` (Wave's detailed ledger, 12k+ rows across years)
2. Filters rows whose Account Name is "Uncategorized Expense" / "Uncategorized Income"
3. Filters to the target year
4. Groups by a normalized memo prefix (first few tokens of the description)
5. For each group, tries the `rules_store` memo/vendor match
6. Returns grouped findings — one per memo pattern, auto-resolved when a memo
   rule exists, otherwise surfaced as REQUIRED so the user can teach it once
   and reclassify all matching transactions.
"""

from __future__ import annotations

import csv
import os
import re
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

UNCATEGORIZED_ACCOUNTS = {"uncategorized expense", "uncategorized income"}

# Wave accounting GL header signature — used to discover GL exports regardless
# of filename. Wave defaults to "accounting.csv" but the export is user-namable.
GL_HEADER_SIGNATURE = {
    "Transaction ID",
    "Transaction Date",
    "Account Name",
}

# Memo-based heuristic seeds. These are NOT persisted to categorization_rules
# — they're suggestions the user confirms via the teach loop. Conservative
# by design: only patterns with high confidence.
MEMO_HEURISTICS = [
    # (regex, schedule_c_category, confidence, rationale)
    (r"\bwise\b",                                      "Contract labor",         0.85, "Wise.com transfer — typically international contractor payment"),
    (r"\bhrblock\b|\bh\s*r\s*block\b|\bhrb\b",        "Legal and professional", 0.9,  "H&R Block tax prep fees"),
    (r"\birs\b.*taxpy?mt|\bus\s*treasury\b",           None,                      1.0,  "Federal income tax payment — not Schedule C"),
    (r"\bharland\s*clarke\b",                          "Office expense",         0.95, "Check printing — office supplies"),
    (r"\bstripe\b.*\btransfer\b",                      "Gross receipts",         0.70, "Stripe payout — merchant income (verify per customer; may need per-project split)"),
    (r"\bpaypal\b.*\btransfer\b",                      "Gross receipts",         0.85, "PayPal payout — merchant income"),
    (r"\bmobile\s*deposit\b",                          "Gross receipts",         0.6,  "Mobile deposit — typically customer payment (verify)"),
    (r"\bcostco\b",                                    "Supplies",               0.6,  "Costco purchase — usually supplies (verify)"),
    (r"\bmicrosoft\b|\bm365\b|\boffice\s*365\b",       "Other expenses",         0.85, "Microsoft 365 subscription — software"),
    (r"\banthropic\b|\bopenai\b|\bchatgpt\b",          "Other expenses",         0.9,  "AI service — software"),
    (r"\baws\b|\bamazon\s*web\b",                      "Other expenses",         0.9,  "AWS hosting — software/hosting"),
    (r"\bgithub\b",                                    "Other expenses",         0.9,  "GitHub — software"),
    (r"\bgoogle\s*workspace\b|\bgsuite\b",             "Other expenses",         0.9,  "Google Workspace — software"),
    (r"\busps\b|\bfedex\b|\bups\b",                    "Other expenses",         0.85, "Shipping — postage/delivery"),
    (r"\buber\b|\blyft\b",                             "Travel",                 0.8,  "Rideshare — travel"),
    (r"\bdelta\b|\bunited\b|\bamerican\s*air\b|\bsouthwest\b", "Travel",         0.85, "Airline — travel"),
    (r"\bmarriott\b|\bhilton\b|\bhyatt\b|\bhotel\b",   "Travel",                 0.85, "Hotel — travel"),
    (r"\brestaurant\b|\bcafe\b|\bgrill\b|\bpizza\b|\bfondue\b|\bsushi\b", "Meals (50% limit)", 0.75, "Restaurant — meals"),
    (r"\bgsdc\b|\bcertification\b",                    "Other expenses",         0.7,  "Professional certification — education/training"),
    (r"\batm\s*(withdrawal|cash)\b",                   None,                      0.1,  "ATM cash — needs vendor context"),
    (r"^\s*check\s*$",                                 None,                      0.1,  "Paper check with no memo — needs context"),
]


def _normalize_memo(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    t = re.sub(r"\d{6,}", " ", t)  # strip long digit runs (txn IDs)
    t = re.sub(r"\s+", " ", t).strip()
    tokens = t.split()[:5]
    return " ".join(tokens)


def _parse_amount(row: dict) -> float:
    debit = row.get("Debit Amount (Two Column Approach)", "") or ""
    credit = row.get("Credit Amount (Two Column Approach)", "") or ""
    single = row.get("Amount (One column)", "") or ""
    def _f(s):
        s = str(s).replace("$", "").replace(",", "").strip()
        if not s:
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0
    d, c, a = _f(debit), _f(credit), _f(single)
    if d or c:
        return d - c
    return a


def _apply_heuristic(memo_lower: str):
    for pattern, category, confidence, rationale in MEMO_HEURISTICS:
        if re.search(pattern, memo_lower):
            return {"category": category, "confidence": confidence, "rationale": rationale}
    return None


def _try_rules_store(memo: str, vendor: str):
    try:
        from learning import rules_store
        hit = rules_store.match(memo=memo, vendor=vendor or None)
        if hit:
            return {
                "category": hit["schedule_c_category"],
                "confidence": hit.get("confidence", 1.0),
                "rationale": f"Matched {hit['pattern_type']} rule id={hit['id']} (source={hit['source']})",
                "rule_id": hit["id"],
            }
    except Exception as e:
        logger.debug(f"rules_store lookup failed: {e}")
    return None


def _find_gl_file(entity_path: str) -> str | None:
    """Locate a Wave-format GL export by CSV header, not by filename."""
    if not os.path.isdir(entity_path):
        return None
    candidates = []
    for name in os.listdir(entity_path):
        if not name.lower().endswith(".csv"):
            continue
        path = os.path.join(entity_path, name)
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                header = f.readline()
            if GL_HEADER_SIGNATURE.issubset({h.strip() for h in header.split(",")}):
                candidates.append((os.path.getsize(path), path))
        except Exception:
            continue
    if not candidates:
        return None
    # Pick the largest — Wave GL exports tend to be the biggest CSV in the folder
    candidates.sort(reverse=True)
    return candidates[0][1]


def reconcile_gl(entity_path: str, year: int, entity_name: str) -> dict:
    """Scan the Wave-format GL file for the entity and return per-group reconciliation proposals."""
    gl_path = _find_gl_file(entity_path)
    if not gl_path:
        return {"groups": [], "findings": [], "totals": {}, "gl_path": None}

    groups = defaultdict(lambda: {"count": 0, "total": 0.0, "samples": [], "first_date": "", "vendors": set()})
    total_uncategorized = 0.0

    try:
        with open(gl_path, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                acct = (row.get("Account Name") or "").strip().lower()
                if acct not in UNCATEGORIZED_ACCOUNTS:
                    continue
                date = (row.get("Transaction Date") or "").strip()
                if not date.startswith(str(year)):
                    continue
                memo = (
                    row.get("Transaction Description")
                    or row.get("Notes / Memo")
                    or row.get("Transaction Line Description")
                    or ""
                ).strip()
                vendor = (row.get("Vendor") or "").strip()
                amount = _parse_amount(row)
                key = _normalize_memo(memo) or "(no memo)"
                scope_key = f"{acct}::{key}"

                g = groups[scope_key]
                g["account"] = acct
                g["memo_pattern"] = key
                g["count"] += 1
                g["total"] += abs(amount)
                if vendor:
                    g["vendors"].add(vendor[:40])
                if len(g["samples"]) < 3:
                    g["samples"].append({
                        "date": date,
                        "memo": memo[:120],
                        "amount": round(amount, 2),
                        "vendor": vendor[:40],
                    })
                if not g["first_date"] or date < g["first_date"]:
                    g["first_date"] = date
                total_uncategorized += abs(amount)
    except Exception as e:
        logger.warning(f"gl_reconciler failed for {entity_name}: {e}")
        return {"groups": [], "findings": [], "totals": {}, "gl_path": gl_path, "error": str(e)}

    group_list = []
    findings = []

    for key, g in sorted(groups.items(), key=lambda kv: -kv[1]["total"]):
        g["vendors"] = sorted(g["vendors"])
        suggestion = _try_rules_store(g["memo_pattern"], next(iter(g["vendors"]), "")) or _apply_heuristic(g["memo_pattern"])
        g["suggestion"] = suggestion

        if suggestion:
            cat = suggestion.get("category")
            cat_label = cat if cat else "Excluded from Schedule C"
            severity = "REVIEW" if suggestion.get("confidence", 0) >= 0.8 else "REQUIRED"
            finding_text = (
                f"{entity_name}: {g['count']}× '{g['memo_pattern']}' transactions "
                f"(${g['total']:.2f}) in {g['account']} → suggested {cat_label} "
                f"({int(100*suggestion.get('confidence',0))}% conf). {suggestion.get('rationale','')}"
            )
        else:
            severity = "REQUIRED"
            finding_text = (
                f"{entity_name}: {g['count']}× '{g['memo_pattern']}' transactions "
                f"(${g['total']:.2f}) in {g['account']} — no memo rule. Teach a rule to reclassify."
            )

        findings.append({
            "topic": "GL Reconciliation",
            "priority": severity,
            "finding": finding_text,
            "source_file": "accounting.csv",
            "memo_pattern": g["memo_pattern"],
            "account": g["account"],
            "group_total": round(g["total"], 2),
            "group_count": g["count"],
            "samples": g["samples"],
            "suggestion": suggestion,
            "entity": entity_name,
        })
        group_list.append({
            "memo_pattern": g["memo_pattern"],
            "account": g["account"],
            "count": g["count"],
            "total": round(g["total"], 2),
            "first_date": g["first_date"],
            "vendors": g["vendors"],
            "suggestion": suggestion,
            "samples": g["samples"],
        })

    return {
        "groups": group_list,
        "findings": findings,
        "totals": {
            "total_uncategorized": round(total_uncategorized, 2),
            "group_count": len(group_list),
        },
        "gl_path": gl_path,
    }
