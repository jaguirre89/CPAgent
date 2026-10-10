# Architecture audit, 2026-10-04 (software findings only)

Scope: code-level findings from an internal engineering audit of the dashboard. All taxpayer-specific content has been removed; examples use generic fixtures. Paths are relative to the repository root. Line numbers refer to the audited revision and may have drifted.

Severity: BLOCKER / HIGH / MED / LOW. The audited codebase had no automated tests (S-4).

Tax-computation fixes (S-5, S-6, S-7, S-10 to S-12 and the filing-module items) are tracked here for a later PR; this PR does not change tax math.

## 1. Runtime and data

- **S-1 BLOCKER, not reproducibly installable.** `pypdf`, `anthropic` and `reportlab` are imported (`filing/irs_forms.py:20,187`, `learning/llm_coach.py:145`) but were missing from `requirements.txt`; pins are floor-only; `sqlcipher3-binary` has no arm64 macOS wheel (`sqlcipher3-wheels` installs and opens the DB). The port is hardcoded to 5050 (`app.py:1303`, `scripts/CPAgent.command:33`, `scripts/install_launchd.sh:41`). `app_config.json` `root_path` can point at a path that no longer exists.
  Acceptance: fresh venv, `pip install -r requirements.txt` (with `==` pins), launch with the port taken from `$PORT`; `GET /api/config` returns 200 and the log shows encryption active.
  Status: dependency names fixed in this PR (floors kept); pins and `$PORT` remain.
- **S-2 BLOCKER, silent replacement of the DB key.** `security/keyring.py` `get_or_create_db_key()` generated a fresh key whenever the Keychain lookup failed, which makes an existing SQLCipher file permanently unreadable. Also the `.secrets` file fallback can persist the key after a Keychain exception.
  Acceptance: with a non-empty `data/taxdash.db` and no key, the function raises instead of generating.
  Status: guard added in this PR; removal of the `.secrets` fallback remains.
- **S-3 HIGH, swallowed migrations and silent year fallback.** `db.py:100-108` swallows migration errors; `db.py:110-129` drops and renames `checklist_overrides` with no backup; `app.py:79-85` falls back to the newest `tax_constants/*.json` when the year's file is missing.
  Acceptance: missing constants file yields a BLOCKED finding; migrations run in a transaction after a pre-migration copy.
- **S-4 HIGH, no tests.** Acceptance: pytest covers P&L parse totals, `generate_schedule_c` meals and exclusions, `_consolidated_values`, a test that every value in every `*_MAP` in `filing/form_field_map.py` exists in the template (at page-widget level), and a golden-file regression.

## 2. Tax math (tracked for a later PR)

- **S-5 HIGH, meals at 100 percent.** `parser/schedule_c.py:187-189` adds full meals to line 24b; `:202-215` computes the deductible amount but only logs it; `:232-236` totals the full amount; the comment at `:208` is false; `filing/irs_forms.py:61-64` copies it to the form.
  Acceptance: line 24b equals `round(meals * meals_deductibility_pct, 2)` and line 28 reflects it.
- **S-6 HIGH, federal tax payments deducted.** `parser/csv_parser.py:101-103` maps tax-payment accounts to "Taxes and licenses"; `schedule_c.py:191-197` only logs.
  Acceptance: those accounts map to `None` with a REQUIRED finding and route to Form 1040 payments.
- **S-7 HIGH, stale and duplicated constants.** `tax_constants/2025.json:4-7` standard deduction values and `:25-26` QBI thresholds are out of date for 2025. Hardcoded duplicates bypass the JSON: `filing/irs_forms.py:76-77`, `:85`, `:90`, `:358`; `state_tax.py:24`; `parser/home_office.py:23-24`. Federal tax is taxable income times a flat rate (`parser/__init__.py:386`); QBI has no taxable-income cap (`parser/__init__.py:368`, `irs_forms.py:86`); SE tax ignores the Social Security wage base in three places (`parser/__init__.py:339-341`, `schedule_c.py:419-430`, `irs_forms.py:76-77`).
  Acceptance: one constants source with a bracket table; QBI = min(20% QBI, 20% x (taxable income - net capital gain)); SE respects `social_security_wage_base`.
- **S-8 HIGH, conduit logic never applied; category leak into income.** `parser/conduit.py:341` `reconcile_conduits()` and `match_stripe_transactions()` are not called from the pipeline. A seeded memo rule in `gl_reconciler.py:49` emits a category that is not a Schedule C category, `parser/__init__.py:162-170` appends it to the expense list, and the seed rule at `csv_parser.py:49` resolves it to gross receipts. `conduit.py:311` checks `type == "1099_nec"` while `pdf_parser.py:222` returns `"1099"`.
  Acceptance: conduit treatment changes line 1 and line 11 deterministically in a fixture test.
