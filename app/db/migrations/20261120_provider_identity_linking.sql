-- Runs after practitioner_identity; existing principal/binding IDs are retained.
ALTER TABLE external_identities ADD COLUMN IF NOT EXISTS email VARCHAR(320);
CREATE UNIQUE INDEX IF NOT EXISTS uq_external_identity_user_provider ON external_identities(user_id,provider);
CREATE TABLE IF NOT EXISTS identity_link_attempts (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(30) NOT NULL,
    state_hash VARCHAR(64) NOT NULL UNIQUE,
    browser_hash VARCHAR(64) NOT NULL,
    session_hash VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    candidate_subject VARCHAR(255),
    candidate_email VARCHAR(320),
    password_failures INTEGER NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_identity_link_attempts_user_id ON identity_link_attempts(user_id);
CREATE INDEX IF NOT EXISTS ix_identity_link_attempts_expires_at ON identity_link_attempts(expires_at);
