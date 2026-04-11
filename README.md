# CPAgent

**A local-first, SOC2-aligned personal tax filing dashboard for self-employed Schedule C filers.**

CPAgent ingests raw financial records (bank statements, credit card statements, P&L exports, W-2s, 1099s), reconciles them through a learning categorization engine, surfaces actionable findings with delta tracking, computes federal + state tax estimates, and generates populated IRS and state filing / extension PDFs — all from your own laptop, with encryption at rest and a PII scrubber on any outbound API call.

> ## ⚠️ DISCLAIMER — USE AT YOUR OWN RISK
> CPAgent is provided "as is", without warranty of any kind, express or implied. The authors and contributors **make no promises** that this software produces correct or complete results for your particular tax situation, and shall not be liable for any claim, damages, or other liability arising from the use of this software. **This tool is not tax advice** and is not a substitute for a licensed tax professional. Always verify all figures, forms, and filings with a qualified CPA or tax attorney before submitting anything to the IRS, any state revenue department, or other taxing authority.

---

## Features

### Parsing & reconciliation
- **Wave / QBO P&L auto-detection** (CSV format sniffing by header row, not filename)
- **Wells Fargo / Citi / generic bank & credit-card PDF parsing** via `pdfplumber`
- **ADP / generic W-2 extraction** (Box 1 wages + federal withholding via positional regex)
- **GL reconciliation** — drills into the raw ledger, groups uncategorized transactions by memo pattern, and proposes categorizations with per-vendor heuristics
- **Unknown-account inference** — detects payments to card issuers (Apple Card, Amazon, Chase, Amex, Capital One, Discover, Barclays, etc.) not configured in your account list
- **Auto-detect new tax year folders** — drop a `YYYY Tax Year` folder and the watcher picks it up live

### Categorization & learning
- **SQLite-backed rules store** with seeded + user-taught + LLM-suggested rule sources
- **Teach loop** — one-click categorization on any finding persists a reusable rule
- **"Resolve All with AI" batch mode** — sends all uncategorized transaction groups to Claude in one call, returns a review table with per-item accept/reject and category override, then batch-applies approved rules
- **Anthropic API integration** for rule suggestions (optional, budget-capped, PII-scrubbed)
- **GL reclassification engine** — auto-classified GL groups reduce uncategorized P&L buckets in real time, routing income and expense reductions to the correct originating account