- **S-9 HIGH, home office has two sources.** The overview reads DB periods (`parser/__init__.py:323-329`); the form generator reads `preferences.home_office_total` (`filing/irs_forms.py:72`). The Form 8829 template is never populated. `parser/home_office.py:91-138` has no square-footage validation.
  Acceptance: one source; Form 8829 populated for the regular method; business percentage derived from office/total area.
- **S-10 MED, state withholding fabricated.** `state_tax.py:41` computes withholding as wages times a rate instead of reading W-2 Box 17; `pdf_parser.py` never extracts Box 17; state rate hardcoded at `irs_forms.py:358`.
  Acceptance: Box 17 parsed and used; rate from constants.
- **S-11 MED, vehicle method.** Constants say `vehicle_method: "actual"` while a static dashboard path applied standard mileage and could double count. No business-use percentage on actual costs; no depreciation or Form 4562 continuity.
  Acceptance: vehicle module takes (method, business %, prior-year 4562 data) and refuses a method change that is not allowed.
- **S-12 MED, other parser issues.** Cashback mapped to gross receipts (`csv_parser.py:52`); intercompany cross-check is dead code (`schedule_c.py:394-413`); with two P&L CSVs the last silently wins (`csv_parser.py:553-567`); checklist `total_found` over-counts for `count:"any"` items (`parser/checklist.py:215-217, 308-311`), producing a percentage above 100 that `readiness.py:21` hides; 14 broad `except Exception` sites turn a dead DB into silent zeros; the readiness gate (`readiness.py:88`) is enforced only through a watermark that is skipped when `reportlab` is missing (`irs_forms.py:186-191`); Schedule C lines 2, 4 and 6 are never populated (`irs_forms.py:104-117`).

## 3. Filing module

- **S-13 BLOCKER, Schedule C field map is wrong.** `filing/form_field_map.py:39-66`: 25 of 42 mapped names do not exist in the bundled 2025 `f1040sc.pdf` and are skipped silently (`irs_forms.py:170-174` only warns). Lines 28 to 31 are shifted by one row, so net profit lands in the square-footage box. On the 2025 form, line 27a is the energy-efficient commercial buildings deduction (Form 7205) and 27b is "Other expenses"; the app labels other expenses 27a.
  Acceptance: every value in every `*_MAP` is a key of the template's fields; a generated PDF's line 31 widget holds the expected net; checks run on page widgets, not only `get_fields()`.
- **S-14 HIGH, Schedule SE map wrong from line 6.** `form_field_map.py:69-80`.
- **S-15 HIGH, Form 1040 stops at line 15.** `irs_forms.py:128-146`: no lines 16, 23, 24, 25d, 26, 33, 34, 37; Schedule 1 is appended blank (`:221`) while 1040 lines 8 and 10 carry its amounts; owner and spouse names are absent from config so headers are blank; Schedules B, D, 8949, 8995, 2210, 4562 and 8829 are not produced.
- **S-16 MED, Colorado DR 0104 is header-only** (`irs_forms.py:356-393`): uses a flat-rate federal estimate, ignores additions and subtractions, withholding per S-10.
- **S-17 MED, watermark and gate.** Unmet readiness can still yield an un-watermarked PDF when `reportlab` is absent (`irs_forms.py:186-191`).

## 4. Security, LLM, ops

- **S-18 MED, PII can leave the scrubber.** `app.py:772-776` sends an unscrubbed classification prompt (the scrubbed copy is used only for cost logging); `learning/llm_coach.py:192-195` interpolates entity names raw; the address regex in `security/scrubber.py:32-36` misses suffixes such as "Heights" and "Hts".
  Acceptance: `audit_leaks()` reports an ADDRESS for such a string; every outbound prompt passes through `scrub()` in a test.
- **S-19 MED, ops hygiene.** No log rotation; the launchd plist uses `KeepAlive=true` with no `ThrottleInterval`; `/api/shutdown` (`app.py:563`) is unauthenticated (localhost only); the file watcher (`app.py:642-715`) invalidates all years on any change under the root.
- **S-20 LOW, heuristic regexes** in `gl_reconciler.py:58-60` (for example `\bunited\b` mapped to Travel, `\bups\b` collisions).

