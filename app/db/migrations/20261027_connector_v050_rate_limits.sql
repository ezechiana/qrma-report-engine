-- Shared fixed-window quotas. Only HMAC bucket keys, counts and expiry.
-- Caller owns transaction. No patient, report, catalogue or credential data.
SET LOCAL lock_timeout = '5s';
CREATE TABLE IF NOT EXISTS connector_rate_limits (
 bucket_key VARCHAR(64) PRIMARY KEY,
 hits INTEGER NOT NULL CHECK (hits > 0),
 reset_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_connector_rate_limits_reset_at ON connector_rate_limits(reset_at);
