"""Schedule C workpaper generation — one per entity."""

import logging
from .csv_parser import ACCOUNT_TO_SCHEDULE_C, SCHEDULE_C_LINE_MAP

logger = logging.getLogger(__name__)

# Standard Schedule C line order
SCHEDULE_C_LINES = [
    # Part I - Income
    {"line": "1", "description": "Gross receipts or sales", "section": "income"},
    {"line": "2", "description": "Returns and allowances", "section": "income"},
    {"line": "3", "description": "Line 1 minus Line 2", "section": "income", "computed": True},
    {"line": "4", "description": "Cost of goods sold", "section": "income"},
    {"line": "5", "description": "Gross profit (Line 3 minus Line 4)", "section": "income", "computed": True},
    {"line": "6", "description": "Other income", "section": "income"},
    {"line": "7", "description": "Gross income (Line 5 plus Line 6)", "section": "income", "computed": True},

    # Part II - Expenses
    {"line": "8", "description": "Advertising", "section": "expenses"},
    {"line": "9", "description": "Car and truck expenses", "section": "expenses"},
    {"line": "10", "description": "Commissions and fees", "section": "expenses"},
    {"line": "11", "description": "Contract labor", "section": "expenses"},
    {"line": "12", "description": "Depletion", "section": "expenses"},
    {"line": "13", "description": "Depreciation and section 179", "section": "expenses"},
    {"line": "14", "description": "Employee benefit programs", "section": "expenses"},
    {"line": "15", "description": "Insurance (other than health)", "section": "expenses"},
    {"line": "16a", "description": "Interest (mortgage)", "section": "expenses"},
    {"line": "16b", "description": "Interest (other)", "section": "expenses"},
    {"line": "17", "description": "Legal and professional services", "section": "expenses"},
    {"line": "18", "description": "Office expense", "section": "expenses"},
    {"line": "19", "description": "Pension and profit-sharing plans", "section": "expenses"},
    {"line": "20a", "description": "Rent - vehicles, machinery, equipment", "section": "expenses"},
    {"line": "20b", "description": "Rent - other business property", "section": "expenses"},
    {"line": "21", "description": "Repairs and maintenance", "section": "expenses"},
    {"line": "22", "description": "Supplies", "section": "expenses"},
    {"line": "23", "description": "Taxes and licenses", "section": "expenses"},
    {"line": "24a", "description": "Travel", "section": "expenses"},
    {"line": "24b", "description": "Deductible meals (50% limit)", "section": "expenses"},
    {"line": "25", "description": "Utilities", "section": "expenses"},
    {"line": "26", "description": "Wages", "section": "expenses"},
    {"line": "27a", "description": "Other expenses", "section": "expenses"},
    {"line": "28", "description": "Total expenses (Lines 8 through 27a)", "section": "expenses", "computed": True},

    # Net
    {"line": "29", "description": "Tentative profit (Line 7 minus Line 28)", "section": "net", "computed": True},
    {"line": "30", "description": "Expenses for business use of home", "section": "net"},
    {"line": "31", "description": "Net profit or (loss)", "section": "net", "computed": True},
]


