"""PDF parsing for bank statements, 1099s, and W-2s using pdfplumber."""

import os
import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False
    logger.warning("pdfplumber not installed. PDF parsing disabled.")


def parse_pdf(filepath):
    """Auto-detect PDF type and parse accordingly."""
    if not HAS_PDFPLUMBER:
        return {"status": "FAILED", "error": "pdfplumber not installed", "file": os.path.basename(filepath)}

    filename = os.path.basename(filepath).lower()

    try:
        if "wellsfargo" in filename or "wells_fargo" in filename:
            return parse_wells_fargo_statement(filepath)
        elif "citi" in filename:
            return parse_citi_cc_statement(filepath)
        elif "1099" in filename:
            return parse_1099_pdf(filepath)
        elif "w2" in filename or "w-2" in filename:
            return parse_w2_pdf(filepath)
        else:
            return parse_generic_pdf(filepath)
    except Exception as e:
        logger.error(f"PDF parse error for {filepath}: {e}")
        return {
            "status": "FAILED",
            "error": str(e),
            "file": os.path.basename(filepath),
        }


def parse_wells_fargo_statement(filepath):
    """Parse a Wells Fargo bank statement PDF."""
    result = {
        "type": "bank_statement",
        "institution": "Wells Fargo",
        "file": os.path.basename(filepath),
        "status": "PARTIAL",
        "account_number": "",
        "statement_date": "",
        "beginning_balance": None,
        "ending_balance": None,
        "total_deposits": None,
        "total_withdrawals": None,
        "transactions": [],
    }

    try:
        with pdfplumber.open(filepath) as pdf:
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"

            if not full_text.strip():
                result["status"] = "FAILED"
                result["error"] = "No text extracted - may be scanned PDF"
                return result

            # Extract account number
            acct_match = re.search(r"Account\s*(?:Number|#|:)\s*[*xX]*(\d{4})", full_text)
            if not acct_match:
                acct_match = re.search(r"(?:ending in|\.\.\.)\s*(\d{4})", full_text)
            if acct_match:
                result["account_number"] = acct_match.group(1)

            # Extract statement period
            period_match = re.search(
                r"(?:Statement [Pp]eriod|Period).*?(\w+ \d{1,2},?\s*\d{4})\s*(?:through|to|-)\s*(\w+ \d{1,2},?\s*\d{4})",
                full_text
            )
            if period_match:
                result["statement_date"] = period_match.group(2).strip()

            # Detect if this is a credit card statement vs checking
            is_cc = bool(re.search(r"(?:SIGNIFY|CREDIT CARD|Previous Balance|New Balance)", full_text))

            if is_cc:
                # Credit card statement — extract CC-specific fields
                result["type"] = "cc_statement"
                prev_match = re.search(r"Previous Balance\s*\$?([\d,]+\.\d{2})", full_text)
                if prev_match:
                    result["previous_balance"] = float(prev_match.group(1).replace(",", ""))
                new_match = re.search(r"New Balance\s*\$?([\d,]+\.\d{2})", full_text)
                if new_match:
                    result["new_balance"] = float(new_match.group(1).replace(",", ""))
                pay_match = re.search(r"(?:Payments|Credits)\s*-?\s*\$?([\d,]+\.\d{2})", full_text)
                if pay_match:
                    result["total_payments"] = float(pay_match.group(1).replace(",", ""))
                chg_match = re.search(r"(?:Purchases|Charges)\s*\+?\s*\$?([\d,]+\.\d{2})", full_text)
                if chg_match:
                    result["total_charges"] = float(chg_match.group(1).replace(",", ""))
                if result.get("previous_balance") is not None or result.get("new_balance") is not None:
                    result["status"] = "COMPLETE"
            else:
                # Checking statement — extract checking-specific fields
                begin_match = re.search(r"[Bb]eginning [Bb]alance.*?\$?([\d,]+\.\d{2})", full_text)
                if begin_match:
                    result["beginning_balance"] = float(begin_match.group(1).replace(",", ""))

                end_match = re.search(r"[Ee]nding [Bb]alance.*?\$?([\d,]+\.\d{2})", full_text)
                if end_match:
                    result["ending_balance"] = float(end_match.group(1).replace(",", ""))

                dep_match = re.search(r"(?:[Tt]otal )?[Dd]eposits(?:/[Cc]redits)?.*?\$?([\d,]+\.\d{2})", full_text)
                if dep_match:
                    result["total_deposits"] = float(dep_match.group(1).replace(",", ""))

                with_match = re.search(r"(?:[Tt]otal )?[Ww]ithdrawals(?:/[Dd]ebits)?.*?\$?([\d,]+\.\d{2})", full_text)
                if with_match:
                    result["total_withdrawals"] = float(with_match.group(1).replace(",", ""))

                if result["beginning_balance"] is not None or result["ending_balance"] is not None:
                    result["status"] = "COMPLETE"

            # Determine statement month from filename (MMDDYY format)
            fname = os.path.basename(filepath)
            date_match = re.match(r"(\d{6})", fname)
            if date_match:
                date_str = date_match.group(1)
                try:
                    stmt_date = datetime.strptime(date_str, "%m%d%y")
                    result["statement_month"] = stmt_date.strftime("%Y-%m")
                    if not result["statement_date"]:
                        result["statement_date"] = stmt_date.strftime("%B %d, %Y")
                except ValueError:
                    pass

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)

    return result


