-- DEV connector v0.4. Additive migration; no existing parser/case tables altered.
CREATE TABLE IF NOT EXISTS connector_devices (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES users(id), credential_hash VARCHAR(64) NOT NULL UNIQUE,
 expires_at TIMESTAMPTZ NOT NULL, revoked BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_connector_devices_owner_id ON connector_devices(owner_id);
CREATE TABLE IF NOT EXISTS connector_pairings (
 id UUID PRIMARY KEY, connector_id UUID NOT NULL, challenge VARCHAR(64) NOT NULL,
 code_hash VARCHAR(64) NOT NULL UNIQUE, owner_id UUID REFERENCES users(id), expires_at TIMESTAMPTZ NOT NULL,
 approved BOOLEAN NOT NULL DEFAULT FALSE, consumed BOOLEAN NOT NULL DEFAULT FALSE, attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS connector_assessments (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES users(id), connector_id UUID NOT NULL,
 job_id UUID NOT NULL, source_profile VARCHAR(80) NOT NULL, captured_at TIMESTAMPTZ NOT NULL,
 received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 raw_sha256 VARCHAR(64) NOT NULL, fingerprint VARCHAR(80), source_html BYTEA NOT NULL,
 parsed_metadata JSON NOT NULL, duplicate JSON, state VARCHAR(40) NOT NULL,
 patient_id UUID REFERENCES patients(id), case_id UUID REFERENCES cases(id), report_id UUID REFERENCES report_versions(id),
 CONSTRAINT uq_connector_job UNIQUE(owner_id,connector_id,job_id)
);
CREATE INDEX IF NOT EXISTS ix_connector_assessments_owner_id ON connector_assessments(owner_id);
CREATE INDEX IF NOT EXISTS ix_connector_assessments_raw_sha256 ON connector_assessments(raw_sha256);
CREATE INDEX IF NOT EXISTS ix_connector_assessments_fingerprint ON connector_assessments(fingerprint);
