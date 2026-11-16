ALTER TABLE content_releases ALTER COLUMN source_key TYPE VARCHAR(80);

CREATE TABLE IF NOT EXISTS patient_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    practitioner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_type VARCHAR(40) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    external_account_ref VARCHAR(255) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_patient_sources_patient_type UNIQUE (patient_id, source_type)
);

CREATE INDEX IF NOT EXISTS ix_patient_sources_patient_id ON patient_sources (patient_id);
CREATE INDEX IF NOT EXISTS ix_patient_sources_practitioner_user_id ON patient_sources (practitioner_user_id);

CREATE TABLE IF NOT EXISTS source_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_source_id UUID NOT NULL REFERENCES patient_sources(id) ON DELETE CASCADE,
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    recorded_at TIMESTAMPTZ NOT NULL,
    title VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ready',
    payload_json JSONB NOT NULL DEFAULT '{}',
    client_summary TEXT NULL,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_source_records_patient_source_id ON source_records (patient_source_id);
CREATE INDEX IF NOT EXISTS ix_source_records_patient_id ON source_records (patient_id);
CREATE INDEX IF NOT EXISTS ix_source_records_created_by_user_id ON source_records (created_by_user_id);
