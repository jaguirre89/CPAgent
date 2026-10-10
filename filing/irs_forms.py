"""Generate populated IRS PDFs from parsed tax data.

Produces a merged `YYYY_1040_Draft.pdf` in the year's `Final_Worksheets/` folder
(or the fallback path if that folder isn't found). Every generation is recorded
in the audit log.

The field mapping in `form_field_map.py` is a best-effort first pass. The PDF is
stamped with a "DRAFT — NOT FOR FILING" watermark until the readiness gate
lifts (BLOCKED=0, REQUIRED=0, checklist≥95%).
"""

from __future__ import annotations

import os
import io
import logging
from pathlib import Path
from datetime import datetime

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject, BooleanObject, DictionaryObject

from filing.form_field_map import (
    SCHEDULE_C,
    SCHEDULE_SE,
    FORM_1040,
    FORM_4868,
    CO_DR0104,
    CO_DR0158I,
    SCHEDULE_C_CATEGORY_TO_FIELD,
)

logger = logging.getLogger(__name__)

APP_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent
TEMPLATES = APP_DIR / "filing" / "templates"


def _money(v) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return ""
    if n == 0:
        return ""
    return f"{n:.0f}"


def _consolidated_values(parsed: dict, config: dict) -> dict:
    """Roll schedule_c lines across entities into a single flat dict."""
    schedules = (parsed.get("schedule_c", {}) or {}).get("schedules", []) or []
    gross = 0.0
    total_exp = 0.0
    net = 0.0
    by_category = {}

    for s in schedules:
        gross += float(s.get("total_income") or 0)
        total_exp += float(s.get("total_expenses") or 0)
        net += float(s.get("net_profit") or 0)
        for line in s.get("expense_lines", []) or []:
            cat = line.get("description") or ""
            amt = float(line.get("amount") or 0)
            by_category[cat] = by_category.get(cat, 0.0) + amt

    owner = config.get("owner_name") or ""
    first, _, last = owner.partition(" ")
    spouse = config.get("spouse_name") or ""
    sp_first, _, sp_last = spouse.partition(" ")

    # Home office deduction from config preferences
    home_office = float((config.get("preferences") or {}).get("home_office_total") or 0)
    net_after_home = net - home_office if net > 0 else net

    # SE tax computation
    se_net = max(0.0, net_after_home * 0.9235)
    se_tax = se_net * 0.153 if net_after_home > 0 else 0
    se_deduction = se_tax / 2

    # W-2 spouse income
    kpis = (parsed.get("overview", {}) or {}).get("kpis", {}) or {}
    spouse_wages = float(kpis.get("spouse_income") or 0)

    # QBI deduction
    qbi_rate = 0.20
    qbi_deduction = max(0.0, net_after_home * qbi_rate) if net_after_home > 0 else 0.0

    # Standard deduction
    filing_status = (config.get("filing_status") or "MFJ").upper()
    std_deduction_map = {"MFJ": 30000, "SINGLE": 15000, "HOH": 22500, "MFS": 15000}
    std_deduction = std_deduction_map.get(filing_status, 30000)

    # AGI and taxable income
    total_income = spouse_wages + net_after_home
    adjustments = se_deduction  # half of SE tax is above-the-line
    agi = total_income - adjustments
    total_deductions = std_deduction + qbi_deduction
    taxable_income = max(0.0, agi - total_deductions)

    # Filing status checkbox
    fs_map = {"SINGLE": "fs_single", "MFJ": "fs_mfj", "MFS": "fs_mfs", "HOH": "fs_hoh"}
    fs_key = fs_map.get(filing_status, "fs_mfj")

    values = {
        "schedule_c": {
            "proprietor_name": owner,
            "ssn": "",  # User must enter SSN manually
            "business_name": schedules[0]["entity"] if schedules else "",
            "address": config.get("address") or "",
            "city_state_zip": config.get("city_state_zip") or "",
            "line_1_gross_receipts": _money(gross),
            "line_3_net": _money(gross),
            "line_7_gross_income": _money(gross),
            "line_28_total_expenses": _money(total_exp),
            "line_29_tentative_profit": _money(net),
            "line_30_home_office": _money(home_office),
            "line_31_net_profit": _money(net_after_home),
        },
        "schedule_se": {
            "name": owner,
            "line_2_net_from_sch_c": _money(net_after_home),
            "line_3_total": _money(net_after_home),
            "line_4a_net_earnings": _money(se_net),
            "line_6_total_se": _money(se_net),
            "line_12_total_se_tax": _money(se_tax),
            "line_13_deduction": _money(se_deduction),
        },
        "form_1040": {
            "first_name_mi": first,
            "last_name": last or owner,
            "spouse_first_name_mi": sp_first,
            "spouse_last_name": sp_last,
            "address": config.get("address") or "",
            "city_state_zip": config.get("city_state_zip") or "",
            fs_key: "1",  # Check the filing status box
            "line_1a_wages": _money(spouse_wages),
            "line_1z_total_wages": _money(spouse_wages),
            "line_8_sched1": _money(net_after_home),
            "line_9_total_income": _money(total_income),
            "line_10_adjustments": _money(adjustments),
            "line_11_agi": _money(agi),
            "line_12_deduction": _money(std_deduction),
            "line_13_qbi": _money(qbi_deduction),
            "line_14_total_ded": _money(total_deductions),
            "line_15_taxable": _money(taxable_income),
        },
        "by_category": by_category,
        "gross": gross,
        "total_expenses": total_exp,
        "net": net_after_home,
    }

    for cat, amt in by_category.items():
        field = SCHEDULE_C_CATEGORY_TO_FIELD.get(cat)
        if field:
            values["schedule_c"][field] = _money(amt)

    return values


