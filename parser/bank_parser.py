"""Bank reconciliation — aggregates PDF and CSV bank data into monthly summaries."""

import os
import re
import logging
from datetime import datetime
from .pdf_parser import parse_all_pdfs_in_folder

logger = logging.getLogger(__name__)

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_FULL = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]


def _parse_wf_date(filename):
    """Parse Wells Fargo statement date from filename (MMDDYY format)."""
    match = re.match(r"(\d{6})", filename)
    if match:
        try:
            return datetime.strptime(match.group(1), "%m%d%y")
        except ValueError:
            pass
    return None


def _parse_citi_month(filename, year):
    """Parse Citi CC statement month from filename like 'Citi-CC_XXXX_March 05.pdf'."""
    match = re.search(r"_(\w+)(?:\s+\d+)?\.pdf$", filename, re.IGNORECASE)
    if match:
        month_name = match.group(1).strip()
        # Handle "(2026)" suffix for January spillover
        if "2026" in filename or "(" in month_name:
            return None  # Next year's statement

        for i, name in enumerate(MONTH_FULL):
            if name.lower().startswith(month_name.lower()):
                return f"{year}-{i+1:02d}"
    return None


def build_bank_reconciliation(year_path, config, year):
    """Build bank reconciliation data for all accounts."""
    entities = config.get("entities", [])
    accounts = []
    findings = []

    for entity in entities:
        folder_name = entity.get("folder_name", "")
        entity_path = os.path.join(year_path, folder_name)
        if not os.path.isdir(entity_path):
            continue

        # Parse all PDFs in entity folder
        pdf_results = parse_all_pdfs_in_folder(entity_path)

        # Group by account type
        wf_statements = []
        citi_statements = []
        other_statements = []

        for pdf in pdf_results:
            if pdf.get("institution") == "Wells Fargo":
                wf_statements.append(pdf)
            elif pdf.get("institution") == "Citi":
                citi_statements.append(pdf)
            elif pdf.get("type") in ("bank_statement", "cc_statement"):
                other_statements.append(pdf)

        # Wells Fargo checking account
        for bank_acct in entity.get("bank_accounts", []):
            acct_name = bank_acct.get("name", "Checking")
            institution = bank_acct.get("institution", "")

            if "wells fargo" in institution.lower():
                monthly = _build_monthly_summary(wf_statements, year)
                status = "COMPLETE" if len(monthly) >= 12 else ("PARTIAL" if monthly else "MISSING")
                gaps = _find_gaps(monthly, year)

                accounts.append({
                    "name": f"{entity['name']} — {acct_name} ({institution})",
                    "entity": entity["name"],
                    "type": "checking",
                    "institution": institution,
                    "months": monthly,
                    "status": status,
                    "gaps": gaps,
                    "statement_count": len(wf_statements),
                })

                if gaps:
                    findings.append({
                        "topic": "Bank Statement Gaps",
                        "priority": "REVIEW",
                        "finding": f"{entity['name']} {acct_name}: Missing or incomplete statements for months: {', '.join(gaps)}.",
                    })

        # Credit cards
        for cc in entity.get("credit_cards", []):
            cc_name = cc.get("name", "Credit Card")
            institution = cc.get("institution", "")

            if "citi" in institution.lower():
                monthly = _build_cc_monthly_summary(citi_statements, year)
                status = "COMPLETE" if len(monthly) >= 12 else ("PARTIAL" if monthly else "MISSING")
                gaps = _find_gaps(monthly, year)

                accounts.append({
                    "name": f"{entity['name']} — {cc_name} ({institution})",
                    "entity": entity["name"],
                    "type": "credit_card",
                    "institution": institution,
                    "months": monthly,
                    "status": status,
                    "gaps": gaps,
                    "statement_count": len(citi_statements),
                })
            elif "apple" in institution.lower():
                # Apple Card - no statements available
                accounts.append({
                    "name": f"{entity['name']} — {cc_name} ({institution})",
                    "entity": entity["name"],
                    "type": "credit_card",
                    "institution": institution,
                    "months": [],
                    "status": "MISSING",
                    "gaps": [f"{year}-{m:02d}" for m in range(1, 13)],
                    "statement_count": 0,
                })
                findings.append({
                    "topic": "Missing Apple Card Statements",
                    "priority": "BLOCKED",
                    "finding": f"{entity['name']}: No Apple Card (Goldman Sachs) statements found. "
                               f"Balance Sheet shows Apple Card activity. Download all 12 monthly statements.",
                })

    return {"accounts": accounts, "findings": findings, "status": "complete"}


def _build_monthly_summary(statements, year):
    """Build monthly summary from Wells Fargo statements."""
    monthly = {}

    for stmt in statements:
        # Determine month from statement data
        month_key = stmt.get("statement_month")
        if not month_key:
            stmt_date = stmt.get("statement_date", "")
            if stmt_date:
                try:
                    dt = datetime.strptime(stmt_date, "%B %d, %Y")
                    month_key = dt.strftime("%Y-%m")
                except ValueError:
                    pass

        if not month_key:
            continue

        # Only include target year
        if not month_key.startswith(str(year)):
            continue

        monthly[month_key] = {
            "month": month_key,
            "beginning_balance": stmt.get("beginning_balance"),
            "ending_balance": stmt.get("ending_balance"),
            "total_deposits": stmt.get("total_deposits"),
            "total_withdrawals": stmt.get("total_withdrawals"),
            "status": stmt.get("status", "PARTIAL"),
            "source_file": stmt.get("file", ""),
        }

    # Sort by month
    return [monthly[k] for k in sorted(monthly.keys())]


def _build_cc_monthly_summary(statements, year):
    """Build monthly summary from credit card statements."""
    monthly = {}

    for stmt in statements:
        month_name = stmt.get("statement_month_name", "")
        month_key = _parse_citi_month(stmt.get("file", ""), year)

        if not month_key:
            continue

        monthly[month_key] = {
            "month": month_key,
            "previous_balance": stmt.get("previous_balance"),
            "new_balance": stmt.get("new_balance"),
            "total_payments": stmt.get("total_payments"),
            "total_charges": stmt.get("total_charges"),
            "status": stmt.get("status", "PARTIAL"),
            "source_file": stmt.get("file", ""),
        }

    return [monthly[k] for k in sorted(monthly.keys())]


def _find_gaps(monthly_list, year):
    """Find missing months in the data."""
    covered = {m["month"] for m in monthly_list}
    expected = {f"{year}-{i:02d}" for i in range(1, 13)}
    return sorted(expected - covered)
