"""CSV parsing for P&L exports, bank CSVs, and auto-detection."""

import csv
import os
import re
import hashlib
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Schedule C line mapping: account name patterns -> Schedule C line info
SCHEDULE_C_LINE_MAP = {
    # Income
    "Gross receipts": {"line": "1", "section": "income"},
    "Returns and allowances": {"line": "2", "section": "income"},
    "Other income": {"line": "6", "section": "income"},

    # Expenses
    "Advertising": {"line": "8", "section": "expenses"},
    "Car and truck expenses": {"line": "9", "section": "expenses"},
    "Commissions and fees": {"line": "10", "section": "expenses"},
    "Contract labor": {"line": "11", "section": "expenses"},
    "Depreciation": {"line": "13", "section": "expenses"},
    "Employee benefit programs": {"line": "14", "section": "expenses"},
    "Insurance (other than health)": {"line": "15", "section": "expenses"},
    "Interest (mortgage)": {"line": "16a", "section": "expenses"},
    "Interest (other)": {"line": "16b", "section": "expenses"},
    "Legal and professional": {"line": "17", "section": "expenses"},
    "Office expense": {"line": "18", "section": "expenses"},
    "Pension and profit-sharing": {"line": "19", "section": "expenses"},
    "Rent - vehicles/equipment": {"line": "20a", "section": "expenses"},
    "Rent - other": {"line": "20b", "section": "expenses"},
    "Repairs and maintenance": {"line": "21", "section": "expenses"},
    "Supplies": {"line": "22", "section": "expenses"},
    "Taxes and licenses": {"line": "23", "section": "expenses"},
    "Travel": {"line": "24a", "section": "expenses"},
    "Meals (50% limit)": {"line": "24b", "section": "expenses"},
    "Utilities": {"line": "25", "section": "expenses"},
    "Wages": {"line": "26", "section": "expenses"},
    "Other expenses": {"line": "27a", "section": "expenses"},
}

# Map Wave/QBO account names to Schedule C category names
ACCOUNT_TO_SCHEDULE_C = {
    # ─── Income accounts ────────────────────────────────────────────────────
    # This is a seed set; users should add their own account names via the
    # "Teach" UI in the Findings tab, which persists rules to SQLite and
    # makes them indistinguishable from seeds at runtime.
    "Consulting Income": "Gross receipts",
    "Sales Income": "Gross receipts",
    "Service Revenue": "Gross receipts",
    "Sales Discounts": "Returns and allowances",
    "Cashback Rewards": "Gross receipts",  # Include but flag for review (generally not taxable)
    "Uncategorized Income": None,  # flagged as BLOCKING, still included in gross receipts

    # Expense accounts
    "Accounting Fees": "Legal and professional",
    "Advertising & Promotion": "Advertising",
    "Advertising-Subscription": "Advertising",
    "Bank Service Charges": "Other expenses",
    "Business Trip - Meals": "Meals (50% limit)",
    "Business Trip - Parking": "Travel",
    "Business Trip - Rental Car & Taxi": "Travel",
    "Charitable Contribution": None,  # NOT Schedule C - goes to Schedule A
    "Computer – Hosting": "Other expenses",
    "Computer – Internet": "Utilities",
    "Computer – Software": "Other expenses",
    "Conferences & Meetings": "Other expenses",
    "Credit Card Interest": "Interest (other)",
    "Domestic Subcontractor": "Contract labor",
    "Education & Training": "Other expenses",
    "Insurance – General Liability": "Insurance (other than health)",
    "International Subcontractors": "Contract labor",
    "Management Fees": None,  # Intercompany - ELIMINATED on consolidated return
    "Meals and Entertainment": "Meals (50% limit)",
    "Merchant Account Fees": "Other expenses",  # Line 27a - Bank/Merchant fees
    "Miscellanous (Non-Tax Deductible)": None,  # excluded from Schedule C
    "Office Furniture": "Supplies",  # Line 22
    "Office Storage": "Rent - other",
    "Office Supplies": "Office expense",
    "Parking for Client/Prospect": "Other expenses",  # Line 27a - Parking
    "Postage & Delivery": "Other expenses",  # Line 27a - Postage
    "Professional Fees": "Legal and professional",
    "Rent Expense": "Rent - other",
    "Rental Car & Taxi": "Rent - vehicles/equipment",  # Line 20a
    "Repair and Maintenance-Office": "Repairs and maintenance",
    "Subscriptions": "Other expenses",
    "Telephone – Wireless": "Utilities",
    "Uncategorized Expense": None,  # flagged as BLOCKING
    "Vehicle Expenses": "Car and truck expenses",
    "Vehicle – Fuel": "Car and truck expenses",
    "Vehicle – Repairs & Maintenance": "Car and truck expenses",
    "Vehicle Insurance": "Car and truck expenses",  # Line 9 - vehicle-related

    # QBO-specific
    "2220 Telephone – Wireless": "Utilities",
    "2290 Subscriptions": "Other expenses",
    "2362 Vehicle Insurance": "Car and truck expenses",  # Line 9 - vehicle-related
    "2380 Insurance": "Insurance (other than health)",

    # Tax payments — shown on Sch C Line 23 but flagged (actually go on 1040)
    "Tax Payments": "Taxes and licenses",
    "Federal Tax Payments": "Taxes and licenses",
    "3001 Federal Tax Payments": "Taxes and licenses",
}