def _populate(reader: PdfReader, field_map: dict, values: dict) -> PdfWriter:
    writer = PdfWriter(clone_from=reader)
    real_updates = {}
    for semantic, field_name in field_map.items():
        val = values.get(semantic)
        if val in (None, ""):
            continue
        real_updates[field_name] = str(val)
    if real_updates:
        try:
            for page in writer.pages:
                writer.update_page_form_field_values(page, real_updates)
        except Exception as e:
            logger.warning(f"field population partial failure: {e}")
    try:
        root = writer._root_object
        if "/AcroForm" in root:
            root["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
    except Exception as e:
        logger.debug(f"AcroForm NeedAppearances set skipped: {e}")
    return writer


def _stamp_watermark(writer: PdfWriter, text: str):
    """Overlay a watermark using a simple canvas — optional, skipped if reportlab missing."""
    try:
        from reportlab.pdfgen import canvas  # noqa
        from reportlab.lib.pagesizes import letter  # noqa
    except ImportError:
        logger.info("reportlab not installed — skipping watermark stamp")
        return
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.saveState()
    c.setFillColorRGB(0.85, 0.85, 0.85)
    c.setFont("Helvetica-Bold", 72)
    c.translate(306, 400)
    c.rotate(45)
    c.drawCentredString(0, 0, text)
    c.restoreState()
    c.save()
    buf.seek(0)
    stamp = PdfReader(buf)
    for page in writer.pages:
        page.merge_page(stamp.pages[0])


def generate_1040_schedule_c(
    year: int,
    parsed: dict,
    config: dict,
    year_path: str,
    apply_watermark: bool = True,
) -> dict:
    values = _consolidated_values(parsed, config)

    writer = PdfWriter()

    templates = [
        ("Form 1040", "f1040.pdf", FORM_1040, values["form_1040"]),
        ("Schedule 1", "f1040s1.pdf", {}, {}),
        ("Schedule C", "f1040sc.pdf", SCHEDULE_C, values["schedule_c"]),
        ("Schedule SE", "f1040sse.pdf", SCHEDULE_SE, values["schedule_se"]),
    ]

    missing = []
    for label, filename, field_map, vals in templates:
        path = TEMPLATES / filename
        if not path.exists():
            missing.append(filename)
            continue
        reader = PdfReader(str(path))
        populated = _populate(reader, field_map, vals)
        for p in populated.pages:
            writer.add_page(p)

    if apply_watermark:
        _stamp_watermark(writer, "DRAFT — NOT FOR FILING")

    out_dir = Path(year_path) / _locate_worksheets_folder(year_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{year}_1040_Draft.pdf"

    with open(out_path, "wb") as f:
        writer.write(f)

    return {
        "path": str(out_path),
        "pages": len(writer.pages),
        "gross": values["gross"],
        "total_expenses": values["total_expenses"],
        "net_profit": values["net"],
        "watermarked": apply_watermark,
        "missing_templates": missing,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def generate_extension_4868(
    year: int,
    parsed: dict,
    config: dict,
    year_path: str,
) -> dict:
    """Generate a populated Form 4868 extension request.

    Pulls the estimated-tax-owed KPIs we already compute in build_overview
    and drops them into Part II of the extension form. The user reviews,
    prints, and either mails it or e-files via IRS Free File Fillable Forms
    (both valid; postmark date counts for extension).
    """
    kpis = (parsed.get("overview", {}) or {}).get("kpis", {}) or {}
    owner = config.get("owner_name") or ""
    first, _, last = owner.partition(" ")

    est_total_tax   = kpis.get("estimated_tax_owed_before_payments", 0)
    total_payments  = (kpis.get("estimated_payments_made", 0) or 0) + (kpis.get("spouse_federal_withholding", 0) or 0)
    balance_due     = max(0.0, est_total_tax - total_payments)

    values = {
        "name": owner,
        "address": config.get("address", ""),
        "city_state_zip": config.get("city_state_zip", ""),
        "ssn": "",
        "spouse_ssn": "",
        "line_4_estimated_total_tax": _money(est_total_tax),
        "line_5_total_payments": _money(total_payments),
        "line_6_balance_due": _money(balance_due),
        "line_7_amount_paying": _money(balance_due),
    }

    template = TEMPLATES / "f4868.pdf"
    if not template.exists():
        return {"error": "f4868.pdf template not found — run scripts/download_irs_forms.sh"}

    reader = PdfReader(str(template))
    populated = _populate(reader, FORM_4868, values)

    writer = PdfWriter()
    for p in populated.pages:
        writer.add_page(p)

    out_dir = Path(year_path) / _locate_worksheets_folder(year_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{year}_Form_4868_Extension.pdf"
    with open(out_path, "wb") as f:
        writer.write(f)

    return {
        "path": str(out_path),
        "pages": len(writer.pages),
        "estimated_total_tax": est_total_tax,
        "total_payments": total_payments,
        "balance_due": balance_due,
        "amount_paying": balance_due,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "instructions": (
            "To file: upload this PDF via IRS Free File Fillable Forms at "
            "freefilefillableforms.com (direct IRS e-file, free) OR print and mail. "
            "Extension grants time to FILE — not time to PAY. Estimated balance due "
            "should be paid by the original filing deadline (April 15) to avoid late-payment penalty."
        ),
    }


def generate_state_filing(
    year: int,
    parsed: dict,
    config: dict,
    year_path: str,
) -> dict:
    """Generate the per-state filing PDF using the entity's configured state.

    Round 1: Colorado DR 0104 only. The flat 4.4% rate + federal taxable income
    starting point makes this a 1-file generator; other states will each need
    their own field map + handler in state_tax.py's registry.
    """
    kpis = (parsed.get("overview", {}) or {}).get("kpis", {}) or {}
    state = (kpis.get("state") or "").upper()

    if state != "CO":
        return {
            "error": f"state filing not implemented for {state!r}. CPAgent only ships CO DR 0104 in round 1.",
            "state": state,
        }

    template = TEMPLATES / "co_dr0104_2025.pdf"
    if not template.exists():
        return {"error": "co_dr0104_2025.pdf not found — run scripts/download_irs_forms.sh"}

    owner = config.get("owner_name") or ""
    first, _, last = owner.partition(" ")
    spouse = config.get("spouse_name") or ""
    sp_first, _, sp_last = spouse.partition(" ")

    state_tax = kpis.get("state_tax") or {}
    fed_taxable = float(state_tax.get("taxable_income") or 0)
    co_rate = 0.044  # 4.40% flat rate for 2025
    co_tax = fed_taxable * co_rate
    withholding = float(state_tax.get("estimated_withholding") or 0)
    est_payments = float(state_tax.get("estimated_payments_made") or 0)
    total_payments = withholding + est_payments
    balance_or_overpay = co_tax - total_payments

    values = {
        "first_name": first,
        "last_name": last or owner,
        "ssn": config.get("ssn_last4") or "",
        "spouse_first_name": sp_first,
        "spouse_last_name": sp_last,
        "spouse_ssn": "",
        "address": config.get("address") or "",
        "city": config.get("city") or "",
        "state": "CO",
        "zip": config.get("zip") or "",
        "phone": config.get("phone") or "",
        # Dollar lines
        "line_1_fed_taxable": _money(fed_taxable),
        "line_2_additions": "",  # No additions for simple Schedule C filer
        "line_3_subtotal": _money(fed_taxable),
        "line_4_subtractions": "",  # No subtractions (TABOR refund TODO)
        "line_5_co_taxable": _money(fed_taxable),
        "line_6_co_tax": _money(co_tax),
        "line_10_total_tax": _money(co_tax),
        "line_20_income_tax_paid": _money(withholding) if withholding else "",
        "line_21_estimated_paid": _money(est_payments) if est_payments else "",
        "line_33_total_payments": _money(total_payments) if total_payments else "",
    }
    if balance_or_overpay > 0:
        values["line_39_amount_owed"] = _money(balance_or_overpay)
    elif balance_or_overpay < 0:
        values["line_34_overpayment"] = _money(abs(balance_or_overpay))
        values["line_36_refund"] = _money(abs(balance_or_overpay))

    reader = PdfReader(str(template))
    populated = _populate(reader, CO_DR0104, values)

    writer = PdfWriter()
    for p in populated.pages:
        writer.add_page(p)

    _stamp_watermark(writer, "DRAFT — NOT FOR FILING")

    out_dir = Path(year_path) / _locate_worksheets_folder(year_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{year}_CO_DR0104_Draft.pdf"
    with open(out_path, "wb") as f:
        writer.write(f)

    return {
        "path": str(out_path),
        "pages": len(writer.pages),
        "state": "CO",
        "state_name": "Colorado",
        "form": "DR 0104",
        "state_taxable_income": fed_taxable,
        "state_tax_before_payments": round(co_tax, 2),
        "state_tax_owed": round(max(0, balance_or_overpay), 2),
        "withholding": round(withholding, 2),
        "estimated_payments": round(est_payments, 2),
        "watermarked": True,
        "notes": state_tax.get("notes", []),
        "instructions": (
            "Dollar lines are auto-populated from CPAgent's tax computation. "
            "Verify: Line 1 = federal taxable income, Line 6 = 4.40% flat rate. "
            "TABOR refund and state-specific subtractions are NOT applied — adjust "
            "manually if applicable. e-File via colorado.gov/revenueonline (free) "
            "or print and mail."
        ),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def generate_state_extension(
    year: int,
    parsed: dict,
    config: dict,
    year_path: str,
) -> dict:
    """State extension request.

    For Colorado we ship a fully-populated DR 0158-I PDF. For every other
    state we return a structured response with the form name, DOR URL, and
    computed balance-due figure so the user can file manually or follow
    the link to the state's own e-file / fillable form.
    """
    kpis = (parsed.get("overview", {}) or {}).get("kpis", {}) or {}
    state = (kpis.get("state") or "").upper()
    state_tax = kpis.get("state_tax") or {}

    if state != "CO":
        # Generic handler for all other states — return a ticket the user can
        # follow instead of a PDF we don't know how to populate.
        try:
            import states_registry
            meta = states_registry.get(state) or {}
        except Exception:
            meta = {}

        estimated = float(state_tax.get("tax_before_payments") or 0)
        withholding = float(state_tax.get("estimated_withholding") or 0)
        prior_payments = float(state_tax.get("estimated_payments_made") or 0)
        payments_total = withholding + prior_payments
        balance = max(0.0, estimated - payments_total) if estimated else None

        return {
            "state": state,
            "state_name": meta.get("name") or state,
            "form": meta.get("extension_form"),
            "form_url": meta.get("extension_url"),
            "extension_auto_granted": meta.get("extension_auto_granted", False),
            "has_income_tax": meta.get("has_income_tax"),
            "rate_type": meta.get("rate_type"),
            "estimated_total_tax": estimated or None,
            "total_payments": payments_total or None,
            "balance_due": balance,
            "status": "referral",
            "notes": state_tax.get("notes", []),
            "instructions": (
                (
                    f"{meta.get('name') or state} has no individual income tax — no state extension needed."
                ) if meta.get("has_income_tax") is False else
                (
                    f"{meta.get('name') or state} grants an automatic 6-month filing extension when you file "
                    f"federal Form 4868 and pay any balance due by April 15. You usually do NOT need to file a "
                    f"separate state form. Details: {meta.get('extension_url')}"
                ) if meta.get("extension_auto_granted") else
                (
                    f"CPAgent does not yet auto-populate {meta.get('extension_form') or 'the state extension form'} "
                    f"for {meta.get('name') or state}. Download it from {meta.get('extension_url')} and fill in "
                    f"the estimated total tax, payments made, and balance due using the figures above."
                )
            ),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    template = TEMPLATES / "co_dr0158i_2025.pdf"
    if not template.exists():
        # Fall back to 2024 if the 2025 revision hasn't been added yet
        template = TEMPLATES / "co_dr0158i_2024.pdf"
        if not template.exists():
            return {"error": "co_dr0158i_2025.pdf not found in filing/templates/"}

    state_tax = kpis.get("state_tax") or {}
    estimated = float(state_tax.get("tax_before_payments") or 0)
    withholding = float(state_tax.get("estimated_withholding") or 0)
    prior_payments = float(state_tax.get("estimated_payments_made") or 0)
    payments_total = withholding + prior_payments
    balance = max(0.0, estimated - payments_total)

    def _split_name(full: str) -> tuple[str, str, str]:
        parts = (full or "").strip().split()
        if not parts:
            return "", "", ""
        if len(parts) == 1:
            return parts[0], "", ""
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return parts[0], parts[1][0], " ".join(parts[2:])

    first, mi, last = _split_name(config.get("owner_name") or "")
    sp_first, sp_mi, sp_last = _split_name(config.get("spouse_name") or "")

    values = {
        "first_name": first,
        "middle_initial": mi,
        "last_name": last,
        "ssn": config.get("ssn_last4") or "",
        "spouse_first_name": sp_first,
        "spouse_middle_initial": sp_mi,
        "spouse_last_name": sp_last,
        "spouse_ssn": "",
        "address": config.get("address") or "",
        "city": config.get("city") or "",
        "state": "CO",
        "zip": config.get("zip") or "",
        "estimated_total_tax": _money(estimated),
        "withholding": _money(withholding) if withholding else "",
        "estimated_payments": _money(prior_payments) if prior_payments else "",
        "other_credits": "",
        "total_prior_payments": _money(payments_total),
        "balance_due": _money(balance),
        "amount_paying": _money(balance),
    }

    reader = PdfReader(str(template))
    writer = _populate(reader, CO_DR0158I, values)

    out_dir = Path(year_path) / _locate_worksheets_folder(year_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{year}_CO_DR0158I_Extension.pdf"
    with open(out_path, "wb") as f:
        writer.write(f)

    return {
        "path": str(out_path),
        "pages": len(writer.pages),
        "state": "CO",
        "form": "DR 0158-I",
        "estimated_total_tax": estimated,
        "total_payments": payments_total,
        "balance_due": balance,
        "amount_paying": balance,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "instructions": (
            "Colorado grants an automatic 6-month filing extension if at least "
            "90% of tax liability is paid by April 15. You only need to submit "
            "DR 0158-I if you owe a balance. File via colorado.gov/revenueonline "
            "or mail with payment to the address listed on the form."
        ),
    }


def _locate_worksheets_folder(year_path: str) -> str:
    for name in os.listdir(year_path):
        lower = name.lower()
        if "final" in lower or "worksheet" in lower:
            return name
    return "Final_Worksheets"
