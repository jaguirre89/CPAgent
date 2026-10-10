"""Amazon invoice parser — extracts line items from Amazon order detail PDFs.

When an invoice has >1 item, it needs LLM classification to determine which
items are business-deductible vs personal. Single-item invoices are assumed
to be business (since the user placed them in the Deductible folder).
"""

from __future__ import annotations

import os
import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False


def parse_amazon_invoice(filepath: str) -> dict:
    """Parse an Amazon order detail PDF and extract line items.

    Returns {
        order_number, order_date, grand_total, items: [{name, price, seller}],
        item_count, needs_llm_review: bool
    }
    """
    result = {
        "file": os.path.basename(filepath),
        "type": "amazon_invoice",
        "status": "PARTIAL",
        "order_number": None,
        "order_date": None,
        "grand_total": None,
        "items": [],
        "item_count": 0,
        "needs_llm_review": False,
    }

    if not HAS_PDFPLUMBER:
        result["status"] = "FAILED"
        result["error"] = "pdfplumber not installed"
        return result

    try:
        with pdfplumber.open(filepath) as pdf:
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"

        if not full_text.strip():
            result["status"] = "FAILED"
            result["error"] = "No text extracted"
            return result

        # Extract order metadata
        order_match = re.search(r"Order\s*#\s*([\d\-]+)", full_text)
        if order_match:
            result["order_number"] = order_match.group(1)

        date_match = re.search(r"Order placed\s+(\w+ \d{1,2},?\s*\d{4})", full_text)
        if date_match:
            result["order_date"] = date_match.group(1)

        total_match = re.search(r"Grand Total:\s*\$?([\d,]+\.\d{2})", full_text)
        if total_match:
            result["grand_total"] = float(total_match.group(1).replace(",", ""))

        # Extract line items
        # Pattern: item description spans 1-3 lines, followed by "Sold by:" line,
        # then optionally "Supplied by:" and "Return window", then a price line
        items = _extract_items(full_text)
        result["items"] = items
        result["item_count"] = len(items)
        result["needs_llm_review"] = len(items) > 1
        result["status"] = "COMPLETE" if items else "PARTIAL"

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)

    return result


def _extract_items(text: str) -> list[dict]:
    """Extract individual items from Amazon order text."""
    items = []
    lines = text.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Look for "Sold by:" as the anchor — the item name is above it, price is below
        if line.startswith("Sold by:"):
            seller = line.replace("Sold by:", "").strip()

            # Collect item name from preceding lines (up to 3 lines back, stopping
            # at metadata lines)
            name_parts = []
            j = i - 1
            while j >= 0 and len(name_parts) < 4:
                prev = lines[j].strip()
                # Stop at lines that are clearly not item names
                if any(prev.startswith(kw) for kw in [
                    "Sold by", "Supplied by", "Return window", "Back to top",
                    "Delivered", "Your package", "$", "Grand Total", "Gift Card",
                    "CO Retail", "Estimated tax", "Total before", "Shipping",
                    "Free Shipping", "Item(s)", "Order placed", "Ship to",
                    "Conditions", "©", "http", "Earns",
                ]):
                    break
                if re.match(r"^\$[\d,]+\.\d{2}$", prev):
                    break
                name_parts.insert(0, prev)
                j -= 1

            item_name = " ".join(name_parts).strip()
            if not item_name:
                i += 1
                continue

            # Look for price after "Sold by" (skip "Supplied by", "Return window" lines)
            price = None
            k = i + 1
            while k < len(lines) and k < i + 5:
                price_line = lines[k].strip()
                price_match = re.match(r"^\$?([\d,]+\.\d{2})$", price_line)
                if price_match:
                    price = float(price_match.group(1).replace(",", ""))
                    break
                k += 1

            items.append({
                "name": item_name,
                "price": price,
                "seller": seller,
            })

        i += 1

    return items


def scan_amazon_folder(entity_path: str) -> list[dict]:
    """Scan for Amazon invoice folders and parse all invoices."""
    results = []
    if not os.path.isdir(entity_path):
        return results

    for folder in os.listdir(entity_path):
        if "amazon" not in folder.lower():
            continue
        folder_path = os.path.join(entity_path, folder)
        if not os.path.isdir(folder_path):
            continue

        for f in sorted(os.listdir(folder_path)):
            if not f.lower().endswith(".pdf"):
                continue
            parsed = parse_amazon_invoice(os.path.join(folder_path, f))
            parsed["source_folder"] = folder
            results.append(parsed)

    return results


def build_amazon_summary(invoices: list[dict]) -> dict:
    """Summarize Amazon invoices: total, single-item vs multi-item breakdown."""
    single_item = [inv for inv in invoices if inv["item_count"] == 1]
    multi_item = [inv for inv in invoices if inv["item_count"] > 1]
    needs_review = [inv for inv in invoices if inv.get("needs_llm_review")]

    single_total = sum(inv["grand_total"] or 0 for inv in single_item)
    multi_total = sum(inv["grand_total"] or 0 for inv in multi_item)

    return {
        "total_invoices": len(invoices),
        "single_item_count": len(single_item),
        "single_item_total": round(single_total, 2),
        "multi_item_count": len(multi_item),
        "multi_item_total": round(multi_total, 2),
        "needs_llm_review": len(needs_review),
        "combined_total": round(single_total + multi_total, 2),
        "invoices": invoices,
    }