## 5. Findings from the independent review (SW-01 to SW-10)

Reproductions used synthetic fixtures against the actual schema and code.

- **SW-01 Critical, PDF generation lacks structural and semantic validation.** Only 17 of 42 Schedule C names exist in the template; Schedule SE matches 9 of 10. All 24 mapped 1040 names exist but several point at the wrong box (name field mapped to a fiscal-year field, filing-status checkbox mapped to the combat-zone box, wages mapped to line 1h, Schedule 1 income mapped to line 4a, standard deduction mapped to line 5b, Schedule C tentative profit mapped to line 28 total expenses). Category names such as "Legal and professional services" and "Deductible meals (50% limit)" are not keys in `SCHEDULE_C_CATEGORY_TO_FIELD`, so amounts can be omitted from detail while included in totals. The merge copies pages into a new writer and loses the canonical AcroForm tree; population errors are logged and processing continues; a missing template does not block output; the watermark silently disappears without `reportlab` while the result reports it as applied. Code: `filing/form_field_map.py`; `filing/irs_forms.py:161,208,326`.
  Acceptance: year-specific semantic field maps; every required value present in the reopened logical fields and matching widgets; cross-form totals agree; missing template, bad mapping or failed watermark blocks release.
- **SW-02 High, saved corrections are not dependable.** Saving the same global memo rule twice creates two rows because `entity_scope` is NULL in the UNIQUE key; the older rule wins after a later correction; a memo rule scoped to entity A also matches entity B. Code: `data/schema.sql:8`; `learning/rules_store.py:41,53,87`.
  Acceptance: updating a global rule replaces its effective decision; no duplicate active key; entity-scoped rules never leak across entities on any path; revisions stay auditable.
- **SW-03 Critical, missing rules can turn income into zero.** With the rules store unavailable, a P&L with a $1,000 income line produces $0 income and only a REVIEW finding. Code: `parser/schedule_c.py:265` (falls through to heuristics), income mapping at `:52`.
  Acceptance: inaccessible classification state, malformed dollar values, omitted material income or failed required parsers yield an explicit invalid result that cannot pass readiness.
- **SW-04 Critical, readiness can approve an empty schedule and suppress a returning blocker.** `compute_readiness()` returns 90 percent, green, `can_generate_filing=True` for a complete checklist, no findings and no Schedule C. An automatically resolved BLOCKED finding that later recurs with the same fingerprint stays suppressed (active counts 1, 0, 0) because auto-resolution is treated as a manual dismissal. Code: `readiness.py:16,88`; `findings/engine.py:111`; `app.py:817`; bank findings appended after readiness at `app.py:196`.
  Acceptance: empty or invalid schedules cannot authorize filing; recurring issues reactivate; auto-resolution and human waiver are distinct; all material checks run before the readiness decision.
- **SW-05 High, cache is not tied to all inputs.** `_source_signature()` truncates mtime to whole seconds and hashes metadata, so two same-length files edited within one second collide. The key excludes code version, constants contents and rule/override revision; in-memory hits skip the signature check; the watcher misses move events and PNG evidence. Code: `cache.py:22,51`; `app.py:211,257`.
  Acceptance: changed content, year, constants, rules, overrides or code invalidate derived data; atomic replacement is detected; each export records its inputs.
- **SW-06 High, DB recovery, storage and migrations.** The key generator creates a replacement key on any retrieval failure (`security/keyring.py:77`); `.secrets` can receive credentials after a Keychain exception; `db.py:28` permits plaintext SQLite when the driver is missing; migrations catch broad exceptions, overwrite schema version with 1 and rebuild a table after any probe failure (`db.py:85`).
  Acceptance: an existing DB never triggers replacement-key creation; encryption failures are explicit; backup plus Keychain recovery restores state in an isolated environment; migrations are versioned and transactional. Do not test against the only production database.
- **SW-07 High, home-office inputs admit invalid deductions.** A regular-method fixture with 300 office sq ft, zero total sq ft and $1,000 monthly rent returns a $3,597,000 deduction (zero becomes 1). A full year computes as 11.99 months (days / 30.44), and a $1,500 simplified maximum becomes $1,498.75. Periods are summed without overlap checks or a profit limit; editing a period's start date inserts a new row. Code: `parser/home_office.py:36,67,91,141`; `app.py:1047`.
  Acceptance: positive valid areas, office no larger than total, nonoverlapping dates, method-specific proration and limits, edits update in place, one authoritative result feeds overview, Schedule C and PDF.