def detect_csv_format(filepath):
    """Detect the format of a CSV file. Returns format type and parsed headers."""
    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            lines = []
            for i, line in enumerate(f):
                lines.append(line.strip())
                if i >= 10:
                    break
    except UnicodeDecodeError:
        with open(filepath, "r", encoding="latin-1") as f:
            lines = [f.readline().strip() for _ in range(11)]

    if not lines:
        return {"format": "empty", "headers": [], "sample_rows": []}

    # QBO P&L: first line is "Profit and Loss," with trailing comma (check BEFORE Wave)
    if lines[0].strip().rstrip(",") == "Profit and Loss" and len(lines) > 4 and ",Total" in lines[4]:
        return {"format": "qbo_pl", "headers": lines, "entity_name": lines[1].rstrip(",").strip() if len(lines) > 1 else ""}

    # Wave P&L: first line is "Profit and Loss", has ACCOUNT NUMBER header
    if lines[0].startswith("Profit and Loss"):
        return {"format": "wave_pl", "headers": lines, "entity_name": lines[1].strip() if len(lines) > 1 else ""}

    # Found.com activity report
    if lines[0].startswith("Date,Description,Amount,Category"):
        return {"format": "found_activity", "headers": lines[0].split(","), "sample_rows": lines[1:6]}

    # Wave accounting export
    if lines[0].startswith("Transaction ID,Transaction Date,Account Name"):
        return {"format": "wave_accounting", "headers": lines[0].split(","), "sample_rows": lines[1:4]}

    # Wave customers/vendors/bill_items
    if lines[0].startswith("customer_name,"):
        return {"format": "wave_customers", "headers": lines[0].split(","), "sample_rows": lines[1:4]}
    if lines[0].startswith("vendor_name,"):
        return {"format": "wave_vendors", "headers": lines[0].split(","), "sample_rows": lines[1:4]}
    if lines[0].startswith("vendor,description,invoice_num"):
        return {"format": "wave_bills", "headers": lines[0].split(","), "sample_rows": lines[1:4]}

    # Wave Balance Sheet
    if lines[0].startswith("Balance Sheet"):
        return {"format": "wave_balance_sheet", "headers": lines, "entity_name": lines[1] if len(lines) > 1 else ""}

    # Unknown - return headers for mapping modal
    header_hash = hashlib.sha256(lines[0].encode()).hexdigest()[:16]
    return {
        "format": "unknown",
        "header_hash": header_hash,
        "headers": lines[0].split(",") if lines else [],
        "sample_rows": lines[1:6],
    }


def parse_dollar(val):
    """Parse a dollar string like '$1,234.56' or '($1,234.56)' or '-$1,234.56' into a float."""
    if not val or not isinstance(val, str):
        return 0.0
    val = val.strip().strip('"')
    if not val or val == "$0.00":
        return 0.0
    negative = False
    if val.startswith("(") and val.endswith(")"):
        negative = True
        val = val[1:-1]
    if val.startswith("-"):
        negative = True
        val = val[1:]
    val = val.replace("$", "").replace(",", "").strip()
    try:
        result = float(val)
        return -result if negative else result
    except ValueError:
        return 0.0