def generate_schedule_c(entity_name, entity_data, entity_config, tax_constants):
    """Generate a single Schedule C workpaper for one entity."""
    pl_data = entity_data.get("pl_data")
    findings = []
    unmapped = []

    # Initialize line amounts
    line_amounts = {}
    line_sources = {}  # track which accounts map to each line
    for ldef in SCHEDULE_C_LINES:
        line_amounts[ldef["line"]] = 0
        line_sources[ldef["line"]] = []

    if not pl_data:
        return {
            "entity": entity_name,
            "lines": _build_line_output(line_amounts, line_sources),
            "total_income": 0,
            "total_expenses": 0,
            "net_profit": 0,
            "unmapped_accounts": [],
            "findings": [{"topic": "Missing Data", "priority": "BLOCKED",
                          "finding": f"No P&L data available for {entity_name}. Cannot generate Schedule C."}],
            "income_lines": [],
            "expense_lines": [],
        }

    meals_full_amount = 0

    # Map income accounts
    for acct in pl_data.get("income", []):
        name = acct["account_name"]
        amount = acct["amount"]
        sch_c_cat = _lookup_mapping(name, entity_config)

        if sch_c_cat is None:
            # Explicitly excluded (non-taxable or non-Schedule C)
            if name == "Uncategorized Income":
                if amount > 0.01:
                    findings.append({
                        "topic": "Uncategorized Income",
                        "priority": "BLOCKED",
                        "finding": f"{entity_name}: ${amount:,.2f} in uncategorized income needs classification. Review and assign to proper income category.",
                    })
                    # Still include in gross receipts for now
                    line_amounts["1"] += amount
                    line_sources["1"].append({"account": name, "amount": amount, "flag": "UNCATEGORIZED"})
            continue

        line_info = SCHEDULE_C_LINE_MAP.get(sch_c_cat)
        if line_info:
            line_num = line_info["line"]
            if name == "Sales Discounts":
                # Returns/allowances: make positive for line 2
                line_amounts[line_num] += abs(amount)
                line_sources[line_num].append({"account": name, "amount": abs(amount)})
            else:
                line_amounts[line_num] += amount
                line_sources[line_num].append({"account": name, "amount": amount})
            # Flag Cashback Rewards for review
            if name == "Cashback Rewards":
                findings.append({
                    "topic": "Cashback Rewards",
                    "priority": "REVIEW",
                    "finding": f"{entity_name}: Cashback rewards (${amount:,.2f}) included in gross receipts. "
                               f"Generally NOT taxable (purchase rebate) — verify before including.",
                })
        else:
            unmapped.append({"account": name, "amount": amount, "type": "income"})

    # Map expense accounts
    for acct in pl_data.get("expenses", []):
        name = acct["account_name"]
        amount = acct["amount"]
        sch_c_cat = _lookup_mapping(name, entity_config)

        if sch_c_cat is None:
            # Check specific exclusions
            if "management fee" in name.lower():
                findings.append({
                    "topic": "Intercompany Management Fee",
                    "priority": "REQUIRED",
                    "finding": f"{entity_name}: Management fees of ${amount:,.2f} ELIMINATED — "
                               f"intercompany payment between consolidated entities on one return.",
                    "intercompany": True,
                    "elimination": True,
                })
            elif "charitable" in name.lower() or "donation" in name.lower():
                findings.append({
                    "topic": "Charitable Contributions",
                    "priority": "REQUIRED",
                    "finding": f"{entity_name}: Charitable contributions (${amount:,.2f}) found in business expenses. "
                               f"These are NOT deductible on Schedule C — they belong on Schedule A (if itemizing). "
                               f"Remove from business expenses.",
                })
            elif "non-tax" in name.lower() or "non-deductible" in name.lower():
                findings.append({
                    "topic": "Non-Deductible Expense",
                    "priority": "LOGGED",
                    "finding": f"{entity_name}: {name} (${amount:,.2f}) correctly excluded from Schedule C.",
                })
            elif "tax payment" in name.lower():
                findings.append({
                    "topic": "Tax Payments",
                    "priority": "LOGGED",
                    "finding": f"{entity_name}: Tax payments (${amount:,.2f}) excluded from Schedule C. "
                               f"Estimated tax payments go on Form 1040, not Schedule C.",
                })
            elif "uncategorized" in name.lower():
                if amount > 0.01:
                    findings.append({
                        "topic": "Uncategorized Expense",
                        "priority": "BLOCKED",
                        "finding": f"{entity_name}: ${amount:,.2f} in uncategorized expenses needs classification. "
                                   f"Review and assign to proper Schedule C expense lines.",
                    })
                    unmapped.append({"account": name, "amount": amount, "type": "expense"})
            else:
                unmapped.append({"account": name, "amount": amount, "type": "expense"})
            continue

        line_info = SCHEDULE_C_LINE_MAP.get(sch_c_cat)
        if line_info:
            line_num = line_info["line"]
            # Exclude negative expense amounts (refunds/corrections) as immaterial
            if amount < 0:
                findings.append({
                    "topic": "Expense Refund/Credit",
                    "priority": "LOGGED",
                    "finding": f"{entity_name}: {name} (${amount:,.2f}) — negative expense (refund/correction), excluded.",
                    "elimination": True,
                })
                continue
            # Track full meals amount before 50% limit
            if sch_c_cat == "Meals (50% limit)":
                meals_full_amount += amount
            line_amounts[line_num] += amount
            line_sources[line_num].append({"account": name, "amount": amount})
            # Flag tax payments shown on Sch C but that actually go on 1040
            if "tax payment" in name.lower() or "federal tax" in name.lower():
                findings.append({
                    "topic": "Tax Payments on Sch C",
                    "priority": "LOGGED",
                    "finding": f"{entity_name}: {name} (${amount:,.2f}) shown on Line 23 but estimated "
                               f"tax payments go on Form 1040, not Schedule C.",
                })
        else:
            unmapped.append({"account": name, "amount": amount, "type": "expense"})

    # Meals 50% limit — keep full amount on Line 24b, track deductible separately
    meals_pct = tax_constants.get("meals_deductibility_pct", 0.50)
    meals_deductible = 0
    meals_adjustment = 0
    if meals_full_amount > 0:
        meals_deductible = round(meals_full_amount * meals_pct, 2)
        meals_adjustment = round(meals_full_amount - meals_deductible, 2)
        # Line 24b shows full amount; 50% limit applied in totals
        findings.append({
            "topic": "Meals - 50% Limit",
            "priority": "LOGGED",
            "finding": f"{entity_name}: Total meals expense is ${meals_full_amount:,.2f}. "
                       f"Subject to 50% limit → actual deduction = ${meals_deductible:,.2f}.",
        })

    # Unmapped accounts findings
    for item in unmapped:
        if "uncategorized" not in item["account"].lower():
            findings.append({
                "topic": "Unmapped Expense",
                "priority": "REVIEW",
                "finding": f"{entity_name}: '{item['account']}' (${item['amount']:,.2f}) is not mapped to a Schedule C line. "
                           f"Assign it in Settings or the CSV mapping.",
            })

    # Compute derived lines
    line_amounts["3"] = line_amounts["1"] - line_amounts["2"]
    line_amounts["5"] = line_amounts["3"] - line_amounts["4"]
    line_amounts["7"] = line_amounts["5"] + line_amounts["6"]

    # Total expenses (lines 8 through 27a)
    expense_line_nums = ["8", "9", "10", "11", "12", "13", "14", "15", "16a", "16b",
                         "17", "18", "19", "20a", "20b", "21", "22", "23", "24a", "24b",
                         "25", "26", "27a"]
    total_expenses = sum(line_amounts.get(ln, 0) for ln in expense_line_nums)
    line_amounts["28"] = round(total_expenses, 2)

    line_amounts["29"] = round(line_amounts["7"] - line_amounts["28"], 2)
    line_amounts["31"] = round(line_amounts["29"] - line_amounts["30"], 2)

    # Build output
    lines_output = _build_line_output(line_amounts, line_sources)

    # Separate income and expense line lists for overview charts
    income_lines = [l for l in lines_output if l["section"] == "income" and l["amount"] != 0 and not l.get("computed")]
    expense_lines = [l for l in lines_output if l["section"] == "expenses" and l["amount"] != 0 and not l.get("computed")]

    return {
        "entity": entity_name,
        "entity_type": entity_config.get("entity_type", "LLC (disregarded)"),
        "lines": lines_output,
        "total_income": line_amounts["7"],
        "total_expenses": line_amounts["28"],
        "net_profit": line_amounts["31"],
        "unmapped_accounts": unmapped,
        "findings": findings,
        "meals_full_amount": meals_full_amount,
        "meals_deductible": meals_deductible,
        "meals_adjustment": meals_adjustment,
        "income_lines": income_lines,
        "expense_lines": expense_lines,
    }


