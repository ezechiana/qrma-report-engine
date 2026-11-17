CREATE TABLE IF NOT EXISTS source_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_record_id UUID NOT NULL REFERENCES source_records(id) ON DELETE CASCADE,
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    code VARCHAR(64) NULL,
    name VARCHAR(255) NOT NULL,
    value_text VARCHAR(255) NULL,
    value_numeric NUMERIC(18, 6) NULL,
    unit VARCHAR(40) NULL,
    reference_low NUMERIC(18, 6) NULL,
    reference_high NUMERIC(18, 6) NULL,
    reference_text VARCHAR(255) NULL,
    interpretation VARCHAR(40) NULL,
    observed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_source_observations_record_position UNIQUE (source_record_id, position)
);

CREATE INDEX IF NOT EXISTS ix_source_observations_source_record_id ON source_observations (source_record_id);
CREATE INDEX IF NOT EXISTS ix_source_observations_patient_id ON source_observations (patient_id);
