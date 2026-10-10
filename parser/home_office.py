"""Home office deduction calculator.

Supports two IRS methods:
1. Simplified: $5 per sq ft of office space, max 300 sq ft ($1,500 max).
2. Regular (actual expenses): (office_sqft / total_sqft) × (rent + utilities + insurance + internet).

Handles mid-year moves by pro-rating each period.
"""

from __future__ import annotations

import os
import re
import logging
from datetime import datetime, date
from typing import Any

import db

logger = logging.getLogger(__name__)

# IRS simplified method constants
SIMPLIFIED_RATE_PER_SQFT = 5.00
SIMPLIFIED_MAX_SQFT = 300


def load_home_office_periods(year: int) -> list[dict]:
    """Load all home office periods for a year, sorted by start date."""
    rows = db.query(
        "SELECT * FROM home_office_periods WHERE year = ? ORDER BY start_date",
        (year,),
    )
    return [dict(r) for r in rows]


def save_home_office_period(data: dict) -> int:
    """Save or update a home office period. Returns the row id."""
    row_id = db.execute(
        """INSERT OR REPLACE INTO home_office_periods
           (year, address, lease_file, start_date, end_date,
            total_sqft, office_sqft, monthly_rent, monthly_utilities,
            monthly_insurance, monthly_internet, method, notes, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            data["year"],
            data["address"],
            data.get("lease_file"),
            data["start_date"],
            data["end_date"],
            data.get("total_sqft"),
            data.get("office_sqft"),
            data.get("monthly_rent"),
            data.get("monthly_utilities"),
            data.get("monthly_insurance"),
            data.get("monthly_internet"),
            data.get("method", "simplified"),
            data.get("notes"),
        ),
    )
    return row_id


def delete_home_office_period(period_id: int) -> None:
    db.execute("DELETE FROM home_office_periods WHERE id = ?", (period_id,))


def _months_in_range(start_str: str, end_str: str, year: int) -> float:
    """Calculate the number of months a period overlaps with the tax year.
    Supports partial months (pro-rated to days).
    """
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 0

    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)

    # Clamp to tax year
    effective_start = max(start, year_start)
    effective_end = min(end, year_end)

    if effective_start > effective_end:
        return 0

    days = (effective_end - effective_start).days + 1
    return round(days / 30.44, 2)  # average days per month


def compute_deduction(period: dict, year: int) -> dict:
    """Compute the home office deduction for a single period.

    Returns {
        method, months_in_year, business_pct,
        simplified_deduction, regular_deduction, chosen_deduction,
        breakdown: {rent, utilities, insurance, internet, total_expenses}
    }
    """
    method = period.get("method", "simplified")
    months = _months_in_range(period.get("start_date"), period.get("end_date"), year)
    office_sqft = period.get("office_sqft") or 0
    total_sqft = period.get("total_sqft") or 1  # avoid division by zero
    business_pct = round(office_sqft / total_sqft, 4) if total_sqft > 0 else 0

    # Simplified method
    capped_sqft = min(office_sqft, SIMPLIFIED_MAX_SQFT)
    simplified = round(capped_sqft * SIMPLIFIED_RATE_PER_SQFT * (months / 12), 2)

    # Regular (actual expenses) method
    rent = (period.get("monthly_rent") or 0) * months
    utilities = (period.get("monthly_utilities") or 0) * months
    insurance = (period.get("monthly_insurance") or 0) * months
    internet = (period.get("monthly_internet") or 0) * months
    total_expenses = rent + utilities + insurance + internet
    regular = round(total_expenses * business_pct, 2)

    chosen = simplified if method == "simplified" else regular

    return {
        "period_id": period.get("id"),
        "address": period.get("address", ""),
        "method": method,
        "months_in_year": months,
        "office_sqft": office_sqft,
        "total_sqft": total_sqft,
        "business_pct": round(business_pct * 100, 1),
        "simplified_deduction": simplified,
        "regular_deduction": regular,
        "chosen_deduction": chosen,
        "breakdown": {
            "rent": round(rent, 2),
            "utilities": round(utilities, 2),
            "insurance": round(insurance, 2),
            "internet": round(internet, 2),
            "total_expenses": round(total_expenses, 2),
        },
    }


def compute_total_deduction(year: int) -> dict:
    """Compute total home office deduction across all periods for the year.

    Detects mid-year moves and pro-rates automatically.
    """
    periods = load_home_office_periods(year)
    if not periods:
        return {
            "total_deduction": 0,
            "periods": [],
            "move_detected": False,
            "needs_input": True,
        }

    results = []
    for p in periods:
        result = compute_deduction(p, year)
        results.append(result)

    total = sum(r["chosen_deduction"] for r in results)
    move_detected = len(periods) > 1

    return {
        "total_deduction": round(total, 2),
        "periods": results,
        "move_detected": move_detected,
        "needs_input": False,
        "period_count": len(periods),
    }


def detect_leases_from_folder(year_path: str) -> list[dict]:
    """Scan the Home Office folder for lease files and extract metadata from filenames."""
    leases = []
    ho_folder = None

    if not os.path.isdir(year_path):
        return leases

    for d in os.listdir(year_path):
        if re.search(r"(?i)home.?office", d):
            candidate = os.path.join(year_path, d)
            if os.path.isdir(candidate):
                ho_folder = candidate
                break

    if not ho_folder:
        return leases

    for f in sorted(os.listdir(ho_folder)):
        if not f.lower().endswith(".pdf"):
            continue
        if not re.search(r"(?i)(lease|mortgage|rent)", f):
            continue

        # Try to extract address from filename
        # Patterns: "123 Example St- Signed lease-..." or "Lease Agreement 456 Sample Ave..."
        address = ""
        # Try leading address: "123 Example St-"
        addr_match = re.match(r"(\d+\s+[A-Za-z\s]+?)(?:\s*[-–])", f)
        if addr_match:
            address = addr_match.group(1).strip()
        # Try embedded address: "Lease Agreement 456 Sample Ave"
        if not address:
            addr_match = re.search(r"(\d+\s+[A-Za-z]+(?:\s+[A-Za-z]+)?(?:\s+(?:St|Ave|Dr|Blvd|Rd|Ln|Ct|Way|Pl|Cir|Hts)))", f, re.IGNORECASE)
            if addr_match:
                address = addr_match.group(1).strip()

        leases.append({
            "file": f,
            "address_hint": address,
            "path": os.path.join(ho_folder, f),
        })

    return leases