def _lookup_mapping(account_name, entity_config):
    """Look up Schedule C category for an account name.
    Returns category string, None (explicitly excluded), or 'UNMAPPED' to flag.

    Resolution order:
      1. categorization_rules store (seed + user + llm_suggested rules)
      2. Fuzzy keyword heuristics (generic, not persisted)
    The hardcoded ACCOUNT_TO_SCHEDULE_C dict still lives in csv_parser.py but
    is only used as the seed source for the rules store — runtime lookups go
    through the store so user corrections persist.
    """
    # Direct match: if the account name IS already a Schedule C category, return it
    valid_categories = {
        "Advertising", "Car and truck expenses", "Commissions and fees", "Contract labor",
        "Depreciation", "Employee benefit programs", "Insurance (other than health)",
        "Interest (mortgage)", "Interest (other)", "Legal and professional",
        "Office expense", "Pension and profit-sharing", "Rent - vehicles/equipment",
        "Rent - other", "Repairs and maintenance", "Supplies", "Taxes and licenses",
        "Travel", "Meals (50% limit)", "Utilities", "Wages", "Other expenses",
        "Gross receipts", "Returns and allowances",
    }
    if account_name in valid_categories:
        return account_name

    try:
        from learning import rules_store
        entity_scope = entity_config.get("name") if entity_config else None
        hit = rules_store.match(account_name=account_name, entity_scope=entity_scope)
        if hit:
            return hit["schedule_c_category"]
    except Exception as e:
        # Never let a store hiccup break the parse pipeline; fall through to heuristics.
        import logging
        logging.getLogger(__name__).warning(f"rules_store lookup failed: {e}")

    # Fuzzy keyword matching
    name_lower = account_name.lower()
    keyword_map = {
        "advertising": "Advertising",
        "insurance": "Insurance (other than health)",
        "subcontract": "Contract labor",
        "contractor": "Contract labor",
        "meal": "Meals (50% limit)",
        "travel": "Travel",
        "rent": "Rent - other",
        "subscription": "Other expenses",
        "software": "Other expenses",
        "hosting": "Other expenses",
        "internet": "Utilities",
        "phone": "Utilities",
        "telephone": "Utilities",
        "wireless": "Utilities",
        "office": "Office expense",
        "postage": "Other expenses",
        "delivery": "Other expenses",
        "fuel": "Car and truck expenses",
        "vehicle": "Car and truck expenses",
        "repair": "Repairs and maintenance",
        "maintenance": "Repairs and maintenance",
        "interest": "Interest (other)",
        "professional": "Legal and professional",
        "legal": "Legal and professional",
        "accounting": "Legal and professional",
        "depreciation": "Depreciation",
        "education": "Other expenses",
        "training": "Other expenses",
        "conference": "Other expenses",
        "bank": "Other expenses",
        "merchant": "Other expenses",
        "storage": "Rent - other",
        "parking": "Other expenses",
    }

    for keyword, category in keyword_map.items():
        if keyword in name_lower:
            return category

    return "UNMAPPED"


