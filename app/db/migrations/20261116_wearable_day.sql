CREATE UNIQUE INDEX IF NOT EXISTS uq_source_records_wearable_day
ON source_records (patient_source_id, recorded_at)
WHERE payload_json->>'kind' = 'wearable_day';
