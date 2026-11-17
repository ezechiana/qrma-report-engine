ALTER TABLE patient_claim_invites
    ADD COLUMN IF NOT EXISTS reserved_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS reserved_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS consent_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS consent_version VARCHAR(40) NULL,
    ADD COLUMN IF NOT EXISTS claimed_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS claimed_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_patient_claim_invites_reserved_by
ON patient_claim_invites (reserved_by_user_id);

CREATE INDEX IF NOT EXISTS idx_patient_claim_invites_claimed_by
ON patient_claim_invites (claimed_by_user_id);

CREATE INDEX IF NOT EXISTS idx_patients_claimed_by
ON patients (claimed_by_user_id);