def _build_line_output(line_amounts, line_sources):
    """Build the final line-by-line output."""
    output = []
    for ldef in SCHEDULE_C_LINES:
        line_num = ldef["line"]
        output.append({
            "line": line_num,
            "description": ldef["description"],
            "amount": round(line_amounts.get(line_num, 0), 2),
            "section": ldef["section"],
            "computed": ldef.get("computed", False),
            "sources": line_sources.get(line_num, []),
        })
    return output


def generate_all_schedule_cs(entity_data, entities, config, tax_constants):
    """Generate Schedule C workpapers for all entities."""
    schedules = []
    findings = []

    for entity in entities:
        entity_name = entity["name"]
        data = entity_data.get(entity_name, {})
        entity_type = entity.get("entity_type", "LLC (disregarded)")

        # S-Corp or C-Corp warning
        if entity_type in ("LLC (S-Corp elected)", "S-Corp"):
            findings.append({
                "topic": "Entity Type Warning",
                "priority": "REQUIRED",
                "finding": f"{entity_name} is configured as {entity_type}. "
                           f"Income should be reported on Schedule E / K-1, NOT Schedule C. "
                           f"Review entity configuration in Settings.",
            })
        elif entity_type == "C-Corp":
            findings.append({
                "topic": "Entity Type Warning",
                "priority": "REQUIRED",
                "finding": f"{entity_name} is a C-Corp. A separate corporate return (Form 1120) is required. "
                           f"This entity is excluded from Schedule C.",
            })
            continue

        sc = generate_schedule_c(entity_name, data, entity, tax_constants)
        schedules.append(sc)
        findings.extend(sc["findings"])

    # Cross-entity: verify intercompany amounts match
    mgmt_fee_paid = 0
    mgmt_fee_received = 0
    for sc in schedules:
        for line in sc["lines"]:
            for src in line.get("sources", []):
                if "management fee" in src.get("account", "").lower():
                    if line["section"] == "expenses":
                        mgmt_fee_paid += src["amount"]
                    elif line["section"] == "income":
                        mgmt_fee_received += src["amount"]

    if mgmt_fee_paid > 0 and abs(mgmt_fee_paid - mgmt_fee_received) > 1.0:
        findings.append({
            "topic": "Intercompany Mismatch",
            "priority": "REQUIRED",
            "finding": f"Intercompany management fees don't match across entities. "
                       f"Paid: ${mgmt_fee_paid:,.2f}, Received: ${mgmt_fee_received:,.2f}. "
                       f"Verify both P&L exports include the management fee entries.",
        })

    # Combined summary
    combined_revenue = sum(sc["total_income"] for sc in schedules)
    combined_expenses = sum(sc["total_expenses"] for sc in schedules)
    combined_net = sum(sc["net_profit"] for sc in schedules)

    se_rate = tax_constants.get("se_tax_rate", 0.9235)
    se_fica = tax_constants.get("se_tax_fica_rate", 0.153)

    return {
        "schedules": schedules,
        "combined": {
            "total_revenue": round(combined_revenue, 2),
            "total_expenses": round(combined_expenses, 2),
            "net_profit": round(combined_net, 2),
            "se_tax_base": round(combined_net * se_rate, 2) if combined_net > 0 else 0,
            "se_tax": round(combined_net * se_rate * se_fica, 2) if combined_net > 0 else 0,
        },
        "findings": findings,
    }