def parse_citi_cc_statement(filepath):
    """Parse a Citi credit card statement PDF."""
    result = {
        "type": "cc_statement",
        "institution": "Citi",
        "file": os.path.basename(filepath),
        "status": "PARTIAL",
        "account_number": "",
        "statement_date": "",
        "previous_balance": None,
        "new_balance": None,
        "total_payments": None,
        "total_charges": None,
        "transactions": [],
    }

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

            # Extract account number from filename or text
            fname = os.path.basename(filepath)
            acct_match = re.search(r"(\d{4})", fname)
            if acct_match:
                result["account_number"] = acct_match.group(1)

            # Statement month from filename: "Citi-CC_8552_Month DD.pdf"
            month_match = re.search(r"_(\w+)\s*(?:\d+)?\.pdf", fname, re.IGNORECASE)
            if month_match:
                month_name = month_match.group(1)
                result["statement_month_name"] = month_name

            # Extract balances
            prev_match = re.search(r"[Pp]revious [Bb]alance[:\s]*\$?([\d,]+\.?\d*)", full_text)
            if prev_match:
                result["previous_balance"] = float(prev_match.group(1).replace(",", ""))

            new_match = re.search(r"[Nn]ew [Bb]alance[:\s]*\$?([\d,]+\.?\d*)", full_text)
            if new_match:
                result["new_balance"] = float(new_match.group(1).replace(",", ""))

            pay_match = re.search(r"[Tt]otal [Pp]ayments[:\s]*\$?([\d,]+\.?\d*)", full_text)
            if pay_match:
                result["total_payments"] = float(pay_match.group(1).replace(",", ""))

            chg_match = re.search(r"[Tt]otal (?:[Cc]harges|[Pp]urchases)[:\s]*\$?([\d,]+\.?\d*)", full_text)
            if chg_match:
                result["total_charges"] = float(chg_match.group(1).replace(",", ""))

            if result["new_balance"] is not None:
                result["status"] = "COMPLETE"

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)

    return result


def parse_1099_pdf(filepath):
    """Parse a 1099 PDF to extract payer and amounts."""
    result = {
        "type": "1099",
        "file": os.path.basename(filepath),
        "status": "PARTIAL",
        "payer_name": "",
        "recipient_name": "",
        "amount": 0,
        "form_type": "1099-NEC",
    }

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

            # Extract payer name (usually near top)
            payer_match = re.search(r"PAYER[''`S]*\s*(?:name|NAME)[:\s]*(.+)", full_text)
            if payer_match:
                result["payer_name"] = payer_match.group(1).strip()[:80]

            # Extract amounts - look for "Nonemployee compensation" box
            nec_match = re.search(r"[Nn]onemployee\s+[Cc]ompensation[:\s]*\$?([\d,]+\.?\d*)", full_text)
            if nec_match:
                result["amount"] = float(nec_match.group(1).replace(",", ""))
                result["form_type"] = "1099-NEC"

            # Fallback: look for any dollar amount in a box
            if result["amount"] == 0:
                amounts = re.findall(r"\$?([\d,]+\.\d{2})", full_text)
                if amounts:
                    result["amount"] = max(float(a.replace(",", "")) for a in amounts)

            if result["payer_name"] or result["amount"] > 0:
                result["status"] = "COMPLETE"

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)

    return result


_MONEY = r"(?:\d{1,3}(?:,\d{3})+|\d{1,9})\.\d{2}"


