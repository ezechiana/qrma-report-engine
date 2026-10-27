-- DEV: additive, nullable and deliberately NON-UNIQUE (Import anyway supported).
BEGIN;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS assessment_fingerprint VARCHAR(80);
CREATE INDEX IF NOT EXISTS ix_cases_user_assessment_fingerprint
    ON cases (user_id, assessment_fingerprint)
    WHERE assessment_fingerprint IS NOT NULL;
COMMIT;
