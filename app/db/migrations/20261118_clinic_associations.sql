CREATE TABLE IF NOT EXISTS clinic_associations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    personal_patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    clinic_patient_id UUID NULL REFERENCES patients(id) ON DELETE CASCADE,
    practitioner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL,
    initiated_by VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ NULL,
    ended_at TIMESTAMPTZ NULL,
    ended_by VARCHAR(20) NULL
);

CREATE INDEX IF NOT EXISTS idx_clinic_associations_personal_patient_id
ON clinic_associations (personal_patient_id);

CREATE INDEX IF NOT EXISTS idx_clinic_associations_clinic_patient_id
ON clinic_associations (clinic_patient_id);

CREATE INDEX IF NOT EXISTS idx_clinic_associations_practitioner_user_id
ON clinic_associations (practitioner_user_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_clinic_associations_one_active
ON clinic_associations (personal_patient_id)
WHERE status = 'active';

WITH chosen AS (
    SELECT DISTINCT ON (claimed_by_user_id)
        id,
        user_id,
        claimed_by_user_id,
        claimed_at
    FROM patients
    WHERE claimed_by_user_id IS NOT NULL
    ORDER BY claimed_by_user_id, claimed_at NULLS LAST, created_at
)
UPDATE patients
SET subject_user_id = chosen.claimed_by_user_id
FROM chosen
WHERE patients.id = chosen.id
  AND patients.subject_user_id IS NULL
  AND NOT EXISTS (
      SELECT 1
      FROM patients AS personal
      WHERE personal.subject_user_id = chosen.claimed_by_user_id
  );

WITH chosen AS (
    SELECT DISTINCT ON (claimed_by_user_id)
        id,
        user_id,
        claimed_by_user_id,
        claimed_at
    FROM patients
    WHERE claimed_by_user_id IS NOT NULL
    ORDER BY claimed_by_user_id, claimed_at NULLS LAST, created_at
)
INSERT INTO clinic_associations (
    personal_patient_id,
    clinic_patient_id,
    practitioner_user_id,
    status,
    initiated_by,
    created_at,
    activated_at
)
SELECT
    COALESCE(
        (
            SELECT personal.id
            FROM patients AS personal
            WHERE personal.subject_user_id = chosen.claimed_by_user_id
            LIMIT 1
        ),
        chosen.id
    ),
    chosen.id,
    chosen.user_id,
    'active',
    'practitioner',
    COALESCE(chosen.claimed_at, NOW()),
    COALESCE(chosen.claimed_at, NOW())
FROM chosen
WHERE NOT EXISTS (
    SELECT 1
    FROM clinic_associations AS existing
    WHERE existing.personal_patient_id = COALESCE(
        (
            SELECT personal.id
            FROM patients AS personal
            WHERE personal.subject_user_id = chosen.claimed_by_user_id
            LIMIT 1
        ),
        chosen.id
    )
      AND existing.status = 'active'
);
