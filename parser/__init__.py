"""Tax Dashboard Parsers — orchestrates all parsing for a given tax year."""

import os
import re
import json
import logging
from .csv_parser import parse_all_csvs, detect_csv_format
from .schedule_c import generate_all_schedule_cs
from .checklist import generate_checklist
from .strategy_engine import run_strategies

logger = logging.getLogger(__name__)


def _discover_spouse_w2(year_path):
    """Scan the year folder for a spouse/wife W-2 PDF and parse it.

    Returns a dict with {wages, employer_name, employee_name, federal_withholding,
    source_file} or None if no parsable W-2 found. Detection is folder-name based
    (matches 'wife' / 'spouse') with a fallback that inspects any *W2*.pdf or
    *W-2*.pdf filename anywhere in the year path.
    """
    from .pdf_parser import parse_w2_pdf
    candidates = []
    for name in os.listdir(year_path):
        full = os.path.join(year_path, name)
        if not os.path.isdir(full):
            continue
        lower = name.lower()
        if "wife" in lower or "spouse" in lower:
            for fn in os.listdir(full):
                if fn.lower().endswith(".pdf"):
                    candidates.append(os.path.join(full, fn))
    # Fallback: any file with W-2 in the filename across the whole year
    if not candidates:
        for root, _, files in os.walk(year_path):
            for fn in files:
                if re.search(r"w[-_\s]?2", fn, re.IGNORECASE) and fn.lower().endswith(".pdf"):
                    candidates.append(os.path.join(root, fn))

    total_wages = 0.0
    parsed = []
    for path in candidates:
        try:
            r = parse_w2_pdf(path)
        except Exception as e:
            logger.warning(f"W2 parse failed for {path}: {e}")
            continue
        if r.get("wages"):
            total_wages += float(r["wages"])
            parsed.append({
                "source_file": r.get("file"),
                "wages": r.get("wages"),
                "federal_withholding": r.get("federal_withholding"),
                "employer_name": r.get("employer_name"),
                "employee_name": r.get("employee_name"),
            })

    if not parsed:
        return None
    return {
        "total_wages": round(total_wages, 2),
        "w2s": parsed,
    }


def _apply_findings_engine(data, year, config=None):
    """Run inference + findings engine diff + attach readiness. Safe once per parse."""
    try:
        from inference.account_detector import detect_unknown_accounts
        unknown = detect_unknown_accounts(data, config or {})
        data.setdefault("findings", []).extend(unknown["findings"])
        data["unknown_accounts"] = unknown["unknown_accounts"]
    except Exception as e:
        logger.warning(f"unknown account detection failed: {e}", exc_info=True)
    try:
        from findings.engine import compute_findings, diff_and_persist
        import readiness as readiness_mod
        current = compute_findings(data.get("findings", []), year)
        diff = diff_and_persist(current, year)
        data["findings"] = diff["current"]
        data["findings_transitions"] = diff["transitions"]
        data["findings_summary"] = diff["summary"]
        data["findings_counts"] = diff["counts"]
        data["readiness"] = readiness_mod.compute_readiness(data)
    except Exception as e:
        logger.warning(f"findings engine hook failed: {e}", exc_info=True)
    return data


