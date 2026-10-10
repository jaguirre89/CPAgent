"""All 50 states + DC — income tax metadata and extension form references.

For each state we record:
  - name (full name)
  - has_income_tax
  - rate_type ("flat" | "progressive" | "none")
  - flat_rate (decimal, if flat) — best available 2025 rate; verify before filing
  - extension_form (identifier used by the state DOR)
  - extension_url (canonical landing page for the extension form)
  - extension_auto_granted (bool) — some states grant an automatic extension
        as long as you pay the estimated tax by the original deadline; in
        those cases the extension form is only needed if you owe a balance
  - notes (free text for anything unusual)

CPAgent's tax engine calls `estimate(state, federal_taxable_income, ...)` to
get a rough per-state liability. States with `rate_type="flat"` compute
instantly from federal taxable income × rate. States with progressive
brackets return a flag indicating the user needs to verify manually
(computing bracket math for 40+ progressive-tax states is out of scope for
round 1; the framework is in place for contributors to add).

Form-level PDF field mapping is ONLY implemented for Colorado (DR 0158-I)
in round 1. For all other states the extension endpoint returns a
structured response with the form name, DOR URL, and computed balance-due
figure so the user can file manually in 30 seconds.

**Rates below are round-1 defaults and must be verified against the state's
published 2025 rate before any actual filing. Many states adjust their
rates annually.**
"""

from __future__ import annotations

from typing import Optional

