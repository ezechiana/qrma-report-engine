ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS subject_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_patients_subject_user_id
ON patients (subject_user_id);
