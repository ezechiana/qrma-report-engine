CREATE TABLE IF NOT EXISTS patient_claim_invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    practitioner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cancelled_at TIMESTAMPTZ NULL,
    accepted_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_patient_claim_invites_patient_id
ON patient_claim_invites (patient_id);

CREATE INDEX IF NOT EXISTS idx_patient_claim_invites_practitioner_user_id
ON patient_claim_invites (practitioner_user_id);
