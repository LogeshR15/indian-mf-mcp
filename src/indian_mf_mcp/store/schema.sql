-- Phase 1 schema (AMC/Scheme/Plan/NavPoint/taxonomy history + document/blob substrate
-- needed by later phases is stubbed so migrations are additive, not re-shaped).

CREATE TABLE IF NOT EXISTS amc (
    amc_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    amfi_mf_code TEXT,
    adapter_id TEXT,
    disclosure_urls_json TEXT,
    last_registry_check TEXT
);

CREATE TABLE IF NOT EXISTS scheme (
    scheme_id TEXT PRIMARY KEY,
    amc_id TEXT REFERENCES amc(amc_id),
    name TEXT NOT NULL,
    scheme_type TEXT,
    category TEXT,
    sub_category TEXT,
    benchmark_id TEXT,
    inception_date TEXT,
    objective_text_ref TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS scheme_taxonomy_history (
    scheme_id TEXT REFERENCES scheme(scheme_id),
    as_of_date TEXT NOT NULL,
    scheme_type TEXT,
    category TEXT,
    sub_category TEXT,
    raw_header_string TEXT,
    PRIMARY KEY (scheme_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS plan (
    plan_id TEXT PRIMARY KEY,
    scheme_id TEXT REFERENCES scheme(scheme_id),
    amfi_scheme_code TEXT UNIQUE NOT NULL,
    isin TEXT,
    isin_reinvest TEXT,
    plan_type TEXT,           -- Direct | Regular
    option_type TEXT,         -- Growth | IDCW
    idcw_variant TEXT,        -- Payout | Reinvest | NULL
    active INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT,
    last_seen TEXT
);

CREATE INDEX IF NOT EXISTS idx_plan_isin ON plan(isin);
CREATE INDEX IF NOT EXISTS idx_plan_scheme ON plan(scheme_id);

CREATE TABLE IF NOT EXISTS nav_point (
    plan_id TEXT REFERENCES plan(plan_id),
    date TEXT NOT NULL,
    nav REAL NOT NULL,
    PRIMARY KEY (plan_id, date)
);

CREATE INDEX IF NOT EXISTS idx_navpoint_plan_date ON nav_point(plan_id, date);

CREATE TABLE IF NOT EXISTS document (
    doc_id TEXT PRIMARY KEY,
    scheme_id TEXT REFERENCES scheme(scheme_id),
    amc_id TEXT REFERENCES amc(amc_id),
    doc_type TEXT,
    doc_date TEXT,
    source_url TEXT,
    sha256 TEXT UNIQUE,
    content_type TEXT,
    blob_path TEXT,
    retrieved_at TEXT,
    page_count INTEGER,
    parse_status TEXT,
    parse_confidence REAL
);

CREATE TABLE IF NOT EXISTS document_section (
    section_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT REFERENCES document(doc_id),
    page_number INTEGER NOT NULL,
    headings_json TEXT,   -- JSON list of SEBI-standard headings detected on this page
    text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_docsection_doc ON document_section(doc_id);

CREATE TABLE IF NOT EXISTS benchmark (
    benchmark_id TEXT PRIMARY KEY,
    name TEXT,
    is_proxy INTEGER NOT NULL DEFAULT 0,
    proxy_plan_id TEXT REFERENCES plan(plan_id),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    scheme_id TEXT REFERENCES scheme(scheme_id),
    as_of_date TEXT NOT NULL,
    disclosure_type TEXT,            -- monthly | fortnightly | halfyearly
    source_doc_id TEXT REFERENCES document(doc_id),
    total_market_value_lakhs REAL,
    grand_total_pct_nav REAL,
    reconciliation_ok INTEGER,        -- 0/1/NULL
    benchmark_name TEXT,
    retrieved_at TEXT,
    parse_confidence REAL,
    UNIQUE(scheme_id, as_of_date, disclosure_type)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_scheme_date ON portfolio_snapshot(scheme_id, as_of_date);

CREATE TABLE IF NOT EXISTS holding (
    holding_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT REFERENCES portfolio_snapshot(snapshot_id),
    isin TEXT,
    instrument_name TEXT NOT NULL,
    industry_or_rating TEXT,
    quantity REAL,
    market_value_lakhs REAL,
    pct_nav REAL,
    asset_class TEXT,           -- equity | debt | derivative | cash | foreign | reit | other
    listed INTEGER,
    section_label TEXT
);

CREATE INDEX IF NOT EXISTS idx_holding_snapshot ON holding(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_holding_isin ON holding(isin);

CREATE TABLE IF NOT EXISTS derivative_position (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT REFERENCES portfolio_snapshot(snapshot_id),
    instrument_name TEXT NOT NULL,
    direction TEXT,             -- Long | Short | NULL
    quantity REAL,
    market_value_lakhs REAL,
    pct_to_aum REAL,
    section_label TEXT
);

CREATE INDEX IF NOT EXISTS idx_deriv_snapshot ON derivative_position(snapshot_id);

CREATE TABLE IF NOT EXISTS ingest_run (
    run_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    detail_json TEXT
);
