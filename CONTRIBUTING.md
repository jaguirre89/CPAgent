# Contributing to CPAgent

Thanks for considering a contribution. CPAgent is a round-1 scaffold — the core parse / findings / filing pipeline is in place, but plenty of high-impact additions are one-file changes.

## Ground rules

1. **Never commit personal data.** `app_config.json`, `data/taxdash.db*`, `logs/`, and `filing/templates/*.pdf` are gitignored for a reason. Double-check `git status` before `git add`.
2. **Localhost-only.** Don't change the Flask bind address away from `127.0.0.1`. The `Host` header allow-list is part of the SOC2 posture.
3. **Never send PII outbound.** Any code that touches the Anthropic API — or any other outbound service — must route through `security/scrubber.py::scrub()`. Add unit tests against synthetic PII before merging.
4. **Keep the hot path deterministic.** LLM-suggested categorization rules are fine, but they must become permanent DB rules before being applied. The parse → categorize → Schedule C flow should never hit the API at runtime.

## Starting points

### Add a state handler

1. Open `states_registry.py` and find your state in the `STATES` dict.
2. If it has flat-rate tax and is already marked `"rate_type": "flat"` with a `flat_rate`, you're done — the estimator works automatically.
3. If it has progressive brackets, replace `"rate_type": "progressive"` with `"brackets"` and add a `"brackets"` list of `(threshold, rate)` tuples. Then add a bracket-aware branch to `estimate_state_tax()`.
4. If the state has unusual deductions (e.g., Colorado TABOR), add a custom handler in `state_tax.py::HANDLERS` — it'll be tried before the registry.

### Add a state extension PDF generator

1. Download the extension form with `curl` or in `scripts/download_irs_forms.sh` — many state DORs host fillable PDFs under their "forms" landing pages. Follow redirects.
2. Inspect field names with `PdfReader(path).get_fields()`. Some states use human-readable names (CO), most use opaque `Text143` / `f1_N` naming.
3. Add a field map to `filing/form_field_map.py`.
4. Add a generator function to `filing/irs_forms.py` following the `generate_extension_4868` pattern.
5. Wire it into `generate_state_extension` for your state code.

### Add a P&L CSV format

1. Open `parser/csv_parser.py::detect_csv_format()` — detection is purely header-based, no filename checks.
2. Add a new branch that returns `{"format": "your_format", "headers": ..., "entity_name": ...}`.
3. Write a `parse_your_format()` function returning the same dict shape as `parse_wave_pl()`.
4. Wire it into `parse_all_csvs()`.

### Add a bank / credit-card PDF parser

1. Open `parser/pdf_parser.py` and add a detection branch in `parse_pdf()` (usually by filename substring or first-page text heuristic).
2. Follow the Wells Fargo / Citi patterns for the actual extraction.

## Testing

There's no formal test suite yet — round 1 was verified end-to-end against real data. A `tests/` directory with:

- Scrubber fuzz tests (synthetic PII)
- W-2 parser golden tests (synthetic IRS W-2 layout)
- Findings engine delta tests (fingerprint stability)
- State registry estimator tests

…is a high-value contribution. Use `pytest` and put fixtures under `tests/fixtures/`.

## Pull request checklist

- [ ] No personal data in the diff (grep for your name, entity names, account numbers, SSN patterns)
- [ ] No changes to the Flask bind address or Host allow-list
- [ ] Any outbound API call routes through the scrubber
- [ ] `git status` shows only files you intended
- [ ] README updated if user-visible behavior changed

## Reporting security issues

Please report security vulnerabilities privately (not via public issue). Do not include tax data, SSNs, or account numbers in any report.
