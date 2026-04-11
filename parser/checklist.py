"""Document checklist — auto-generated from folder contents vs expected documents."""

import os
import re
import logging

logger = logging.getLogger(__name__)

# Default expected documents per folder category
DEFAULT_EXPECTED = {
    "Personal": [
        {"doc": "Social Security / ID Documentation", "pattern": r"(?i)(ssn|social|id|identity)", "required": False},
        {"doc": "Prior Year AGI", "pattern": r"(?i)(agi|prior.?year.?agi)", "required": False},
    ],
    "Spouse Income": [
        {"doc": "W-2", "pattern": r"(?i)w.?2", "required": True, "count": 1},
    ],
    "Vehicle": [
        {"doc": "Mileage Log", "pattern": r"(?i)(mileage|mile)", "required": False},
        {"doc": "Vehicle Registration", "pattern": r"(?i)(registration|title)", "required": False},
    ],
    "Home Office": [
        {"doc": "Lease or Mortgage Statement", "pattern": r"(?i)(lease|mortgage|rent)", "required": False},
        {"doc": "Square Footage Record", "pattern": r"(?i)(square|sqft|footage)", "required": False},
    ],
    "Investments": [
        {"doc": "1099-B (Brokerage)", "pattern": r"(?i)1099.?b", "required": False},
        {"doc": "1099-DIV", "pattern": r"(?i)1099.?div", "required": False},
        {"doc": "1099-INT", "pattern": r"(?i)1099.?int", "required": False},
    ],
    "Crypto": [
        {"doc": "Exchange CSV Export", "pattern": r"(?i)(crypto|exchange|coinbase|binance)", "required": False},
        {"doc": "1099-DA", "pattern": r"(?i)1099.?da", "required": False},
    ],
    "Contractors 1099s": [
        {"doc": "1099-NEC Forms Issued", "pattern": r"(?i)(1099|nec)", "required": False, "count": "any"},
    ],
    "Tax Payments": [
        {"doc": "Estimated Payment Records (1040-ES)", "pattern": r"(?i)(1040.?es|estimated|payment)", "required": False},
    ],
    "Prior Returns": [
        {"doc": "Prior Year Federal Return", "pattern": r"(?i)(tax.?return|1040)", "required": True, "count": 1},
    ],
}

# Category keywords to match folder names
FOLDER_CATEGORIES = {
    "personal": "Personal",
    "wife": "Spouse Income",
    "spouse": "Spouse Income",
    "vehicle": "Vehicle",
    "home_office": "Home Office",
    "home office": "Home Office",
    "investment": "Investments",
    "crypto": "Crypto",
    "contractor": "Contractors 1099s",
    "1099": "Contractors 1099s",
    "tax_payment": "Tax Payments",
    "tax payment": "Tax Payments",
    "prior_return": "Prior Returns",
    "prior return": "Prior Returns",
    "final_worksheet": None,  # skip
    "final worksheet": None,
}


def _classify_folder(folder_name):
    """Classify a folder by its name into a category."""
    name_lower = folder_name.lower()
    # Strip leading number prefix like "01_" or "05_"
    stripped = re.sub(r"^\d+[_\s]+", "", name_lower)

    for keyword, category in FOLDER_CATEGORIES.items():
        if keyword in stripped:
            return category
    return None


def _get_entity_expected_docs(entity_config):
    """Generate expected documents list for an entity."""
    docs = []
    name = entity_config.get("name", "Unknown")
    acct_system = entity_config.get("accounting_system", "Other")

    # P&L report
    docs.append({
        "doc": f"Profit & Loss Report ({acct_system})",
        "pattern": r"(?i)(profit.?and.?loss|p.?&.?l|income.?statement).*\.csv$",
        "required": True,
        "count": 1,
    })

    # Bank statements for each configured bank account
    for bank in entity_config.get("bank_accounts", []):
        bank_name = bank.get("name", "Bank")
        institution = bank.get("institution", "")
        docs.append({
            "doc": f"{bank_name} Statements ({institution})",
            "pattern": r"(?i)(wellsfargo|wells.?fargo|wf|bank|statement).*\.pdf$" if "wells fargo" in institution.lower()
                       else r"(?i)(bank|statement|checking).*\.pdf$",
            "required": True,
            "count": 12,
            "type": "monthly",
        })

    # Credit card statements
    for cc in entity_config.get("credit_cards", []):
        cc_name = cc.get("name", "Credit Card")
        institution = cc.get("institution", "")
        if "citi" in institution.lower():
            pattern = r"(?i)citi.*\.pdf$"
        elif "apple" in institution.lower():
            pattern = r"(?i)(apple|gsbank).*\.pdf$"
        else:
            pattern = r"(?i)(cc|credit.?card|statement).*\.pdf$"
        docs.append({
            "doc": f"{cc_name} Statements ({institution})",
            "pattern": pattern,
            "required": True,
            "count": 12,
            "type": "monthly",
        })

    # 1099s received
    docs.append({
        "doc": "1099 Forms Received",
        "pattern": r"(?i)1099.*\.pdf$",
        "required": False,
        "count": "any",
    })

    return docs


