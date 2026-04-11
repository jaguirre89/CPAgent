"""Tax strategy engine — rule-based evaluators for v1."""

import logging

logger = logging.getLogger(__name__)


def run_strategies(schedule_c_data, config, tax_constants):
    """Run all strategy evaluators against parsed data."""
    recommendations = []

    combined = schedule_c_data.get("combined", {})
    schedules = schedule_c_data.get("schedules", [])
    entities = config.get("entities", [])
    filing_status = config.get("filing_status", "MFJ")
    marginal_rate = config.get("preferences", {}).get("estimated_marginal_rate",
                    tax_constants.get("estimated_marginal_rate", 0.22))

    net_profit = combined.get("net_profit", 0)

    # Run each evaluator
    result = evaluate_s_corp_election(net_profit, schedules, entities, tax_constants)
    if result:
        recommendations.append(result)

    result = evaluate_retirement_contributions(net_profit, tax_constants, marginal_rate)
    if result:
        recommendations.append(result)

    result = evaluate_qbi_deduction(net_profit, filing_status, tax_constants)
    if result:
        recommendations.append(result)

    # Summary
    total_savings = sum(r.get("estimated_savings", 0) for r in recommendations)

    return {
        "recommendations": recommendations,
        "total_estimated_savings": round(total_savings, 2),
        "strategy_count": len(recommendations),
    }


def evaluate_s_corp_election(net_profit, schedules, entities, tax_constants):
    """Strategy 1: S-Corp Election analysis."""
    threshold = tax_constants.get("s_corp_se_threshold", 60000)

    if net_profit <= threshold:
        return None

    # Check if any entity is LLC (disregarded)
    has_disregarded = any(
        e.get("entity_type") == "LLC (disregarded)" for e in entities
    )
    if not has_disregarded:
        return None

    se_rate = tax_constants.get("se_tax_rate", 0.9235)
    se_fica = tax_constants.get("se_tax_fica_rate", 0.153)
    salary_pct = tax_constants.get("s_corp_reasonable_salary_pct", 0.60)

    # Current SE tax on full profit
    current_se_tax = net_profit * se_rate * se_fica

    # S-Corp: SE tax only on reasonable salary
    reasonable_salary = max(net_profit * salary_pct, 45000)
    reasonable_salary = min(reasonable_salary, net_profit)
    scorp_se_tax = reasonable_salary * se_fica

    savings = current_se_tax - scorp_se_tax
    # Subtract estimated payroll costs
    payroll_cost = 1000  # estimated annual payroll processing
    net_savings = savings - payroll_cost

    if net_savings <= 0:
        return None

    return {
        "id": "s_corp_election",
        "category": "Entity Structure",
        "title": "Consider S-Corp Election",
        "priority": "REQUIRED",
        "estimated_savings": round(net_savings, 2),
        "confidence": "MEDIUM",
        "summary": (
            f"Your combined net profit of ${net_profit:,.0f} exceeds the ${threshold:,.0f} threshold "
            f"where S-Corp election typically reduces self-employment tax."
        ),
        "detail": (
            f"As disregarded LLCs, you pay SE tax on 100% of net profit (~${current_se_tax:,.0f}). "
            f"Electing S-Corp status and paying yourself a reasonable salary of ${reasonable_salary:,.0f} "
            f"would limit SE tax to the salary only, saving approximately ${savings:,.0f}/year. "
            f"After estimated payroll processing costs (~${payroll_cost:,}/year), "
            f"net savings would be approximately ${net_savings:,.0f}/year. "
            f"Note: S-Corp election applies per entity — consult a CPA about which entity to elect."
        ),
        "action_items": [
            "Consult a CPA to confirm reasonable salary requirement for your industry",
            "File Form 2553 by March 15 to elect for the current tax year (or by March 15 of next year for next year)",
            "Set up payroll (Gusto, QuickBooks Payroll, or similar)",
            "Consider additional costs: payroll taxes, quarterly filings, separate tax return (Form 1120S)",
        ],
        "irs_reference": "IRC Section 1362 / Form 2553",
        "data_used": {
            "net_profit": net_profit,
            "current_se_tax": round(current_se_tax, 2),
            "reasonable_salary": round(reasonable_salary, 2),
            "scorp_se_tax": round(scorp_se_tax, 2),
            "savings_before_costs": round(savings, 2),
        },
    }


