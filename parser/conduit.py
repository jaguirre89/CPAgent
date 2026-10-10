"""Conduit / pass-through income modeling.

Handles the case where gross receipts include money that passes through
the filer's Stripe (or other processor) but belongs to a counterparty.

Primary use case: Cressio collects Example Client sponsor payments through
its Stripe, keeps its service fees, and pays Example Client the remainder
via checks. The Stripe deposits hit the bank as "STRIPE TRANSFER ST-xxx".

This module:
1. Loads/saves conduit relationships from the DB
2. Parses the pass-through CSV to build daily payout expectations
3. Matches uncategorized GL "stripe transfer" transactions against
   date-windowed sums from the CSV
4. Scans 1099-NEC PDFs for issued 1099s
5. Cross-references to auto-classify matching transactions
"""

from __future__ import annotations

import csv
import os
import re
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import db

logger = logging.getLogger(__name__)

# Stripe daily payouts typically arrive T+2
PAYOUT_DELAY_DAYS = 2
AMOUNT_TOLERANCE = 1.50  # dollars


# ─── DB operations ────────────────────────────────────────────────────────────

def load_conduit_relationships(year: int) -> list[dict]:
    """Load all conduit relationships for a given year."""
    rows = db.query(
        "SELECT * FROM conduit_relationships WHERE year = ? ORDER BY counterparty_name",
        (year,),
    )
    return [dict(r) for r in rows]