def parse_wave_pl(filepath):
    """Parse a Wave Profit and Loss CSV."""
    result = {
        "format": "wave_pl",
        "entity_name": "",
        "date_range": "",
        "income": [],
        "cogs": [],
        "expenses": [],
        "total_income": 0,
        "total_cogs": 0,
        "total_expenses": 0,
        "gross_profit": 0,
        "net_profit": 0,
        "findings": [],
        "parse_errors": [],
    }

    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        with open(filepath, "r", encoding="latin-1") as f:
            lines = f.readlines()

    if len(lines) < 6:
        result["parse_errors"].append(f"Wave P&L too short: {filepath}")
        return result

    result["entity_name"] = lines[1].strip()
    # Date range: "Date Range: 2025-01-01 to 2025-12-31"
    date_match = re.search(r"Date Range:\s*(.+)", lines[2])
    if date_match:
        result["date_range"] = date_match.group(1).strip()

    section = None  # "income", "cogs", "expenses"

    for line in lines[6:]:
        line = line.strip()
        if not line or line == '""':
            continue

        # Parse CSV fields
        reader = csv.reader([line])
        try:
            fields = next(reader)
        except StopIteration:
            continue

        if len(fields) < 3:
            continue

        acct_num = fields[0].strip()
        acct_name = fields[1].strip()
        amount_str = fields[2].strip() if len(fields) > 2 else ""

        # Detect section boundaries
        if acct_name == "Income":
            section = "income"
            continue
        elif acct_name == "Cost of Goods Sold":
            section = "cogs"
            continue
        elif acct_name == "Operating Expenses":
            section = "expenses"
            continue

        # Total lines
        if acct_name == "Total Income":
            result["total_income"] = parse_dollar(amount_str)
            section = None
            continue
        elif acct_name == "Total Cost of Goods Sold":
            result["total_cogs"] = parse_dollar(amount_str)
            section = None
            continue
        elif acct_name == "Total Operating Expenses":
            result["total_expenses"] = parse_dollar(amount_str)
            section = None
            continue
        elif acct_name == "Gross Profit":
            result["gross_profit"] = parse_dollar(amount_str)
            continue
        elif acct_name == "Net Profit":
            result["net_profit"] = parse_dollar(amount_str)
            continue

        # Data rows
        amount = parse_dollar(amount_str)
        if section and acct_name:
            entry = {
                "account_number": acct_num,
                "account_name": acct_name,
                "amount": amount,
            }
            if section == "income":
                result["income"].append(entry)
            elif section == "cogs":
                result["cogs"].append(entry)
            elif section == "expenses":
                result["expenses"].append(entry)

    return result