STATES: dict[str, dict] = {
    # No income tax (9 states)
    "AK": {"name": "Alaska",        "has_income_tax": False, "rate_type": "none",        "extension_form": None,       "extension_url": None,                                                                     "extension_auto_granted": True,  "notes": "No state individual income tax."},
    "FL": {"name": "Florida",       "has_income_tax": False, "rate_type": "none",        "extension_form": None,       "extension_url": None,                                                                     "extension_auto_granted": True,  "notes": "No state individual income tax."},
    "NV": {"name": "Nevada",        "has_income_tax": False, "rate_type": "none",        "extension_form": None,       "extension_url": None,                                                                     "extension_auto_granted": True,  "notes": "No state individual income tax."},
    "NH": {"name": "New Hampshire", "has_income_tax": False, "rate_type": "none",        "extension_form": None,       "extension_url": "https://www.revenue.nh.gov/forms/index.htm",                             "extension_auto_granted": True,  "notes": "No tax on wages. Interest & dividends tax is being phased out (0% in 2025)."},
    "SD": {"name": "South Dakota",  "has_income_tax": False, "rate_type": "none",        "extension_form": None,       "extension_url": None,                                                                     "extension_auto_granted": True,  "notes": "No state individual income tax."},
    "TN": {"name": "Tennessee",     "has_income_tax": False, "rate_type": "none",        "extension_form": None,       "extension_url": None,                                                                     "extension_auto_granted": True,  "notes": "Hall tax on investment income was repealed in 2021."},
    "TX": {"name": "Texas",         "has_income_tax": False, "rate_type": "none",        "extension_form": None,       "extension_url": None,                                                                     "extension_auto_granted": True,  "notes": "No state individual income tax."},
    "WA": {"name": "Washington",    "has_income_tax": False, "rate_type": "none",        "extension_form": None,       "extension_url": None,                                                                     "extension_auto_granted": True,  "notes": "No state individual income tax. Capital gains excise tax applies above a threshold."},
    "WY": {"name": "Wyoming",       "has_income_tax": False, "rate_type": "none",        "extension_form": None,       "extension_url": None,                                                                     "extension_auto_granted": True,  "notes": "No state individual income tax."},

    # Flat-rate states
    "AZ": {"name": "Arizona",       "has_income_tax": True,  "rate_type": "flat",         "flat_rate": 0.025,   "extension_form": "Form 204",       "extension_url": "https://azdor.gov/forms/individual/application-filing-extension",          "extension_auto_granted": False, "notes": "Flat 2.5% rate."},
    "CO": {"name": "Colorado",      "has_income_tax": True,  "rate_type": "flat",         "flat_rate": 0.044,   "extension_form": "DR 0158-I",      "extension_url": "https://tax.colorado.gov/DR0158-I",                                        "extension_auto_granted": True,  "notes": "Automatic 6-month extension granted if ≥90% of tax is paid by April 15. Statutory rate is 4.40% (may be TABOR-adjusted). CPAgent fully supports CO form generation."},
    "IL": {"name": "Illinois",      "has_income_tax": True,  "rate_type": "flat",         "flat_rate": 0.0495,  "extension_form": "IL-505-I",       "extension_url": "https://tax.illinois.gov/forms/incometax/currentyear/individual.html",      "extension_auto_granted": True,  "notes": "Automatic extension to Oct 15 — file IL-505-I only if you owe tax."},
    "IN": {"name": "Indiana",       "has_income_tax": True,  "rate_type": "flat",         "flat_rate": 0.0305,  "extension_form": "IT-9",           "extension_url": "https://www.in.gov/dor/tax-forms/",                                         "extension_auto_granted": True,  "notes": "Accepts federal extension as state extension if no balance due."},
    "KY": {"name": "Kentucky",      "has_income_tax": True,  "rate_type": "flat",         "flat_rate": 0.04,    "extension_form": "Form 740EXT",    "extension_url": "https://revenue.ky.gov/Forms/",                                             "extension_auto_granted": False, "notes": "Flat 4.0% rate."},
    "MA": {"name": "Massachusetts", "has_income_tax": True,  "rate_type": "flat",         "flat_rate": 0.05,    "extension_form": "Form M-4868",    "extension_url": "https://www.mass.gov/lists/personal-income-tax-forms-and-instructions",     "extension_auto_granted": True,  "notes": "Automatic extension if ≥80% paid by April 15. 4% surtax on income >$1M."},
    "MI": {"name": "Michigan",      "has_income_tax": True,  "rate_type": "flat",         "flat_rate": 0.0425,  "extension_form": "Form 4",         "extension_url": "https://www.michigan.gov/taxes/forms",                                      "extension_auto_granted": False, "notes": "Flat 4.25% rate."},
    "NC": {"name": "North Carolina","has_income_tax": True,  "rate_type": "flat",         "flat_rate": 0.045,   "extension_form": "Form D-410",     "extension_url": "https://www.ncdor.gov/taxes-forms/individual-income-tax",                   "extension_auto_granted": False, "notes": "Flat 4.5% rate."},
    "PA": {"name": "Pennsylvania",  "has_income_tax": True,  "rate_type": "flat",         "flat_rate": 0.0307,  "extension_form": "REV-276",        "extension_url": "https://www.revenue.pa.gov/Forms/",                                         "extension_auto_granted": True,  "notes": "Automatic extension if federal Form 4868 was filed. Flat 3.07% rate."},
    "UT": {"name": "Utah",          "has_income_tax": True,  "rate_type": "flat",         "flat_rate": 0.0465,  "extension_form": "TC-546",         "extension_url": "https://tax.utah.gov/forms",                                                "extension_auto_granted": True,  "notes": "Automatic extension if prepaid ≥90% of tax liability."},

    # Progressive states — rate_type="progressive" signals the engine to surface
    # a REVIEW finding asking the user to compute manually (or contribute a
    # bracket table for that state).
    "AL": {"name": "Alabama",        "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form 40V",          "extension_url": "https://www.revenue.alabama.gov/individual-corporate/",                      "extension_auto_granted": False},
    "AR": {"name": "Arkansas",       "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form AR1055-IT",    "extension_url": "https://www.dfa.arkansas.gov/income-tax/individual-income-tax/",             "extension_auto_granted": False},
    "CA": {"name": "California",     "has_income_tax": True, "rate_type": "progressive", "extension_form": "FTB 3519",          "extension_url": "https://www.ftb.ca.gov/forms/",                                              "extension_auto_granted": True, "notes": "Automatic 6-month extension to Oct 15 — FTB 3519 only required if paying tax."},
    "CT": {"name": "Connecticut",    "has_income_tax": True, "rate_type": "progressive", "extension_form": "CT-1040 EXT",       "extension_url": "https://portal.ct.gov/DRS/",                                                 "extension_auto_granted": False},
    "DE": {"name": "Delaware",       "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form 1027I",        "extension_url": "https://revenue.delaware.gov/personal-income-tax-forms/",                    "extension_auto_granted": False},
    "DC": {"name": "District of Columbia", "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form FR-127", "extension_url": "https://otr.cfo.dc.gov/page/individual-income-tax-forms",                   "extension_auto_granted": False},
    "GA": {"name": "Georgia",        "has_income_tax": True, "rate_type": "flat",         "flat_rate": 0.0539,  "extension_form": "Form IT-303",      "extension_url": "https://dor.georgia.gov/taxes/individual-taxes",                            "extension_auto_granted": True,  "notes": "GA moved to flat 5.39% rate in 2025."},
    "HI": {"name": "Hawaii",         "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form N-101A",       "extension_url": "https://tax.hawaii.gov/forms/",                                              "extension_auto_granted": True},
    "ID": {"name": "Idaho",          "has_income_tax": True, "rate_type": "flat",         "flat_rate": 0.0569,  "extension_form": "Form 51",          "extension_url": "https://tax.idaho.gov/taxes/income-tax/individual-income/",                  "extension_auto_granted": True},
    "IA": {"name": "Iowa",           "has_income_tax": True, "rate_type": "flat",         "flat_rate": 0.038,   "extension_form": "IA 1040V",         "extension_url": "https://tax.iowa.gov/forms",                                                 "extension_auto_granted": True,  "notes": "IA moved to flat 3.8% rate in 2025."},
    "KS": {"name": "Kansas",         "has_income_tax": True, "rate_type": "progressive", "extension_form": "K-40V",             "extension_url": "https://www.ksrevenue.gov/forms.html",                                       "extension_auto_granted": False},
    "LA": {"name": "Louisiana",      "has_income_tax": True, "rate_type": "flat",         "flat_rate": 0.03,    "extension_form": "R-2868",           "extension_url": "https://revenue.louisiana.gov/Forms",                                        "extension_auto_granted": False, "notes": "LA moved to flat 3% rate in 2025."},
    "ME": {"name": "Maine",          "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form 1040EXT-ME",   "extension_url": "https://www.maine.gov/revenue/tax-return-forms",                             "extension_auto_granted": False},
    "MD": {"name": "Maryland",       "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form 502E",         "extension_url": "https://www.marylandtaxes.gov/individual/index.php",                         "extension_auto_granted": True,  "notes": "Automatic extension if federal 4868 filed and no balance due."},
    "MN": {"name": "Minnesota",      "has_income_tax": True, "rate_type": "progressive", "extension_form": "M13",               "extension_url": "https://www.revenue.state.mn.gov/individuals",                               "extension_auto_granted": True},
    "MS": {"name": "Mississippi",    "has_income_tax": True, "rate_type": "flat",         "flat_rate": 0.047,   "extension_form": "Form 80-106",      "extension_url": "https://www.dor.ms.gov/individual",                                          "extension_auto_granted": True,  "notes": "MS moved to flat 4.7% rate in 2024 and is phasing to 4.0%."},
    "MO": {"name": "Missouri",       "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form MO-60",        "extension_url": "https://dor.mo.gov/forms/",                                                  "extension_auto_granted": False},
    "MT": {"name": "Montana",        "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form EXT-FID",      "extension_url": "https://mtrevenue.gov/taxes/individual-income-tax/",                         "extension_auto_granted": True},
    "NE": {"name": "Nebraska",       "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form 4868N",        "extension_url": "https://revenue.nebraska.gov/individuals",                                   "extension_auto_granted": False},
    "NJ": {"name": "New Jersey",     "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form NJ-630",       "extension_url": "https://www.nj.gov/treasury/taxation/individuals.shtml",                     "extension_auto_granted": False},
    "NM": {"name": "New Mexico",     "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form RPD-41096",    "extension_url": "https://www.tax.newmexico.gov/forms-publications/",                          "extension_auto_granted": False},
    "NY": {"name": "New York",       "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form IT-370",       "extension_url": "https://www.tax.ny.gov/forms/",                                              "extension_auto_granted": False},
    "ND": {"name": "North Dakota",   "has_income_tax": True, "rate_type": "progressive", "extension_form": "federal 4868",      "extension_url": "https://www.tax.nd.gov/individual",                                          "extension_auto_granted": True,  "notes": "Accepts federal extension automatically."},
    "OH": {"name": "Ohio",           "has_income_tax": True, "rate_type": "progressive", "extension_form": "IT-40P",            "extension_url": "https://tax.ohio.gov/individual/resources/forms",                            "extension_auto_granted": True,  "notes": "Accepts federal extension automatically."},
    "OK": {"name": "Oklahoma",       "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form 504-I",        "extension_url": "https://oklahoma.gov/tax/individuals.html",                                  "extension_auto_granted": False},
    "OR": {"name": "Oregon",         "has_income_tax": True, "rate_type": "progressive", "extension_form": "OR-40-V (EXT)",     "extension_url": "https://www.oregon.gov/DOR/forms/",                                          "extension_auto_granted": True},
    "RI": {"name": "Rhode Island",   "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form RI-4868",      "extension_url": "https://tax.ri.gov/forms",                                                   "extension_auto_granted": False},
    "SC": {"name": "South Carolina", "has_income_tax": True, "rate_type": "progressive", "extension_form": "SC4868",            "extension_url": "https://dor.sc.gov/tax/individual-income",                                   "extension_auto_granted": False},
    "VT": {"name": "Vermont",        "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form IN-151",       "extension_url": "https://tax.vermont.gov/individuals",                                        "extension_auto_granted": False},
    "VA": {"name": "Virginia",       "has_income_tax": True, "rate_type": "progressive", "extension_form": "Form 760IP",        "extension_url": "https://www.tax.virginia.gov/forms",                                         "extension_auto_granted": True,  "notes": "Automatic 6-month extension — Form 760IP only required if paying tax."},
    "WV": {"name": "West Virginia",  "has_income_tax": True, "rate_type": "progressive", "extension_form": "Schedule L",        "extension_url": "https://tax.wv.gov/Individuals/",                                            "extension_auto_granted": False},
    "WI": {"name": "Wisconsin",      "has_income_tax": True, "rate_type": "progressive", "extension_form": "federal 4868",      "extension_url": "https://www.revenue.wi.gov/Pages/Individuals/home.aspx",                     "extension_auto_granted": True,  "notes": "Accepts federal extension automatically."},
}


