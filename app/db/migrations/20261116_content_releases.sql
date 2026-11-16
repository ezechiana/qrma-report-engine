CREATE TABLE IF NOT EXISTS content_releases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    practitioner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_key VARCHAR(40) NOT NULL,
    direction VARCHAR(40) NOT NULL,
    released BOOLEAN NOT NULL DEFAULT FALSE,
    released_at TIMESTAMPTZ NULL,
    withdrawn_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_content_releases_patient_source_direction UNIQUE (patient_id, source_key, direction)
);

CREATE INDEX IF NOT EXISTS ix_content_releases_patient_id ON content_releases (patient_id);
CREATE INDEX IF NOT EXISTS ix_content_releases_practitioner_user_id ON content_releases (practitioner_user_id);
