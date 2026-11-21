-- Provision legacy runtime-created objects before switching to a non-owner login.
-- Preserve table/row identities and all historical migration filenames.
SET LOCAL lock_timeout = '5s';
CREATE TABLE IF NOT EXISTS share_link_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), link_token TEXT NOT NULL,
    event_type TEXT NOT NULL, metadata JSONB, metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE share_link_events ADD COLUMN IF NOT EXISTS metadata JSONB;
ALTER TABLE share_link_events ADD COLUMN IF NOT EXISTS metadata_json JSONB;
CREATE INDEX IF NOT EXISTS idx_share_link_events_token ON share_link_events(link_token);
CREATE INDEX IF NOT EXISTS idx_share_link_events_event_type ON share_link_events(event_type);
CREATE INDEX IF NOT EXISTS idx_share_link_events_created_at ON share_link_events(created_at);
CREATE INDEX IF NOT EXISTS idx_share_link_events_token_type_created ON share_link_events(link_token,event_type,created_at DESC);
CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), payload JSONB
);
ALTER TABLE stripe_webhook_events ADD COLUMN IF NOT EXISTS payload JSONB;
CREATE TABLE IF NOT EXISTS platform_admin_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), actor_user_id UUID NULL,
    actor_email TEXT NULL, action TEXT NOT NULL, target_user_id UUID NULL,
    target_email TEXT NULL, details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_platform_admin_audit_created_at ON platform_admin_audit_log(created_at DESC);
