-- Preserve existing principal IDs. Never infer provider bindings from email.
ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_revision INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS external_identities (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(30) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_external_identity_subject UNIQUE (provider, subject)
);
CREATE INDEX IF NOT EXISTS ix_external_identities_user_id ON external_identities(user_id);