def get(state_code: str) -> Optional[dict]:
    return STATES.get((state_code or "").upper())


def all_states() -> list[dict]:
    return [dict(code=k, **v) for k, v in STATES.items()]


def estimate_state_tax(state_code: str, federal_taxable_income: float, spouse_w2_income: float = 0.0, estimated_payments_made: float = 0.0) -> dict:
    """Compute a rough state tax liability for any state.

    Flat-rate states: taxable × flat_rate — minus estimated withholding.
    Progressive states: return a stub result with a REVIEW note asking the
    user to compute manually. We still return the extension form info so
    the extension flow works even when the liability is unknown.
    No-income-tax states: return 0 with a friendly note.
    """
    meta = get(state_code)
    if meta is None:
        return {"state": state_code, "status": "unknown", "tax_owed": 0.0, "notes": [f"Unknown state code: {state_code}"]}

    if not meta["has_income_tax"]:
        return {
            "state": state_code,
            "state_name": meta["name"],
            "status": "no_income_tax",
            "tax_owed": 0.0,
            "tax_before_payments": 0.0,
            "estimated_withholding": 0.0,
            "notes": [meta.get("notes") or "No state individual income tax."],
            "extension_form": meta.get("extension_form"),
            "extension_url": meta.get("extension_url"),
            "extension_auto_granted": meta.get("extension_auto_granted", False),
        }

    if meta["rate_type"] == "flat":
        rate = meta["flat_rate"]
        taxable = max(0.0, float(federal_taxable_income or 0))
        before_payments = taxable * rate
        est_withholding = (spouse_w2_income or 0) * rate
        tax_owed = max(0.0, before_payments - est_withholding - (estimated_payments_made or 0))
        return {
            "state": state_code,
            "state_name": meta["name"],
            "status": "ok",
            "rate_type": "flat",
            "flat_rate": rate,
            "taxable_income": round(taxable, 2),
            "tax_before_payments": round(before_payments, 2),
            "estimated_withholding": round(est_withholding, 2),
            "estimated_payments_made": round(estimated_payments_made, 2),
            "tax_owed": round(tax_owed, 2),
            "rate_source": f"{meta['name']} {round(rate * 100, 2)}% flat rate (2025 estimate — verify before filing)",
            "notes": [meta.get("notes") or ""],
            "extension_form": meta.get("extension_form"),
            "extension_url": meta.get("extension_url"),
            "extension_auto_granted": meta.get("extension_auto_granted", False),
        }

    # Progressive — framework in place, no bracket math yet
    return {
        "state": state_code,
        "state_name": meta["name"],
        "status": "progressive_unsupported",
        "rate_type": "progressive",
        "taxable_income": round(max(0.0, float(federal_taxable_income or 0)), 2),
        "tax_before_payments": None,
        "tax_owed": None,
        "notes": [
            f"{meta['name']} uses progressive tax brackets. CPAgent's round-1 state engine does not yet compute "
            f"bracket math for this state — compute manually using the state DOR's tax tables or contribute a "
            f"bracket table to CPAgent's states_registry.py.",
            meta.get("notes") or "",
        ],
        "extension_form": meta.get("extension_form"),
        "extension_url": meta.get("extension_url"),
        "extension_auto_granted": meta.get("extension_auto_granted", False),
    }