def generate_checklist(year_path, config):
    """Generate document checklist by scanning year folder."""
    categories = []
    total_expected = 0
    total_found = 0
    findings = []

    if not os.path.isdir(year_path):
        return {"categories": [], "total_expected": 0, "total_found": 0, "completion_pct": 0, "findings": []}

    # Discover all subfolders
    subfolders = sorted([d for d in os.listdir(year_path) if os.path.isdir(os.path.join(year_path, d))])
    entities = config.get("entities", [])

    # Process entity folders
    for entity in entities:
        folder_name = entity.get("folder_name", "")
        entity_path = os.path.join(year_path, folder_name)
        if not os.path.isdir(entity_path):
            findings.append({
                "topic": "Missing Entity Folder",
                "priority": "REQUIRED",
                "finding": f"Folder not found for {entity['name']}: {folder_name}",
            })
            continue

        expected_docs = _get_entity_expected_docs(entity)
        files_in_folder = os.listdir(entity_path)
        items = []

        for doc_spec in expected_docs:
            pattern = doc_spec["pattern"]
            expected_count = doc_spec.get("count", 1)
            matching_files = [f for f in files_in_folder if re.search(pattern, f)]

            if expected_count == "any":
                status = "FOUND" if matching_files else "N/A"
                count_ok = True
            elif isinstance(expected_count, int):
                count_ok = len(matching_files) >= expected_count
                if matching_files and count_ok:
                    status = "FOUND"
                elif matching_files:
                    status = "PARTIAL"
                else:
                    status = "MISSING"
            else:
                status = "FOUND" if matching_files else "MISSING"
                count_ok = bool(matching_files)

            if status == "FOUND":
                total_found += 1
            total_expected += 1 if expected_count != "any" else 0

            items.append({
                "doc": doc_spec["doc"],
                "status": status,
                "files": matching_files[:5],  # limit display
                "count_expected": expected_count,
                "count_found": len(matching_files),
                "required": doc_spec.get("required", False),
            })

            # Generate findings for missing required docs
            if status == "MISSING" and doc_spec.get("required"):
                findings.append({
                    "topic": "Missing Document",
                    "priority": "REQUIRED",
                    "finding": f"{entity['name']}: {doc_spec['doc']} not found in {folder_name}/.",
                })
            elif status == "PARTIAL":
                findings.append({
                    "topic": "Incomplete Documents",
                    "priority": "REVIEW",
                    "finding": f"{entity['name']}: {doc_spec['doc']} — found {len(matching_files)} of {expected_count} expected.",
                })

        categories.append({
            "name": entity["name"],
            "folder": folder_name,
            "items": items,
        })

    # Process non-entity folders
    entity_folders = {e.get("folder_name", "") for e in entities}
    for folder in subfolders:
        if folder in entity_folders:
            continue
        if folder.startswith("."):
            continue

        category = _classify_folder(folder)
        if category is None:
            continue

        expected_docs = DEFAULT_EXPECTED.get(category, [])
        folder_path = os.path.join(year_path, folder)
        files_in_folder = os.listdir(folder_path)
        items = []

        for doc_spec in expected_docs:
            pattern = doc_spec["pattern"]
            expected_count = doc_spec.get("count", 1)
            matching_files = [f for f in files_in_folder if re.search(pattern, f)]

            if expected_count == "any":
                status = "FOUND" if matching_files else "N/A"
            elif matching_files:
                status = "FOUND" if len(matching_files) >= (expected_count if isinstance(expected_count, int) else 1) else "PARTIAL"
            else:
                status = "MISSING" if doc_spec.get("required") else "NEEDED"

            if status == "FOUND":
                total_found += 1
            if expected_count != "any":
                total_expected += 1

            items.append({
                "doc": doc_spec["doc"],
                "status": status,
                "files": matching_files[:5],
                "count_expected": expected_count,
                "count_found": len(matching_files),
                "required": doc_spec.get("required", False),
            })

            if status == "MISSING" and doc_spec.get("required"):
                findings.append({
                    "topic": "Missing Document",
                    "priority": "REQUIRED",
                    "finding": f"{category}: {doc_spec['doc']} not found in {folder}/.",
                })

        if items:
            categories.append({
                "name": category,
                "folder": folder,
                "items": items,
            })

    completion_pct = round(total_found / total_expected * 100, 1) if total_expected > 0 else 0

    return {
        "categories": categories,
        "total_expected": total_expected,
        "total_found": total_found,
        "completion_pct": completion_pct,
        "findings": findings,
    }