def parse_qbo_pl(filepath):
    """Parse a QuickBooks Online Profit and Loss CSV."""
    result = {
        "format": "qbo_pl",
        "entity_name": "",
        "date_range": "",
        "income": [],
        "cogs": [],
        "expenses": [],
        "total_income": 0,
        "total_cogs": 0,
        "total_expenses": 0,
        "gross_profit": 0,
        "net_profit": 0,
        "findings": [],
        "parse_errors": [],
    }

    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        with open(filepath, "r", encoding="latin-1") as f:
            lines = f.readlines()

    if len(lines) < 4:
        result["parse_errors"].append(f"QBO P&L too short: {filepath}")
        return result

    result["entity_name"] = lines[1].strip().rstrip(",")
    result["date_range"] = lines[2].strip().strip('"').rstrip(",")

    section = None
    current_parent = None

    for line in lines[4:]:
        line = line.strip()
        if not line or line.startswith('"Cash Basis'):
            continue

        reader = csv.reader([line])
        try:
            fields = next(reader)
        except StopIteration:
            continue

        if len(fields) < 1:
            continue

        label = fields[0].strip()
        amount_str = fields[1].strip() if len(fields) > 1 else ""

        # Section detection
        if label == "Income":
            section = "income"
            continue
        elif label == "Expenses":
            section = "expenses"
            continue
        elif label == "Gross Profit":
            result["gross_profit"] = parse_dollar(amount_str) if amount_str else 0
            continue
        elif label.startswith("Net Operating Income"):
            result["net_profit"] = parse_dollar(amount_str) if amount_str else 0
            continue
        elif label.startswith("Net Income"):
            if not result["net_profit"]:
                result["net_profit"] = parse_dollar(amount_str) if amount_str else 0
            continue
        elif label == "Net Other Income":
            continue

        # "Total for X" lines
        if label.startswith("Total for "):
            parent_name = label[len("Total for "):]
            amount = parse_dollar(amount_str)
            # Capture top-level totals
            if parent_name == "Expenses":
                result["total_expenses"] = amount
            elif parent_name == "Income":
                result["total_income"] = amount
            # Reset parent tracking (child items already added individually)
            current_parent = None
            continue

        # Regular data lines
        if section and label:
            # QBO format: account code may be prefix "2220 Telephone - Wireless"
            acct_num = ""
            acct_name = label
            code_match = re.match(r"^(\d{4})\s+(.+)", label)
            if code_match:
                acct_num = code_match.group(1)
                acct_name = code_match.group(2)

            # If no amount and it's a parent category
            if not amount_str or not amount_str.strip():
                current_parent = acct_name
                continue

            amount = parse_dollar(amount_str)
            if amount == 0 and not amount_str.startswith("$"):
                # Try plain number
                try:
                    amount = float(amount_str.replace(",", ""))
                except (ValueError, AttributeError):
                    pass

            entry = {
                "account_number": acct_num,
                "account_name": acct_name,
                "full_label": label,
                "amount": amount,
                "parent": current_parent,
            }

            if section == "income":
                result["income"].append(entry)
            elif section == "expenses":
                result["expenses"].append(entry)

    # Calculate totals from parsed items if not set by totals lines
    if not result["total_expenses"]:
        result["total_expenses"] = sum(e["amount"] for e in result["expenses"])
    if not result["total_income"]:
        result["total_income"] = sum(e["amount"] for e in result["income"])

    return result


def parse_found_csv(filepath):
    """Parse Found.com activity report CSV."""
    result = {
        "format": "found_activity",
        "transactions": [],
        "income_total": 0,
        "expense_total": 0,
        "findings": [],
        "parse_errors": [],
    }

    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    amount = float(row.get("Amount", "0"))
                except (ValueError, TypeError):
                    amount = 0

                txn = {
                    "date": row.get("Date", ""),
                    "description": row.get("Description", ""),
                    "amount": amount,
                    "category": row.get("Category", ""),
                    "card": row.get("Card", ""),
                    "note": row.get("Note", ""),
                    "tags": row.get("Tags", ""),
                }
                result["transactions"].append(txn)

                if amount > 0:
                    result["income_total"] += amount
                else:
                    result["expense_total"] += abs(amount)

    except Exception as e:
        result["parse_errors"].append(f"Found.com CSV parse error: {e}")

    return result


def parse_accounting_csv(filepath, target_year=2025):
    """Parse Wave accounting export CSV, filtered to target year."""
    result = {
        "format": "wave_accounting",
        "transactions": [],
        "accounts_summary": {},
        "monthly_summary": {},
        "findings": [],
        "parse_errors": [],
    }

    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                txn_date_str = row.get("Transaction Date", "")
                try:
                    txn_date = datetime.strptime(txn_date_str, "%Y-%m-%d")
                except (ValueError, TypeError):
                    continue

                if txn_date.year != target_year:
                    continue

                account_group = row.get("Account Group", "")
                account_type = row.get("Account Type", "")
                account_name = row.get("Account Name", "")
                description = row.get("Transaction Description", "")

                try:
                    amount = float(row.get("Amount (One column)", "0") or "0")
                except (ValueError, TypeError):
                    amount = 0

                txn = {
                    "date": txn_date_str,
                    "account_name": account_name,
                    "description": description,
                    "amount": amount,
                    "account_group": account_group,
                    "account_type": account_type,
                    "customer": row.get("Customer", ""),
                    "vendor": row.get("Vendor", ""),
                }
                result["transactions"].append(txn)

                # Summarize by account (only expense/income rows)
                if account_group in ("Expense", "Income"):
                    if account_name not in result["accounts_summary"]:
                        result["accounts_summary"][account_name] = 0
                    result["accounts_summary"][account_name] += amount

                    # Monthly breakdown
                    month_key = txn_date.strftime("%Y-%m")
                    if month_key not in result["monthly_summary"]:
                        result["monthly_summary"][month_key] = {}
                    if account_name not in result["monthly_summary"][month_key]:
                        result["monthly_summary"][month_key][account_name] = 0
                    result["monthly_summary"][month_key][account_name] += amount

                # Flag suspicious transactions (only on expense side to avoid double-entry dupes)
                if account_group == "Expense":
                    desc_lower = description.lower()
                    # Only flag clearly personal keywords, not normal payment methods
                    for keyword in ["personal", "atm withdrawal", "venmo"]:
                        if keyword in desc_lower:
                            result["findings"].append({
                                "topic": "Suspicious Transaction",
                                "priority": "REQUIRED",
                                "finding": f"Verify business purpose: '{description}' on {txn_date_str} (${abs(amount):.2f}). Contains keyword '{keyword}'.",
                            })
                            break

    except Exception as e:
        result["parse_errors"].append(f"Accounting CSV parse error: {e}")

    return result