def parse_w2_pdf(filepath):
    """Parse a W-2 PDF.

    IRS / ADP W-2 layouts put the box label on one line and the dollar value
    on the next line. We match the label on line N and capture the first
    dollar-formatted number on the following 1-3 lines. As a fallback we
    look for ADP's 'Reported W-2 Wages' summary row.
    """
    result = {
        "type": "w2",
        "file": os.path.basename(filepath),
        "status": "PARTIAL",
        "employer_name": "",
        "employee_name": "",
        "wages": 0,
        "federal_withholding": 0,
        "social_security_wages": 0,
        "medicare_wages": 0,
        "state_wages": 0,
        "state_withholding": 0,
    }

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

        # ─ ADP summary row (most reliable when present) ─────────────────────
        m = re.search(rf"Reported W-2 Wages\s+({_MONEY})", full_text)
        if m:
            result["wages"] = float(m.group(1).replace(",", ""))

        # ─ Positional fallback: on most W-2 layouts the data rows extract as
        #   standalone lines "AMOUNT1 AMOUNT2" in this order:
        #     row 0: Box 1 wages   | Box 2 federal withholding
        #     row 1: Box 3 SS wages| Box 4 SS tax withheld
        #     row 2: Box 5 Medicare| Box 6 Medicare tax withheld
        #   We anchor on the wages number (already captured above via the ADP
        #   summary if present) and walk the first three "MONEY MONEY" lines.
        paired = re.findall(rf"^\s*({_MONEY})\s+({_MONEY})\s*$", full_text, re.MULTILINE)
        if paired:
            if not result["wages"]:
                result["wages"] = float(paired[0][0].replace(",", ""))
            result["federal_withholding"] = float(paired[0][1].replace(",", ""))
            if len(paired) > 1:
                result["social_security_wages"] = float(paired[1][0].replace(",", ""))
            if len(paired) > 2:
                result["medicare_wages"] = float(paired[2][0].replace(",", ""))

        # ─ State wages (CO line has 3-token layout: "CO 95338046 50565.60") ─
        m = re.search(rf"\b[A-Z]{{2}}\s+\d{{6,}}\s+({_MONEY})", full_text)
        if m:
            result["state_wages"] = float(m.group(1).replace(",", ""))

        # ─ Employer name (first non-blank line after "Employer's name") ────
        m = re.search(r"Employer['`\u2019]?s?\s+name[^\n]*\n([A-Z][^\n]{2,80})", full_text)
        if m:
            result["employer_name"] = m.group(1).strip()[:80]

        # ─ Employee name (nice-to-have; used for attribution in the UI) ────
        m = re.search(r"Employee['`\u2019]?s?\s+name[^\n]*\n([A-Z][A-Z\s]{2,60})", full_text)
        if m:
            result["employee_name"] = m.group(1).strip()[:80]

        if result["wages"] > 0:
            result["status"] = "COMPLETE"

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)

    return result


def parse_generic_pdf(filepath):
    """Generic PDF parser - extract text and try to identify type."""
    result = {
        "type": "unknown",
        "file": os.path.basename(filepath),
        "status": "PARTIAL",
        "text_preview": "",
    }

    try:
        with pdfplumber.open(filepath) as pdf:
            text = ""
            for page in pdf.pages[:3]:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"

            result["text_preview"] = text[:500]
            if text.strip():
                result["status"] = "COMPLETE"
            else:
                result["status"] = "FAILED"
                result["error"] = "No text extracted - may be scanned"

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)

    return result


def parse_all_pdfs_in_folder(folder_path):
    """Parse all PDFs in a folder."""
    results = []
    if not os.path.isdir(folder_path):
        return results

    for filename in sorted(os.listdir(folder_path)):
        if filename.lower().endswith(".pdf"):
            filepath = os.path.join(folder_path, filename)
            result = parse_pdf(filepath)
            results.append(result)

    return results


def parse_prior_return(filepath):
    """Extract key fields from a prior-year Form 1040 PDF (AGI, total tax, etc.)."""
    if not HAS_PDFPLUMBER:
        return {"status": "FAILED", "error": "pdfplumber not installed"}

    result = {
        "type": "prior_return",
        "file": os.path.basename(filepath),
        "status": "PARTIAL",
    }

    try:
        with pdfplumber.open(filepath) as pdf:
            full_text = ""
            for page in pdf.pages[:5]:  # First 5 pages cover Form 1040
                text = page.extract_text()
                if text:
                    full_text += text + "\n"

            if not full_text.strip():
                result["status"] = "FAILED"
                result["error"] = "No text extracted"
                return result

            # AGI — Line 11 of Form 1040
            agi_match = re.search(
                r"(?:line\s*11|adjusted\s*gross\s*income).*?[\$\s]([\d,]+(?:\.\d{2})?)",
                full_text, re.IGNORECASE
            )
            if agi_match:
                result["agi"] = float(agi_match.group(1).replace(",", ""))

            # Total tax — Line 24
            tax_match = re.search(
                r"(?:line\s*24|total\s*tax).*?[\$\s]([\d,]+(?:\.\d{2})?)",
                full_text, re.IGNORECASE
            )
            if tax_match:
                result["total_tax"] = float(tax_match.group(1).replace(",", ""))

            # Filing status
            for status_kw in ["married filing jointly", "single", "head of household",
                              "married filing separately", "qualifying"]:
                if status_kw in full_text.lower():
                    result["filing_status"] = status_kw.title()
                    break

            # Tax year
            year_match = re.search(r"(?:Form 1040|tax year|20)\s*(\d{4})", full_text, re.IGNORECASE)
            if year_match:
                result["tax_year"] = int(year_match.group(1))

            if result.get("agi") is not None:
                result["status"] = "COMPLETE"

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)

    return result
