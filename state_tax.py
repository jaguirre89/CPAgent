"""Per-state tax estimation.

Colorado has a hand-tuned handler below because the TABOR refund mechanism
can adjust the flat rate year-to-year. Every other US state + DC uses the
generic registry in `states_registry.py`, which has flat-rate math for the
12 flat-rate states, a zero-tax stub for the 9 no-income-tax states, and
a framework stub for the progressive-bracket states (contributors welcome
to add bracket tables).

The architecture is: a registry maps two-letter state codes to handler
functions that compute state-level taxable income, tax owed, and payments
made, given the already-computed federal numbers. This lets future states
plug in as single-file additions without touching the parser.
"""

from __future__ import annotations

from typing import Callable, Optional

# ─── Colorado (CO) ────────────────────────────────────────────────────────────
#
# Flat income tax rate (TABOR-adjusted). 2024 return rate was 4.25% (reduced
# via TABOR refund); 2025 default statutory rate is 4.40%. The exact rate is
# published mid-year by the CO DOR. Until the 2025 rate is final, we use
# 4.40% and surface a REVIEW finding asking the user to confirm.
CO_FLAT_RATE = 0.0440
CO_RATE_SOURCE = "2025 CO DOR statutory flat income tax rate (confirm before filing — may be TABOR-adjusted)"


def compute_colorado(federal_taxable_income: float, spouse_w2_income: float = 0.0, spouse_withholding: float = 0.0, estimated_payments_made: float = 0.0) -> dict:
    """Compute CO state income tax estimate.

    Colorado's starting point is federal taxable income — same base we already
    compute in build_overview. CO then adds/subtracts state-specific modifications
    (TABOR refund, pension exclusion, 529 deduction, etc.) which we ignore in
    round 1 for simplicity. Result is tagged as a best-effort estimate.
    """
    co_taxable = max(0.0, federal_taxable_income)
    co_tax_before_payments = co_taxable * CO_FLAT_RATE
    # Colorado allows W-2 state withholding as a credit. We approximate from the
    # spouse W-2 (state_wages * rate) since most employers withhold at
    # approximately the flat rate.
    co_state_withholding_estimate = spouse_w2_income * CO_FLAT_RATE if spouse_w2_income else 0.0
    co_payments = co_state_withholding_estimate + estimated_payments_made
    co_tax_owed = max(0.0, co_tax_before_payments - co_payments)

    return {
        "state": "CO",
        "state_name": "Colorado",
        "flat_rate": CO_FLAT_RATE,
        "rate_source": CO_RATE_SOURCE,
        "taxable_income": round(co_taxable, 2),
        "tax_before_payments": round(co_tax_before_payments, 2),
        "estimated_withholding": round(co_state_withholding_estimate, 2),
        "estimated_payments_made": round(estimated_payments_made, 2),
        "tax_owed": round(co_tax_owed, 2),
        "forms": {
            "main": "DR 0104",
            "extension": "DR 0158-I",
            "estimated_payments": "DR 0104EP",
        },
        "notes": [
            "Starting point: federal taxable income. State-specific additions/subtractions (TABOR refund, pension exclusion, 529 contribution deduction, etc.) are NOT applied in this estimate.",
            "Spouse W-2 withholding is estimated at the flat rate; actual withholding comes from Box 17 of the W-2 (TODO: extract).",
            "Colorado automatically grants a 6-month filing extension if at least 90% of tax liability is paid by April 15. File DR 0158-I only if you owe a balance.",
        ],
    }


# ─── No-income-tax states ────────────────────────────────────────────────────
NO_INCOME_TAX_STATES = {"AK", "FL", "NV", "NH", "SD", "TN", "TX", "WA", "WY"}


def compute_no_tax(*_args, **_kwargs) -> dict:
    return {"state": None, "tax_owed": 0.0, "notes": ["No state income tax."]}


# ─── Registry ────────────────────────────────────────────────────────────────
HANDLERS: dict[str, Callable] = {
    "CO": compute_colorado,
}


def detect_state(config: dict) -> Optional[str]:
    """Pick the primary state from configured entities. Returns uppercase 2-letter code."""
    for e in config.get("entities", []) or []:
        st = (e.get("state") or "").strip().upper()
        if len(st) == 2:
            return st
    return None


def compute_state_tax(config: dict, federal_taxable_income: float, spouse_w2_income: float = 0.0) -> dict:
    """Compute state tax using the 50-state registry in states_registry.py.

    The local HANDLERS map (currently CO only) is tried FIRST for states
    that need custom logic (e.g., TABOR adjustments). If no custom handler
    exists, we fall through to the generic registry-based estimator which
    covers every state — flat-rate states get an accurate estimate, no-tax
    states return 0, progressive states return a framework stub with the
    extension form info still populated.
    """
    state = detect_state(config)
    if state is None:
        return {"state": None, "tax_owed": 0.0, "status": "unconfigured", "notes": ["No entity state in config."]}

    # Custom handlers take precedence
    handler = HANDLERS.get(state)
    if handler is not None:
        out = handler(federal_taxable_income, spouse_w2_income=spouse_w2_income)
        out["status"] = "ok"
        return out

    # Fall through to the generic 50-state registry
    try:
        import states_registry
        return states_registry.estimate_state_tax(
            state,
            federal_taxable_income=federal_taxable_income,
            spouse_w2_income=spouse_w2_income,
        )
    except Exception as e:
        return {"state": state, "tax_owed": 0.0, "status": "error", "notes": [str(e)]}
