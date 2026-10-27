-- Additive only. Apply explicitly before loading updated ORM models.
-- The filename runner owns the transaction; no CONCURRENTLY.
SET LOCAL lock_timeout = '5s';
ALTER TABLE connector_devices
 ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ,
 ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ,
 ADD COLUMN IF NOT EXISTS last_ip_address VARCHAR(45),
 ADD COLUMN IF NOT EXISTS connector_version VARCHAR(64),
 ADD COLUMN IF NOT EXISTS platform VARCHAR(20);
CREATE INDEX IF NOT EXISTS ix_connector_devices_last_seen_at ON connector_devices(last_seen_at);
ALTER TABLE audit_logs
 ADD COLUMN IF NOT EXISTS connector_id UUID,
 ADD COLUMN IF NOT EXISTS job_id UUID,
 ADD COLUMN IF NOT EXISTS assessment_id UUID,
 ADD COLUMN IF NOT EXISTS actor_type VARCHAR(20),
 ADD COLUMN IF NOT EXISTS outcome VARCHAR(20),
 ADD COLUMN IF NOT EXISTS request_id UUID,
 ADD COLUMN IF NOT EXISTS ip_address VARCHAR(45),
 ADD COLUMN IF NOT EXISTS event_key VARCHAR(200);
CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_logs_event_key ON audit_logs(event_key);
CREATE INDEX IF NOT EXISTS ix_audit_logs_connector_id ON audit_logs(connector_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_job_id ON audit_logs(job_id);
CREATE INDEX IF NOT EXISTS ix_connector_audit_time ON audit_logs(created_at DESC, id DESC)
 WHERE actor_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_connector_assessments_device_received
 ON connector_assessments(owner_id, connector_id, received_at);
