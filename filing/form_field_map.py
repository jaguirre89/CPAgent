"""IRS fillable PDF field mapping.

IRS fillable PDFs use opaque field names like `topmostSubform[0].Page1[0].f1_10[0]`
with no tooltips — the `/TU` attribute is stripped in the published PDFs. The
mapping below was derived by inspecting the form field order in pypdf and cross-
referencing against the published line order on irs.gov.

**This mapping is a first-pass heuristic.** Verify each field before e-filing —
the line-to-field correspondence can shift when the IRS releases a new form
revision. The `POST /api/filing/generate` route stamps a DRAFT watermark until
the readiness gate lifts, which minimizes the risk of a bad auto-populated draft
leaving the machine.
"""

from __future__ import annotations

# Schedule C (f1040sc.pdf) — 124 fields, single-page primary layout
SCHEDULE_C = {
    # Header
    "proprietor_name":   "topmostSubform[0].Page1[0].f1_1[0]",
    "ssn":               "topmostSubform[0].Page1[0].f1_2[0]",
    "principal_business":"topmostSubform[0].Page1[0].f1_3[0]",
    "business_code":     "topmostSubform[0].Page1[0].BComb[0].f1_4[0]",
    "business_name":     "topmostSubform[0].Page1[0].f1_5[0]",
    "ein":               "topmostSubform[0].Page1[0].DComb[0].f1_6[0]",
    "address":           "topmostSubform[0].Page1[0].f1_7[0]",
    "city_state_zip":    "topmostSubform[0].Page1[0].f1_8[0]",

    # Line 1 Gross receipts
    "line_1_gross_receipts":    "topmostSubform[0].Page1[0].f1_10[0]",
    "line_2_returns":           "topmostSubform[0].Page1[0].f1_11[0]",
    "line_3_net":               "topmostSubform[0].Page1[0].f1_12[0]",
    "line_4_cogs":              "topmostSubform[0].Page1[0].f1_13[0]",
    "line_5_gross_profit":      "topmostSubform[0].Page1[0].f1_14[0]",
    "line_6_other_income":      "topmostSubform[0].Page1[0].f1_15[0]",
    "line_7_gross_income":      "topmostSubform[0].Page1[0].f1_16[0]",

    # Expenses (lines 8-27a)
    "line_8_advertising":       "topmostSubform[0].Page1[0].f1_17[0]",
    "line_9_car_truck":         "topmostSubform[0].Page1[0].f1_18[0]",
    "line_10_commissions":      "topmostSubform[0].Page1[0].f1_19[0]",
    "line_11_contract_labor":   "topmostSubform[0].Page1[0].f1_20[0]",
    "line_12_depletion":        "topmostSubform[0].Page1[0].f1_21[0]",
    "line_13_depreciation":     "topmostSubform[0].Page1[0].f1_22[0]",
    "line_14_benefits":         "topmostSubform[0].Page1[0].f1_23[0]",
    "line_15_insurance":        "topmostSubform[0].Page1[0].f1_24[0]",
    "line_16a_interest_mortgage":"topmostSubform[0].Page1[0].f1_25[0]",
    "line_16b_interest_other":  "topmostSubform[0].Page1[0].f1_26[0]",
    "line_17_legal_pro":        "topmostSubform[0].Page1[0].f1_27[0]",
    "line_18_office":           "topmostSubform[0].Page1[0].f1_28[0]",
    "line_19_pension":          "topmostSubform[0].Page1[0].f1_29[0]",
    "line_20a_rent_vehicle":    "topmostSubform[0].Page1[0].f1_30[0]",
    "line_20b_rent_other":      "topmostSubform[0].Page1[0].f1_31[0]",
    "line_21_repairs":          "topmostSubform[0].Page1[0].f1_32[0]",
    "line_22_supplies":         "topmostSubform[0].Page1[0].f1_33[0]",
    "line_23_taxes":            "topmostSubform[0].Page1[0].f1_34[0]",
    "line_24a_travel":          "topmostSubform[0].Page1[0].f1_35[0]",
    "line_24b_meals":           "topmostSubform[0].Page1[0].f1_36[0]",
    "line_25_utilities":        "topmostSubform[0].Page1[0].f1_37[0]",
    "line_26_wages":            "topmostSubform[0].Page1[0].f1_38[0]",
    "line_27a_other":           "topmostSubform[0].Page1[0].f1_39[0]",
    "line_28_total_expenses":   "topmostSubform[0].Page1[0].f1_40[0]",
    "line_29_tentative_profit": "topmostSubform[0].Page1[0].f1_41[0]",
    "line_30_home_office":      "topmostSubform[0].Page1[0].f1_42[0]",
    "line_31_net_profit":       "topmostSubform[0].Page1[0].f1_43[0]",
}

# Schedule SE (f1040sse.pdf) — computes SE tax from net profit
SCHEDULE_SE = {
    "name":                     "topmostSubform[0].Page1[0].f1_1[0]",
    "ssn":                      "topmostSubform[0].Page1[0].f1_2[0]",
    "line_2_net_from_sch_c":    "topmostSubform[0].Page1[0].f1_5[0]",
    "line_3_total":             "topmostSubform[0].Page1[0].f1_6[0]",
    "line_4a_net_earnings":     "topmostSubform[0].Page1[0].f1_7[0]",
    "line_6_total_se":          "topmostSubform[0].Page1[0].f1_9[0]",
    "line_10_ss_portion":       "topmostSubform[0].Page1[0].f1_13[0]",
    "line_11_medicare":         "topmostSubform[0].Page1[0].f1_14[0]",
    "line_12_total_se_tax":     "topmostSubform[0].Page1[0].f1_15[0]",
    "line_13_deduction":        "topmostSubform[0].Page1[0].f1_16[0]",
}