def _apply_gl_reclassifications(entity_csv_data, gl_result, entity_name):
    """Take auto-classified GL groups and adjust the P&L data.

    For each group where the GL reconciler found a user rule (LOGGED severity),
    subtract its amount from the originating uncategorized bucket (expense or
    income) and add to the proper Schedule C category so the generator picks
    it up.

    P&L data structure: pl_data.expenses = [{account_name, account_number, amount}]
    """
    pl = entity_csv_data.get("pl_data", {})
    expenses = pl.get("expenses", [])
    income = pl.get("income", [])
    findings = gl_result.get("findings", [])

    if not isinstance(expenses, list):
        return

    # Find the uncategorized expense entry
    uncat_expense_idx = None
    for i, e in enumerate(expenses):
        if "uncategorized" in (e.get("account_name") or "").lower():
            uncat_expense_idx = i
            break

    # Find the uncategorized income entry
    uncat_income_idx = None
    if isinstance(income, list):
        for i, entry in enumerate(income):
            if "uncategorized" in (entry.get("account_name") or "").lower():
                uncat_income_idx = i
                break

    if uncat_expense_idx is None and uncat_income_idx is None:
        return

    reclassified_expense_total = 0
    reclassified_income_total = 0

    for finding in findings:
        if finding.get("priority") != "LOGGED":
            continue
        suggestion = finding.get("suggestion")
        # Determine which uncategorized bucket this finding came from
        is_income_account = "income" in (finding.get("account") or "").lower()

        if not suggestion or suggestion.get("category") is None:
            # category=None means "Exclude from Schedule C" (e.g., IRS tax payments,
            # personal ATM deposits). Route reduction to the originating bucket.
            amount = abs(finding.get("group_total", 0))
            if is_income_account:
                reclassified_income_total += amount
            else:
                reclassified_expense_total += amount
            continue
        if suggestion.get("confidence", 0) < 0.9:
            continue

        amount = abs(finding.get("group_total", 0))
        category = suggestion["category"]

        if category == "Gross receipts":
            # This is income — add to income list with recognizable name
            income.append({
                "account_name": "Example Client Income",
                "account_number": "",
                "amount": amount,
                "_reclassified": True,
            })
            # Reduce the uncategorized income bucket
            reclassified_income_total += amount
        else:
            # Add as expense with the Schedule C category as the account name
            # so _lookup_mapping() in schedule_c.py can map it correctly
            expenses.append({
                "account_name": category,
                "account_number": "",
                "amount": amount,
                "_reclassified": True,
            })
            # Route reduction to the originating bucket
            if is_income_account:
                reclassified_income_total += amount
            else:
                reclassified_expense_total += amount

    # Reduce uncategorized expense by the expense-sourced reclassified total
    if reclassified_expense_total > 0 and uncat_expense_idx is not None:
        old_amount = abs(expenses[uncat_expense_idx].get("amount", 0))
        new_amount = max(0, old_amount - reclassified_expense_total)
        expenses[uncat_expense_idx]["amount"] = new_amount
        logger.info(
            f"{entity_name}: reclassified ${reclassified_expense_total:,.2f} from "
            f"Uncategorized Expense (${old_amount:,.2f} → ${new_amount:,.2f}) "
            f"via GL reconciler rules"
        )

    # Reduce uncategorized income by the income-sourced reclassified total
    if reclassified_income_total > 0 and uncat_income_idx is not None:
        old_amount = abs(income[uncat_income_idx].get("amount", 0))
        new_amount = max(0, old_amount - reclassified_income_total)
        income[uncat_income_idx]["amount"] = new_amount
        logger.info(
            f"{entity_name}: reclassified ${reclassified_income_total:,.2f} from "
            f"Uncategorized Income (${old_amount:,.2f} → ${new_amount:,.2f}) "
            f"via GL reconciler rules"
        )


def parse_year(year_path, config, tax_constants):
    """Parse all data for a given tax year directory.

    Returns a dict with keys: overview, schedule_c, findings, bank_recon, checklist, strategy, parse_errors
    """
    findings = []
    parse_errors = []

    # Discover entity folders
    entities = config.get("entities", [])
    entity_data = {}

    # Determine year number from the folder path for downstream use
    _year_num = 0
    try:
        import re as _re_local
        _m = _re_local.search(r"(\d{4})", os.path.basename(year_path))
        if _m:
            _year_num = int(_m.group(1))
    except Exception:
        pass

    for entity in entities:
        folder_name = entity.get("folder_name", "")
        entity_path = os.path.join(year_path, folder_name) if folder_name else None

        if entity_path and os.path.isdir(entity_path):
            csv_result = parse_all_csvs(entity_path, entity, config)
            entity_data[entity["name"]] = csv_result
            findings.extend(csv_result.get("findings", []))
            parse_errors.extend(csv_result.get("parse_errors", []))

            # GL reconciliation — drill into accounting.csv for uncategorized buckets
            try:
                from .gl_reconciler import reconcile_gl
                gl = reconcile_gl(entity_path, _year_num, entity["name"])
                entity_data[entity["name"]]["gl_reconciliation"] = gl
                findings.extend(gl.get("findings", []))

                # Apply auto-classified GL groups back to P&L data so Schedule C
                # picks them up: reduce Uncategorized Expense, add to proper category.
                _apply_gl_reclassifications(entity_data[entity["name"]], gl, entity["name"])
            except Exception as e:
                logger.warning(f"gl reconciliation failed for {entity['name']}: {e}")
        else:
            findings.append({
                "topic": "Missing Folder",
                "priority": "REQUIRED",
                "finding": f"Entity folder not found for {entity['name']}: {folder_name}",
            })

    # Generate Schedule C workpapers
    schedule_c_data = generate_all_schedule_cs(entity_data, entities, config, tax_constants)
    findings.extend(schedule_c_data.get("findings", []))

    # Document checklist
    checklist_data = generate_checklist(year_path, config)
    findings.extend(checklist_data.get("findings", []))

    # Spouse W-2 auto-parse
    spouse_w2 = _discover_spouse_w2(year_path)

    # Build overview from schedule C data
    overview = build_overview(schedule_c_data, entity_data, tax_constants, config, spouse_w2=spouse_w2, year=_year_num)

    # Strategy engine
    strategy_data = run_strategies(schedule_c_data, config, tax_constants)

    # Bank reconciliation placeholder (populated by PDF parser)
    bank_recon = {"accounts": [], "status": "pending"}

    # Sort findings by priority (new action-oriented taxonomy)
    priority_order = {"BLOCKED": 0, "REQUIRED": 1, "REVIEW": 2, "RESOLVED": 3, "LOGGED": 4}
    findings.sort(key=lambda f: priority_order.get(f.get("priority", "LOGGED"), 99))

    # Extract year from year_path for findings engine persistence
    year_num = 0
    try:
        import re as _re
        m = _re.search(r"(\d{4})", os.path.basename(year_path))
        if m:
            year_num = int(m.group(1))
    except Exception:
        pass

    data = {
        "overview": overview,
        "schedule_c": schedule_c_data,
        "findings": findings,
        "bank_recon": bank_recon,
        "checklist": checklist_data,
        "strategy": strategy_data,
        "parse_errors": parse_errors,
    }
    return _apply_findings_engine(data, year_num, config)