def parse_all_csvs(entity_path, entity_config, app_config):
    """Parse all CSV files in an entity folder."""
    result = {
        "pl_data": None,
        "found_data": None,
        "accounting_data": None,
        "balance_sheet": None,
        "other_csvs": [],
        "findings": [],
        "parse_errors": [],
    }

    csv_files = [f for f in os.listdir(entity_path) if f.lower().endswith(".csv")]

    for csv_file in csv_files:
        filepath = os.path.join(entity_path, csv_file)
        fmt = detect_csv_format(filepath)

        if fmt["format"] == "wave_pl":
            result["pl_data"] = parse_wave_pl(filepath)
            result["pl_data"]["source_file"] = csv_file
            result["findings"].extend(result["pl_data"].get("findings", []))
            result["parse_errors"].extend(result["pl_data"].get("parse_errors", []))

        elif fmt["format"] == "qbo_pl":
            result["pl_data"] = parse_qbo_pl(filepath)
            result["pl_data"]["source_file"] = csv_file
            result["findings"].extend(result["pl_data"].get("findings", []))
            result["parse_errors"].extend(result["pl_data"].get("parse_errors", []))

        elif fmt["format"] == "found_activity":
            result["found_data"] = parse_found_csv(filepath)
            result["found_data"]["source_file"] = csv_file
            result["findings"].extend(result["found_data"].get("findings", []))
            result["parse_errors"].extend(result["found_data"].get("parse_errors", []))

        elif fmt["format"] == "wave_accounting":
            # Extract target year from entity path
            year_match = re.search(r"(\d{4})\s+Tax Year", entity_path)
            target_year = int(year_match.group(1)) if year_match else 2025
            result["accounting_data"] = parse_accounting_csv(filepath, target_year)
            result["accounting_data"]["source_file"] = csv_file
            result["findings"].extend(result["accounting_data"].get("findings", []))
            result["parse_errors"].extend(result["accounting_data"].get("parse_errors", []))

        elif fmt["format"] == "wave_balance_sheet":
            result["balance_sheet"] = {"source_file": csv_file, "format": "wave_balance_sheet"}

        elif fmt["format"] in ("wave_customers", "wave_vendors", "wave_bills"):
            result["other_csvs"].append({"file": csv_file, "format": fmt["format"]})

        elif fmt["format"] == "unknown":
            # Check if there's a saved mapping
            saved_mappings = app_config.get("csv_mappings", {})
            header_hash = fmt.get("header_hash", "")
            if header_hash in saved_mappings:
                # Use saved mapping to parse
                pass  # TODO: implement custom mapping parser
            else:
                result["findings"].append({
                    "topic": "Unknown CSV Format",
                    "priority": "REVIEW",
                    "finding": f"Unrecognized CSV format: {csv_file}. Use the CSV Mapping modal to configure column mappings.",
                    "csv_file": csv_file,
                    "headers": fmt.get("headers", []),
                    "sample_rows": fmt.get("sample_rows", []),
                    "header_hash": header_hash,
                })

    # Flag if no P&L found
    if not result["pl_data"]:
        result["findings"].append({
            "topic": "Missing P&L",
            "priority": "BLOCKED",
            "finding": f"No Profit & Loss CSV found for {entity_config.get('name', 'Unknown')}. Upload a P&L export to the entity folder.",
        })

    return result