# Form 1040 (f1040.pdf) — taxpayer header only; line flows stay TODO
FORM_1040 = {
    "first_name_mi":            "topmostSubform[0].Page1[0].f1_01[0]",
    "last_name":                "topmostSubform[0].Page1[0].f1_02[0]",
    "ssn":                      "topmostSubform[0].Page1[0].f1_03[0]",
    "spouse_first_name_mi":     "topmostSubform[0].Page1[0].f1_04[0]",
    "spouse_last_name":         "topmostSubform[0].Page1[0].f1_05[0]",
    "spouse_ssn":               "topmostSubform[0].Page1[0].f1_06[0]",
}

# Form 4868 — Application for Automatic Extension of Time To File
# Part I: Identification / Part II: Individual income tax
FORM_4868 = {
    # Part I — Name, address, SSN
    "name":        "topmostSubform[0].Page1[0].PartI_ReadOrder[0].f1_4[0]",
    "address":     "topmostSubform[0].Page1[0].PartI_ReadOrder[0].f1_5[0]",
    "city_state_zip": "topmostSubform[0].Page1[0].PartI_ReadOrder[0].f1_6[0]",
    "ssn":         "topmostSubform[0].Page1[0].PartI_ReadOrder[0].f1_7[0]",
    "spouse_ssn":  "topmostSubform[0].Page1[0].PartI_ReadOrder[0].f1_8[0]",

    # Part II — Individual Income Tax
    "line_4_estimated_total_tax":    "topmostSubform[0].Page1[0].f1_11[0]",
    "line_5_total_payments":          "topmostSubform[0].Page1[0].f1_12[0]",
    "line_6_balance_due":             "topmostSubform[0].Page1[0].f1_13[0]",
    "line_7_amount_paying":           "topmostSubform[0].Page1[0].f1_14[0]",
}


# Colorado DR 0104 — 2025 Individual Income Tax Return
# CO uses human-readable field names, unlike the IRS opaque f1_N naming.
CO_DR0104 = {
    "first_name":               "First Name",
    "middle_initial":           "Middle Initial",
    "last_name":                "Last Name",
    "ssn":                      "Social Security Number",
    "spouse_first_name":        "Spouse First Name",
    "spouse_middle_initial":    "Spouse Middle Initial",
    "spouse_last_name":         "Spouse Last Name",
    "spouse_ssn":               "Spouse Social Security Number",
    "address":                  "Physical Street Address 2",
    "city":                     "City 4",
    "state":                    "State 5",
    "zip":                      "ZIP Code 4",
    "phone":                    "Phone Number 3",
    # Line fields come with numeric tooltips in the PDF. These are
    # discovered positionally (see the comment in irs_forms.py::_populate).
    # We set only the header + flat-rate computation into Line 1 / Line 20 / etc.
}

# Colorado DR 0158-I — Extension of Time for Filing Individual Income Tax
# Fields are generic "Text143"…"Text162" — unnamed but sequential. Based on
# the published form layout, the top block is name/address/SSN and the bottom
# block is the estimated tax / withholding / balance due computation.
CO_DR0158I = {
    "name":                 "Text143",
    "ssn":                  "Text144",
    "address":              "Text146",
    "city":                 "Text147",
    "state":                "Text148",
    "zip":                  "Text149",
    "spouse_name":          "Text150",
    "spouse_ssn":           "Text151",
    "estimated_total_tax":  "Text155",
    "total_prior_payments": "Text156",
    "balance_due":          "Text157",
    "amount_paying":        "Text158",
}


SCHEDULE_C_CATEGORY_TO_FIELD = {
    "Advertising":                  "line_8_advertising",
    "Car and truck expenses":       "line_9_car_truck",
    "Commissions and fees":         "line_10_commissions",
    "Contract labor":               "line_11_contract_labor",
    "Depreciation":                 "line_13_depreciation",
    "Employee benefit programs":    "line_14_benefits",
    "Insurance (other than health)":"line_15_insurance",
    "Interest (mortgage)":          "line_16a_interest_mortgage",
    "Interest (other)":             "line_16b_interest_other",
    "Legal and professional":       "line_17_legal_pro",
    "Office expense":               "line_18_office",
    "Pension and profit-sharing":   "line_19_pension",
    "Rent - vehicles/equipment":    "line_20a_rent_vehicle",
    "Rent - other":                 "line_20b_rent_other",
    "Repairs and maintenance":      "line_21_repairs",
    "Supplies":                     "line_22_supplies",
    "Taxes and licenses":           "line_23_taxes",
    "Travel":                       "line_24a_travel",
    "Meals (50% limit)":            "line_24b_meals",
    "Utilities":                    "line_25_utilities",
    "Wages":                        "line_26_wages",
    "Other expenses":               "line_27a_other",
}