### Findings engine
- **Action-oriented severity taxonomy**: `BLOCKED` / `REQUIRED` / `REVIEW` / `RESOLVED` / `LOGGED`
- **Delta tracking** — persistent findings history with transition detection ("was BLOCKED, now RESOLVED")
- **Dismiss persistence** — user-dismissed findings stay resolved across re-parses (won't re-activate)
- **LOGGED auto-hide** — auto-classified findings are tracked but hidden from the active findings list
- **Real-time SSE updates** — findings refresh across tabs when files change, without page reload

### Filing Readiness meter
- Composite score (40% docs + 30% zero-blocked + 20% required-cleared + 10% Schedule C lines computed)
- Document completion capped at 100% (handles found > expected gracefully)
- Gates the IRS draft watermark so you can't generate a filing-ready PDF until everything is clean

### Tax calculations
- **Federal**: Self-Employment Tax (15.3% of 92.35% of net SE earnings), Federal Income Tax (marginal rate × taxable income after SE deduction + QBI + standard deduction), with spouse W-2 income auto-parsed
- **State**: flat-rate computation for 12 states, no-tax handling for 9 states, framework stubs for all progressive states with extension form metadata

### Filing output
- **IRS Form 1040 + Schedule C + Schedule SE + Schedule 1** populated PDFs
- **IRS Form 4868** federal extension (downloads + populates line 4/5/6/7 automatically)
- **Colorado DR 0104** + **DR 0158-I** extension (full implementation)
- **All 50 states + DC extension metadata** — form name, DOR URL, and computed balance-due figure returned as a referral response for states without a full PDF generator

### Security (SOC2-aligned)
- **Encryption at rest** via SQLCipher (AES-256) with master key in macOS Keychain, never on disk
- **Localhost-only bind** + Host header allow-list (CC6.1)
- **Audit log** of every mutating route + every outbound LLM call, with SHA-256 payload hashing (CC7.2)
- **PII scrubber** — every byte sent to the Anthropic API passes through a regex scrubber that redacts SSN, EIN, bank/card/routing numbers (Luhn-validated), addresses, and tokenizes entity + person names. A redaction map is retained locally so API responses can be un-tokenized for display without any PII round-tripping
- **Data classification registry** (public / internal / confidential / pii)
- **Retention purge** on startup (2-year default for findings, audit log, LLM suggestions)
- **Anthropic cost ceiling** — configurable monthly USD budget enforced before any API call

### Persistence
- SQLite-backed parsed cache survives restarts — cold startup loads 12k-row GL + 60+ PDFs in <100ms after the first parse
- Invalidated by a signature over source file mtimes, so editing any file triggers a re-parse

---

## Prerequisites

- **macOS** (LaunchAgent installer is macOS-specific; the app itself runs anywhere Python does)
- **Python 3.9+**
- **Homebrew** for `sqlcipher` (optional but recommended for encryption at rest)

## Installation

```bash
git clone https://github.com/YOUR_USER/cpagent.git
cd cpagent

# Install SQLCipher (optional but recommended — falls back to plaintext sqlite3 if absent)
brew install sqlcipher

# Python dependencies (--user or in a venv)
python3 -m pip install --user -r requirements.txt

# If sqlcipher3-binary doesn't install via pip, build pysqlcipher3 against brew sqlcipher:
SQLCIPHER_PREFIX=/opt/homebrew/opt/sqlcipher \
CPPFLAGS="-I${SQLCIPHER_PREFIX}/include -I${SQLCIPHER_PREFIX}/include/sqlcipher" \
LDFLAGS="-L${SQLCIPHER_PREFIX}/lib" \
python3 -m pip install --user --no-binary=:all: pysqlcipher3
```

## First Launch

```bash
python3 app.py
```

Open **http://localhost:5050**. On first launch you'll see a setup screen:

1. **Root folder path** — the parent directory containing your `YYYY Tax Year` folders
2. **Filing status** — MFJ / Single / HOH / MFS
3. **Primary entity** — name, type (LLC/S-Corp/C-Corp), state, accounting software
4. Save — the app creates the encrypted SQLite database at `data/taxdash.db`, seeds the categorization rules from `parser/csv_parser.ACCOUNT_TO_SCHEDULE_C`, and starts the file watcher

See `app_config.example.json` for the config schema.

### Download the IRS templates (required for filing PDFs)

```bash
scripts/download_irs_forms.sh
```

This fetches Form 1040, Schedule C, Schedule SE, Schedule 1, Form 4868, Form 8829, Form 4562, and Colorado DR 0104 + DR 0158-I into `filing/templates/` with SHA-256 verification.

### Install the desktop shortcut (optional, macOS)

```bash
scripts/install_shortcut.sh
```

This generates a CPAgent icon (via Pillow + `iconutil`), attaches it to `CPAgent.command` via Rez, and installs a double-clickable copy to `~/Applications/CPAgent.command`. Drag it to the Dock for one-click start.

### Run as a background service (optional, macOS)

```bash
scripts/install_launchd.sh
```

Installs a LaunchAgent at `~/Library/LaunchAgents/com.cpagent.app.plist` that starts the server at login, keeps it alive with respawn-on-crash, and logs to `logs/`.

---

## Folder structure

CPAgent auto-discovers source folders by name pattern — no hardcoded numbering required:

```
Tax Agent/                        (← root, configured in Settings)
├── 2025 Tax Year/
│   ├── 01_Personal/              ← SSN, prior-year AGI
│   ├── 02_Spouse_W2/             ← spouse W-2 PDFs (auto-parsed)
│   ├── 03_Acme_LLC/              ← Wave / QBO P&L CSV, bank PDFs, 1099s
│   ├── 04_Second_LLC/            ← same
│   ├── 05_Vehicle/               ← mileage logs
│   ├── 06_Home_Office/
│   ├── 07_Investments/           ← 1099-B / DIV / INT
│   ├── 08_Crypto/
│   ├── 09_Contractors_1099s/
│   ├── 10_Tax_Payments/
│   ├── 11_Prior_Returns/
│   └── 12_Final_Worksheets/      ← dashboard_notes.json, generated PDFs land here
└── cpagent/                      ← this repo
```

---

## Dashboard tabs

| Tab | What's on it |
|---|---|
| **Overview** | Estimated Total Tax Owed card (federal + state), KPIs, Strategy Recommendations strip, revenue / expense charts |
| **Schedule C** | Per-entity workpapers, all 31 IRS lines with computed fields |
| **Findings** | CoWork-style delta summary, severity chips, transition badges, Teach button, Dismiss button, "Resolve All with AI" batch classifier with review modal |
| **Bank Rec** | Monthly reconciliation per configured account, gap detection |
| **Checklist** | Document completeness tracker with progress bar |
| **Strategy** | Tax optimization evaluators (S-Corp election, Solo 401k / SEP-IRA, QBI deduction) |
| **Filing** | Generate IRS draft PDF, Form 4868 federal extension, state filing + extension |
| **Settings** | Entity config, tax constants, AI Settings (API key, model switcher, cost ceiling) |

---

## Security posture

| Control | Implementation |
|---|---|
| CC6.1 Logical access | Flask binds `127.0.0.1` only; `Host` header allow-list rejects LAN requests |
| CC6.6 Encryption at rest | SQLCipher (AES-256), key in macOS Keychain |
| CC6.7 Encryption in transit | TLS 1.3 to Anthropic API via SDK default; localhost HTTP only |
| CC7.2 Monitoring | Audit log on every mutating route + LLM call, SHA-256 payload hashes |
| P6.1 Transmission integrity | PII scrubber with Luhn-validated card redaction, stable entity tokenization |
| P4.2 Retention | Configurable purge for findings / audit / LLM suggestions (2-year default) |
| CC3.1 Data classification | Field-level classification registry (public / internal / confidential / pii) |

---

## Anthropic API (optional)

CPAgent uses Claude for **rule suggestions and batch classification** — every suggestion becomes a permanent DB rule that the deterministic engine applies on subsequent parses. The LLM is never in the hot path; it's a bootstrapping tool that teaches the local rules engine.

Set your API key via the UI: **Settings → AI Settings → paste `sk-ant-…` → Save to Keychain**. The key is stored under service `com.cpagent.app` and **never written to disk or committed to any file**. The env variable `ANTHROPIC_API_KEY` is used as a fallback if the Keychain is empty.

Supported models (default Sonnet):
- `claude-opus-4-6` — most capable, most expensive
- `claude-sonnet-4-6` — **default**, best value for categorization
- `claude-haiku-4-5-20251001` — fastest, cheapest

Cost ceiling is enforced before any call: `PUT /api/llm/budget` to change it, current usage visible in AI Settings.

---

## Contributing

CPAgent is actively developed with lots of room for contributions:

- **State tax handlers** — add bracket tables for progressive-tax states in `states_registry.py` / `state_tax.py`
- **State extension PDF field maps** — add per-state maps in `filing/form_field_map.py` and generators in `filing/irs_forms.py`
- **Additional bank / credit-card PDF parsers** in `parser/pdf_parser.py`
- **Additional P&L CSV formats** in `parser/csv_parser.py::detect_csv_format()`
- **OCR fallback** for scanned PDFs
- **Windows / Linux launcher** (currently macOS-only)

See `CONTRIBUTING.md` for workflow and the `TODO:` markers in the code for starting points.

---

## License

MIT — see `LICENSE`.