def save_conduit_relationship(data: dict) -> int:
    """Save or update a conduit relationship. Returns the row id."""
    row_id = db.execute(
        """INSERT OR REPLACE INTO conduit_relationships
           (counterparty_name, entity_scope, memo_pattern, treatment,
            linked_1099_amount, linked_1099_file, expected_annual_amount,
            year, notes, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            data["counterparty_name"],
            data.get("entity_scope"),
            data.get("memo_pattern"),
            data["treatment"],
            data.get("linked_1099_amount"),
            data.get("linked_1099_file"),
            data.get("expected_annual_amount"),
            data.get("year"),
            data.get("notes"),
        ),
    )
    return row_id


def delete_conduit_relationship(rel_id: int) -> None:
    db.execute("DELETE FROM conduit_relationships WHERE id = ?", (rel_id,))


# ─── CSV parsing ──────────────────────────────────────────────────────────────

def parse_passthrough_csv(csv_path: str, exclude_emails: list[str] | None = None) -> dict:
    """Parse a Stripe pass-through CSV and build daily net totals.

    Returns {
        transactions: [{date, client, email, sub_total, fees, net, product}],
        daily_totals: {date_str: total_net},
        total_gross, total_fees, total_net, count
    }
    """
    exclude = set(e.lower() for e in (exclude_emails or []))
    transactions = []
    daily_totals = defaultdict(float)

    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                email = (row.get("Client Email") or "").strip().lower()
                if email in exclude:
                    continue

                try:
                    net = float(row.get("Total") or 0)
                except (ValueError, TypeError):
                    net = 0
                if net <= 0:
                    continue

                try:
                    sub_total = float(row.get("Sub-Total") or 0)
                    fees = float(row.get("Fees") or 0)
                except (ValueError, TypeError):
                    sub_total = net
                    fees = 0

                date_str = (row.get("Date") or "").strip()
                try:
                    dt = datetime.strptime(date_str, "%m/%d/%Y")
                    date_key = dt.strftime("%Y-%m-%d")
                except ValueError:
                    date_key = date_str

                transactions.append({
                    "date": date_key,
                    "client": (row.get("Client Name") or "").strip(),
                    "email": email,
                    "sub_total": sub_total,
                    "fees": fees,
                    "net": net,
                    "product": (row.get("Product") or "").strip(),
                })
                daily_totals[date_key] += net

    except Exception as e:
        logger.warning(f"Failed to parse pass-through CSV {csv_path}: {e}")
        return {"transactions": [], "daily_totals": {}, "total_gross": 0,
                "total_fees": 0, "total_net": 0, "count": 0, "error": str(e)}

    return {
        "transactions": transactions,
        "daily_totals": dict(daily_totals),
        "total_gross": round(sum(t["sub_total"] for t in transactions), 2),
        "total_fees": round(sum(t["fees"] for t in transactions), 2),
        "total_net": round(sum(t["net"] for t in transactions), 2),
        "count": len(transactions),
    }


def build_payout_windows(daily_totals: dict[str, float], delay_days: int = PAYOUT_DELAY_DAYS) -> list[dict]:
    """Build expected Stripe payout amounts with date windows.

    Stripe batches charges and pays out ~T+2. For each day with charges,
    we create a window: [charge_date + delay - 1, charge_date + delay + 1].
    We also create cumulative windows for multi-day batches.
    """
    windows = []
    for date_str, total in sorted(daily_totals.items()):
        try:
            charge_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        payout_center = charge_date + timedelta(days=delay_days)
        windows.append({
            "charge_date": date_str,
            "expected_amount": round(total, 2),
            "payout_window_start": (payout_center - timedelta(days=1)).strftime("%Y-%m-%d"),
            "payout_window_end": (payout_center + timedelta(days=1)).strftime("%Y-%m-%d"),
        })

    return windows


# ─── Stripe-GL matching ──────────────────────────────────────────────────────

def match_stripe_transactions(
    gl_stripe_groups: list[dict],
    csv_data: dict,
    year: int,
) -> dict:
    """Match uncategorized GL 'stripe transfer' transactions against the CSV.

    gl_stripe_groups: groups from the GL reconciler with memo_pattern containing 'stripe'
    csv_data: output of parse_passthrough_csv()

    Returns {
        matched: [{gl_group, csv_match_info, confidence}],
        unmatched_gl: [gl_groups with no CSV match],
        unmatched_csv: [CSV daily totals with no GL match],
        summary: {matched_amount, unmatched_amount, csv_total}
    }
    """
    daily_totals = csv_data.get("daily_totals", {})
    windows = build_payout_windows(daily_totals)

    matched = []
    unmatched_gl = []
    used_windows = set()

    for group in gl_stripe_groups:
        amount = abs(group.get("total", 0))
        if amount <= 0:
            continue

        # Try to find a matching payout window
        best_match = None
        best_diff = AMOUNT_TOLERANCE + 1

        for i, window in enumerate(windows):
            if i in used_windows:
                continue
            diff = abs(amount - window["expected_amount"])
            if diff < best_diff and diff <= AMOUNT_TOLERANCE:
                best_match = (i, window)
                best_diff = diff

        # Also try summing adjacent days (Stripe sometimes batches 2-3 days)
        if best_match is None:
            for i, w1 in enumerate(windows):
                if i in used_windows:
                    continue
                cumulative = w1["expected_amount"]
                batch_indices = [i]
                for j in range(i + 1, min(i + 4, len(windows))):
                    if j in used_windows:
                        continue
                    cumulative += windows[j]["expected_amount"]
                    batch_indices.append(j)
                    diff = abs(amount - cumulative)
                    if diff <= AMOUNT_TOLERANCE:
                        best_match = (batch_indices[0], {
                            "charge_date": f"{w1['charge_date']} to {windows[j]['charge_date']}",
                            "expected_amount": round(cumulative, 2),
                            "batch_days": len(batch_indices),
                        })
                        best_diff = diff
                        # Mark all batch indices as used
                        for bi in batch_indices:
                            used_windows.add(bi)
                        break
                if best_match:
                    break

        if best_match:
            idx, window = best_match
            if idx not in used_windows:  # single-day match
                used_windows.add(idx)
            confidence = max(0.5, 1.0 - (best_diff / AMOUNT_TOLERANCE))
            matched.append({
                "gl_group": group,
                "csv_match": window,
                "amount_diff": round(best_diff, 2),
                "confidence": round(confidence, 2),
                "classification": "Gross receipts",
                "tag": "Example Client pass-through income",
            })
        else:
            unmatched_gl.append(group)

    # Find CSV days that had no GL match
    unmatched_csv = []
    for i, window in enumerate(windows):
        if i not in used_windows:
            unmatched_csv.append(window)

    matched_amount = sum(abs(m["gl_group"].get("total", 0)) for m in matched)
    unmatched_amount = sum(abs(g.get("total", 0)) for g in unmatched_gl)

    return {
        "matched": matched,
        "unmatched_gl": unmatched_gl,
        "unmatched_csv": unmatched_csv,
        "summary": {
            "matched_count": len(matched),
            "matched_amount": round(matched_amount, 2),
            "unmatched_gl_count": len(unmatched_gl),
            "unmatched_gl_amount": round(unmatched_amount, 2),
            "csv_total": csv_data.get("total_net", 0),
            "csv_count": csv_data.get("count", 0),
        },
    }


# ─── 1099 scanning ────────────────────────────────────────────────────────────

def scan_1099s(year_path: str) -> list[dict]:
    """Scan 09_Contractors_1099s/ for issued 1099-NEC PDFs and extract amounts."""
    results = []
    contractors_dir = None

    if not os.path.isdir(year_path):
        return results

    for d in os.listdir(year_path):
        if re.search(r"(?i)(contractor|1099)", d):
            candidate = os.path.join(year_path, d)
            if os.path.isdir(candidate):
                contractors_dir = candidate
                break

    if not contractors_dir:
        return results

    try:
        from parser.pdf_parser import parse_pdf
    except ImportError:
        return results

    for f in sorted(os.listdir(contractors_dir)):
        if not f.lower().endswith(".pdf"):
            continue
        filepath = os.path.join(contractors_dir, f)
        parsed = parse_pdf(filepath)
        if parsed.get("type") == "1099_nec":
            results.append({
                "file": f,
                "recipient": parsed.get("recipient_name", ""),
                "amount": parsed.get("nonemployee_compensation", 0),
                "payer": parsed.get("payer_name", ""),
            })
        else:
            amt_match = re.search(r"_(\d+)(?:_\d+)?\.pdf$", f)
            name_match = re.match(r"([A-Za-z]+(?:LLC)?)", f)
            if amt_match and name_match:
                results.append({
                    "file": f,
                    "recipient": _humanize_name(name_match.group(1)),
                    "amount": float(amt_match.group(1)),
                    "payer": "",
                })

    return results


def _humanize_name(camel: str) -> str:
    """Convert 'ExampleClientLLC' to 'Example Client LLC'."""
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", camel)
    spaced = re.sub(r"(LLC|Inc|Corp)", r" \1", spaced)
    return spaced.strip()


# ─── High-level reconciliation ────────────────────────────────────────────────

def reconcile_conduits(
    gl_groups: list[dict],
    conduit_rels: list[dict],
    issued_1099s: list[dict],
    year_path: str = "",
    year: int = 0,
) -> dict:
    """Full conduit reconciliation: CSV matching + rule matching + 1099 cross-ref.

    Returns {matched, unmatched_gl, csv_summary, proposals}
    """
    all_matched = []
    remaining = list(gl_groups)

    # Phase 1: For conduits with a CSV file, do Stripe-CSV matching
    for rel in conduit_rels:
        csv_file = rel.get("linked_1099_file") or ""
        if not csv_file or not csv_file.endswith(".csv"):
            continue

        # Resolve CSV path
        csv_path = None
        if year_path and os.path.isdir(year_path):
            candidate = os.path.join(year_path, csv_file)
            if os.path.isfile(candidate):
                csv_path = candidate
            else:
                # Search recursively
                for root, dirs, files in os.walk(year_path):
                    for f in files:
                        if f == os.path.basename(csv_file):
                            csv_path = os.path.join(root, f)
                            break
                    if csv_path:
                        break

        if not csv_path:
            logger.warning(f"CSV not found for conduit {rel['counterparty_name']}: {csv_file}")
            continue

        # Parse CSV
        exclude_emails = []
        if rel.get("notes") and "exclude" in rel["notes"].lower():
            # Extract emails to exclude from notes
            exclude_emails = re.findall(r"[\w.+-]+@[\w.-]+", rel["notes"])

        csv_data = parse_passthrough_csv(csv_path, exclude_emails=exclude_emails)

        # Filter GL groups to only stripe-related ones for this entity
        memo_pattern = (rel.get("memo_pattern") or "stripe").lower()
        entity = rel.get("entity_scope") or ""
        stripe_groups = []
        non_stripe = []
        for g in remaining:
            memo = (g.get("memo_pattern") or "").lower()
            g_entity = (g.get("entity") or "")
            if memo_pattern in memo and (not entity or entity.lower() in g_entity.lower()):
                stripe_groups.append(g)
            else:
                non_stripe.append(g)

        if stripe_groups:
            result = match_stripe_transactions(stripe_groups, csv_data, year)
            for m in result["matched"]:
                m["conduit"] = rel["counterparty_name"]
            all_matched.extend(result["matched"])
            remaining = non_stripe + result["unmatched_gl"]

    # Phase 2: For conduits without CSV, do memo-pattern matching
    for rel in conduit_rels:
        if (rel.get("linked_1099_file") or "").endswith(".csv"):
            continue  # Already handled in Phase 1
        pattern = (rel.get("memo_pattern") or "").lower()
        if not pattern:
            continue

        new_remaining = []
        for g in remaining:
            memo = (g.get("memo_pattern") or "").lower()
            if pattern in memo:
                all_matched.append({
                    "gl_group": g,
                    "csv_match": None,
                    "confidence": 0.8,
                    "classification": "Gross receipts",
                    "tag": f"{rel['counterparty_name']} pass-through (memo rule)",
                    "conduit": rel["counterparty_name"],
                })
            else:
                new_remaining.append(g)
        remaining = new_remaining

    # Phase 3: Check remaining against 1099 amounts
    proposals = []
    _1099_by_recipient = defaultdict(list)
    for nec in issued_1099s:
        key = nec["recipient"].lower().replace(" ", "")
        _1099_by_recipient[key].append(nec)

    for g in remaining:
        total = abs(g.get("total", 0))
        for recipient_key, necs in _1099_by_recipient.items():
            for nec in necs:
                if abs(total - nec["amount"]) <= 1.0:
                    proposals.append({
                        "gl_group": g,
                        "matched_1099": nec,
                        "match_type": "amount_match",
                        "suggested_treatment": "book_as_contract_labor",
                    })

    return {
        "matched": all_matched,
        "unmatched_gl": remaining,
        "proposals": proposals,
        "summary": {
            "matched_count": len(all_matched),
            "matched_amount": round(sum(abs(m["gl_group"].get("total", 0)) for m in all_matched), 2),
        },
    }