- **SW-08 High, import success is not evidence of correctness.** `scan_1099s()` infers a monetary amount from a filename and substitutes it for the real document; `match_stripe_transactions()` ignores dates; the last P&L parsed overwrites earlier ones; a saved custom CSV mapping leads to `pass`; the malformed value `'$1O0.00'` becomes 0.0; bank summaries are keyed by month rather than account plus month; applying `_apply_gl_reclassifications()` twice adds the same $100 twice. Code: `parser/csv_parser.py:161,539`; `parser/conduit.py:173,283,341`; `parser/bank_parser.py:194`; `parser/__init__.py:91`.
  Acceptance: reject wrong-year documents and inferred monetary values; require explicit source selection; validate control totals; enforce account/entity/date matching; make adjustments idempotent.
- **SW-09 High, lifecycle is not reproducible.** No lockfile, tests or CI; requirements omit directly imported packages; `load_tax_constants(2026)` returns a dict declaring year 2025; the state exporter always selects the 2025 Colorado template; launch scripts hardcode port 5050. Code: `requirements.txt`; `app.py:74,1128,1301`; `filing/irs_forms.py:326`; `scripts/com.cpagent.app.plist`.
  Acceptance: reproducible clean install with explicit versions; validated root path; `portman` launch; unsupported years fail clearly until constants and templates exist.
- **SW-10 High, privacy guarantee exceeds what the scrubber proves.** A memo with an unconfigured name, an email address and an unhyphenated nine-digit SSN-like value survives `scrub()` and `audit_leaks()` reports nothing; name masking depends on config; detection and its self-check share the same pattern assumptions. Code: `security/scrubber.py:63,80,107`; `learning/llm_coach.py:165`.
  Acceptance: adversarial tests with real input formats; minimized outbound fields; verification of the whole request payload, not only a regex self-check.

## 6. Readiness gate

A pipeline that reports 100 percent readiness with zero open findings is not evidence of a correct schedule: the gate is a percentage, not a correctness check. Known computation errors (meals, tax payments, constants, field maps) coexisted with a green gate. Readiness must require a computed schedule and treat correctness-class findings as blocking (see SW-04).

## 7. Mapping of the independent review to the main findings

| Review item | Status | Main finding |
|---|---|---|
| SW-01 PDF mapping | Agreed; adds the 27a/27b relabel and 1040 semantic mismatches | S-13, S-14, S-15 |
| SW-02 rules duplicates / scope leak | Accepted; one duplicate already seen in a recovered DB | S-21 |
| SW-03 missing rules, income becomes zero | Accepted | S-22 |
| SW-04 readiness approves empty schedule; blocker never returns | Accepted | S-23 (with S-12, S-17) |
| SW-05 cache signature | Accepted | S-24 |
| SW-06 key and migrations | Key guard added in this PR; `.secrets` fallback still to fix | S-2, S-3 |
| SW-07 home-office validation | Accepted | S-25 (with S-9) |
| SW-08 import validation | Accepted | S-8, S-12, S-26 |
| SW-09 install and year lifecycle | Agreed | S-1, S-3 |
| SW-10 scrubber gaps | Accepted; extends S-18 | S-18 |

## 8. Proposed order of work after this PR

10. S-1 environment and requirements (`sqlcipher3-wheels`, `pypdf`, `reportlab`, `anthropic`, pins, `portman`); S-2 remove the `.secrets` fallback.
11. S-4 test harness first, including: a field-map-exists test at the page-widget level; a golden-file test that the app reproduces a known-good return line for line; fixtures for SW-02 (duplicate rule), SW-03 (income to zero), SW-04 (empty-schedule readiness, blocker reappearance), SW-05 (same-second cache), SW-07 (zero-sqft home office), SW-08 (filename amount, `$1O0.00`, double GL apply) and SW-10 (scrubber).
12. S-13, S-14, S-15 PDF maps with the 27b relabel and 1040 semantics; preserve the AcroForm tree on merge; never write full SSNs into generated drafts stored in the repo.
13. S-5 meals, S-6 federal tax payments, S-7 constants/brackets/QBI/SE, S-22 (income-to-zero must block), S-23 (readiness requires computed schedules; correctness-class findings block).
14. S-21 rules-store uniqueness and scope; S-24 cache key (content hash + constants + rules revision + code version); S-25 home-office validation and single source; S-8 and S-26 conduit application and import validation; S-9 home-office single source; S-10 and S-11 state withholding and vehicle; S-18 scrubber; S-3, S-12, S-16, S-17, S-19, S-20.
