-- Idempotent report generation support.
-- Prevents accidental v1/v2/v3 duplicates when the same case/source HTML is processed more than once.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE report_versions
ADD COLUMN IF NOT EXISTS source_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_report_versions_case_source_hash
ON report_versions(case_id, source_hash)
WHERE source_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_report_versions_source_hash
ON report_versions(source_hash)
WHERE source_hash IS NOT NULL;
