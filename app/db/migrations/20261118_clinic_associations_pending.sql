CREATE UNIQUE INDEX IF NOT EXISTS uq_clinic_associations_one_pending
ON clinic_associations (personal_patient_id, practitioner_user_id)
WHERE status = 'pending';