def build_overview(schedule_c_data, entity_data, tax_constants, config, spouse_w2=None, year=0):
    """Build overview KPI and chart data from parsed results."""
    schedules = schedule_c_data.get("schedules", [])

    total_revenue = 0
    total_expenses = 0
    total_net = 0
    revenue_by_entity = {}
    expense_categories = {}

    for sc in schedules:
        entity_name = sc["entity"]
        rev = sc.get("total_income", 0)
        exp = sc.get("total_expenses", 0)
        net = sc.get("net_profit", 0)

        total_revenue += rev
        total_expenses += exp
        total_net += net
        revenue_by_entity[entity_name] = rev

        for line in sc.get("expense_lines", []):
            cat = line["description"]
            expense_categories[cat] = expense_categories.get(cat, 0) + line["amount"]

    # Home office deduction (Schedule C Line 30) — computed from DB periods
    try:
        from parser.home_office import compute_total_deduction
        ho_result = compute_total_deduction(year or 2025)
        home_office_deduction = ho_result.get("total_deduction", 0)
    except Exception as e:
        logger.warning(f"Home office deduction failed: {e}")
        home_office_deduction = 0

    # Apply home office deduction to net profit (Line 30 → Line 31)
    total_net -= home_office_deduction

    # Self-employment tax: 15.3% of 92.35% of net SE earnings
    #   6.2% + 6.2% Social Security (capped at wage base) + 1.45% + 1.45% Medicare
    # Simplified formula here ignores the SS wage-base cap, which is 99% accurate
    # for self-employed incomes under ~$162k and within a few hundred dollars
    # above that.
    se_rate = tax_constants.get("se_tax_rate", 0.9235)
    se_fica = tax_constants.get("se_tax_fica_rate", 0.153)
    se_tax = total_net * se_rate * se_fica if total_net > 0 else 0

    # Federal income tax estimate:
    #   taxable = net_profit - (half of SE tax, an above-the-line deduction)
    #           - QBI deduction (if eligible)
    #           + spouse W-2 income
    #           - standard deduction
    #   income_tax ≈ taxable × marginal_rate  (rough; single bracket)
    #
    # We use the configured marginal rate from tax_constants/app_config rather
    # than computing brackets because this is a round-1 estimate. The Strategy
    # tab's S-Corp evaluator does the precise computation.
    filing_status = (config.get("filing_status") or "MFJ").upper()
    std_deduction_key = {
        "MFJ": "standard_deduction_mfj",
        "SINGLE": "standard_deduction_single",
        "HOH": "standard_deduction_hoh",
        "MFS": "standard_deduction_mfs",
    }.get(filing_status, "standard_deduction_mfj")
    std_deduction = tax_constants.get(std_deduction_key, 30000)
    marginal_rate = (
        config.get("preferences", {}).get("estimated_marginal_rate")
        or tax_constants.get("estimated_marginal_rate", 0.22)
    )
    qbi_rate = tax_constants.get("qbi_deduction_rate", 0.20)

    se_tax_above_line = se_tax / 2
    qbi_deduction = max(0.0, total_net * qbi_rate) if total_net > 0 else 0.0

    # Spouse W-2 income: prefer the auto-parsed total from the spouse/wife
    # folder; fall back to a manual override in app_config.preferences.
    spouse_w2_amount = 0.0
    spouse_w2_fed_wh = 0.0
    if spouse_w2 and spouse_w2.get("total_wages"):
        spouse_w2_amount = float(spouse_w2["total_wages"])
        spouse_w2_fed_wh = sum(
            float(w.get("federal_withholding") or 0) for w in spouse_w2.get("w2s", [])
        )
    else:
        spouse_w2_amount = float(config.get("preferences", {}).get("spouse_w2_income", 0) or 0)

    taxable_income = max(
        0.0,
        total_net - se_tax_above_line - qbi_deduction + spouse_w2_amount - std_deduction,
    )
    federal_income_tax = taxable_income * marginal_rate

    # Estimated payments already made — use the manual override in preferences.
    # The GL may contain IRS USATAXPYMT entries but these could be prior-year
    # balance-due payments (e.g., 2024 tax owed paid in 2025), not current-year
    # estimated payments. Only count payments the user has explicitly confirmed
    # as current-year estimated payments.
    est_payments_made = float(
        config.get("preferences", {}).get("estimated_tax_payments_made", 0) or 0
    )

    # Spouse federal withholding also reduces tax owed.
    estimated_tax_owed_before = se_tax + federal_income_tax
    estimated_tax_owed = max(
        0.0, estimated_tax_owed_before - est_payments_made - spouse_w2_fed_wh
    )

    # State tax — per-state registry in state_tax.py. Uses the entity's
    # configured state; currently only CO is fully implemented.
    try:
        import state_tax as state_tax_mod
        state_tax = state_tax_mod.compute_state_tax(
            config, federal_taxable_income=taxable_income, spouse_w2_income=spouse_w2_amount
        )
    except Exception as e:
        logger.warning(f"state tax computation failed: {e}")
        state_tax = {"state": None, "tax_owed": 0.0, "status": "error", "notes": [str(e)]}

    # Unclassified total
    unclassified = 0
    for sc in schedules:
        for acc in sc.get("unmapped_accounts", []):
            unclassified += abs(acc.get("amount", 0))

    return {
        "kpis": {
            "gross_receipts": total_revenue,
            "total_deductions": total_expenses,
            "net_profit": total_net,
            "se_tax_estimate": round(se_tax, 2),
            "self_employment_tax": round(se_tax, 2),
            "federal_income_tax": round(federal_income_tax, 2),
            "estimated_tax_owed_before_payments": round(estimated_tax_owed_before, 2),
            "estimated_payments_made": round(est_payments_made, 2),
            "estimated_tax_owed": round(estimated_tax_owed, 2),
            "qbi_deduction": round(qbi_deduction, 2),
            "standard_deduction": std_deduction,
            "marginal_rate": marginal_rate,
            "filing_status": filing_status,
            "home_office_deduction": round(home_office_deduction, 2),
            "vehicle_deduction": round(sum(
                line.get("amount", 0)
                for sc in schedules
                for line in sc.get("lines", [])
                if line.get("line") == "9"
            ), 2),
            "spouse_income": round(spouse_w2_amount, 2),
            "spouse_federal_withholding": round(spouse_w2_fed_wh, 2),
            "spouse_w2_details": (spouse_w2 or {}).get("w2s", []),
            "unclassified_total": unclassified,
            "state_tax": state_tax,
            "state_tax_owed": round(float(state_tax.get("tax_owed") or 0), 2),
            "state_tax_before_payments": round(float(state_tax.get("tax_before_payments") or 0), 2),
            "state": state_tax.get("state"),
        },
        "charts": {
            "revenue_by_entity": revenue_by_entity,
            "expense_categories": dict(sorted(expense_categories.items(), key=lambda x: -x[1])[:10]),
            "monthly_bank": {},  # populated by bank parser
        },
    }
