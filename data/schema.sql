-- Tax Dashboard schema. All tables created idempotently at app startup.

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categorization_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL CHECK (pattern_type IN ('account_name', 'memo', 'vendor')),
    pattern TEXT NOT NULL,
    schedule_c_category TEXT,
    entity_scope TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL CHECK (source IN ('seed', 'user', 'llm_suggested')),
    reasoning TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (pattern_type, pattern, entity_scope)
);

CREATE INDEX IF NOT EXISTS idx_rules_pattern ON categorization_rules(pattern_type, pattern);
CREATE INDEX IF NOT EXISTS idx_rules_source ON categorization_rules(source);

CREATE TABLE IF NOT EXISTS findings_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL,
    year INTEGER NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('BLOCKED', 'REQUIRED', 'REVIEW', 'RESOLVED', 'LOGGED')),
    previous_severity TEXT,
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    source_file TEXT,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    UNIQUE (fingerprint, year)
);

CREATE INDEX IF NOT EXISTS idx_findings_year ON findings_history(year);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings_history(severity);

CREATE TABLE IF NOT EXISTS unknown_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_name TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    evidence_amount REAL NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'dismissed', 'resolved')),
    notes TEXT,
    UNIQUE (detected_name)
);

CREATE TABLE IF NOT EXISTS llm_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_hash TEXT NOT NULL,
    scrubbed_batch_json TEXT NOT NULL,
    response_json TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    model_id TEXT NOT NULL,
    accepted_rule_ids TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS llm_budget (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL CHECK (period IN ('month', 'session')),
    period_start TEXT NOT NULL,
    ceiling_usd REAL NOT NULL,
    spent_usd REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    payload_hash TEXT,
    result TEXT,
    ip TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

CREATE TABLE IF NOT EXISTS data_classification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    field_path TEXT NOT NULL UNIQUE,
    classification TEXT NOT NULL CHECK (classification IN ('public', 'internal', 'confidential', 'pii')),
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS parsed_cache (
    year INTEGER PRIMARY KEY,
    data_json TEXT NOT NULL,
    source_signature TEXT NOT NULL,
    cached_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS checklist_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    category TEXT NOT NULL,
    doc_name TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    reference_file TEXT,
    reference_amount REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (year, category, doc_name)
);

CREATE TABLE IF NOT EXISTS schedule_c_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    entity TEXT NOT NULL,
    line_number TEXT NOT NULL,
    note_text TEXT NOT NULL,
    implies_rule INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (year, entity, line_number)
);

CREATE TABLE IF NOT EXISTS home_office_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    address TEXT NOT NULL,
    lease_file TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    total_sqft REAL,
    office_sqft REAL,
    monthly_rent REAL,
    monthly_utilities REAL,
    monthly_insurance REAL,
    monthly_internet REAL,
    method TEXT NOT NULL DEFAULT 'simplified' CHECK (method IN ('simplified', 'regular')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (year, address, start_date)
);

CREATE TABLE IF NOT EXISTS conduit_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    counterparty_name TEXT NOT NULL,
    entity_scope TEXT,
    memo_pattern TEXT,
    treatment TEXT NOT NULL CHECK (treatment IN ('subtract_from_gross', 'book_as_contract_labor')),
    linked_1099_amount REAL,
    linked_1099_file TEXT,
    expected_annual_amount REAL,
    year INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (counterparty_name, entity_scope, year)
);