def evaluate_retirement_contributions(net_profit, tax_constants, marginal_rate):
    """Strategy 2: Solo 401(k) / SEP-IRA contribution analysis."""
    if net_profit <= 0:
        return None

    se_rate = tax_constants.get("se_tax_rate", 0.9235)
    se_fica = tax_constants.get("se_tax_fica_rate", 0.153)

    # Net SE income after SE tax deduction
    se_tax = net_profit * se_rate * se_fica
    net_se_income = net_profit - (se_tax / 2)  # half of SE tax is deductible

    # SEP-IRA: 25% of net SE income
    sep_pct = tax_constants.get("sep_ira_pct", 0.25)
    sep_limit = tax_constants.get("sep_ira_limit", 70000)
    sep_max = min(net_se_income * sep_pct, sep_limit)

    # Solo 401(k): employee deferral + employer contribution
    emp_limit = tax_constants.get("solo_401k_employee_deferral_limit", 23500)
    employer_pct = tax_constants.get("solo_401k_employer_pct", 0.25)
    total_limit = tax_constants.get("solo_401k_total_limit", 70000)

    employee_contribution = min(emp_limit, net_se_income)
    employer_contribution = min(net_se_income * employer_pct, total_limit - employee_contribution)
    solo_401k_max = min(employee_contribution + employer_contribution, total_limit)

    # Tax savings at marginal rate
    best_contribution = max(sep_max, solo_401k_max)
    best_name = "Solo 401(k)" if solo_401k_max >= sep_max else "SEP-IRA"
    tax_savings = best_contribution * marginal_rate

    return {
        "id": "retirement_contribution",
        "category": "Retirement",
        "title": f"{best_name} Contribution Opportunity",
        "priority": "REQUIRED",
        "estimated_savings": round(tax_savings, 2),
        "confidence": "HIGH",
        "summary": (
            f"Contributing ${best_contribution:,.0f} to a {best_name} would reduce your "
            f"taxable income, saving approximately ${tax_savings:,.0f} in federal income tax "
            f"at your estimated {marginal_rate*100:.0f}% marginal rate."
        ),
        "detail": (
            f"SEP-IRA max contribution: ${sep_max:,.0f} (25% of net SE income).\n"
            f"Solo 401(k) max: ${solo_401k_max:,.0f} "
            f"(${employee_contribution:,.0f} employee + ${employer_contribution:,.0f} employer).\n\n"
            f"The Solo 401(k) typically allows a higher total contribution because it includes "
            f"an employee elective deferral of up to ${emp_limit:,}. "
            f"Both must be established and funded by the tax filing deadline (with extensions)."
        ),
        "action_items": [
            f"Open a Solo 401(k) or SEP-IRA before December 31 of the tax year (for employee deferrals)",
            f"Contribute up to ${best_contribution:,.0f} before filing deadline",
            "Consult a financial advisor about investment allocation",
            "Track contributions for Form 1040 Line 15 (SEP) or Line 16 (Solo 401k)",
        ],
        "irs_reference": "IRC Section 401(k) / Section 408(k) — IRS Publication 560",
        "data_used": {
            "net_profit": net_profit,
            "net_se_income": round(net_se_income, 2),
            "sep_ira_max": round(sep_max, 2),
            "solo_401k_max": round(solo_401k_max, 2),
            "employee_deferral": round(employee_contribution, 2),
            "employer_contribution": round(employer_contribution, 2),
            "marginal_rate": marginal_rate,
        },
    }


def evaluate_qbi_deduction(net_profit, filing_status, tax_constants):
    """Strategy 3: Qualified Business Income (QBI) Deduction — Section 199A."""
    if net_profit <= 0:
        return None

    qbi_rate = tax_constants.get("qbi_deduction_rate", 0.20)
    phase_out_start = tax_constants.get("qbi_phase_out_mfj", 383900)
    phase_out_end = tax_constants.get("qbi_phase_out_mfj_end", 483900)
    marginal_rate = tax_constants.get("estimated_marginal_rate", 0.22)

    # QBI deduction = 20% of qualified business income
    qbi_deduction = net_profit * qbi_rate
    tax_savings = qbi_deduction * marginal_rate

    # Phase-out warning
    phase_out_note = ""
    if net_profit > phase_out_start * 0.8:
        phase_out_note = (
            f"\n\nNote: The QBI deduction begins to phase out for MFJ filers with "
            f"taxable income above ${phase_out_start:,}. Your business income alone is "
            f"${net_profit:,.0f} — when combined with spouse W-2 income and other sources, "
            f"check if total taxable income approaches this threshold."
        )

    return {
        "id": "qbi_deduction",
        "category": "Deductions",
        "title": "Qualified Business Income (QBI) Deduction",
        "priority": "REQUIRED",
        "estimated_savings": round(tax_savings, 2),
        "confidence": "HIGH",
        "summary": (
            f"You may be eligible for a QBI deduction of up to ${qbi_deduction:,.0f}, "
            f"reducing your taxable income without any cash outlay."
        ),
        "detail": (
            f"Section 199A allows a deduction of {qbi_rate*100:.0f}% of qualified business income. "
            f"With combined net profit of ${net_profit:,.0f}, the maximum QBI deduction is "
            f"${qbi_deduction:,.0f}. At your estimated {marginal_rate*100:.0f}% marginal rate, "
            f"this saves approximately ${tax_savings:,.0f} in federal income tax. "
            f"This deduction is taken on Form 1040, not Schedule C — it reduces taxable income "
            f"but not self-employment income.{phase_out_note}"
        ),
        "action_items": [
            "QBI deduction is automatic if eligible — no special election needed",
            "Verify your total taxable income stays below the phase-out threshold",
            "Specified service trades (law, health, consulting, etc.) have additional limitations above the threshold",
            "Your CPA should compute the exact deduction on Form 8995 or 8995-A",
        ],
        "irs_reference": "IRC Section 199A — Form 8995 / 8995-A",
        "data_used": {
            "net_profit": net_profit,
            "qbi_rate": qbi_rate,
            "qbi_deduction": round(qbi_deduction, 2),
            "filing_status": filing_status,
            "phase_out_start": phase_out_start,
        },
    }
