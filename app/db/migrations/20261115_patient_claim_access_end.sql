ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS claim_access_ended_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS claim_access_ended_by VARCHAR(20) NULL;
