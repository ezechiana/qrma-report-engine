-- Additive S0 clinical evidence. Existing audit_logs and ledger names are untouched.
SET LOCAL lock_timeout = '5s';
CREATE TABLE IF NOT EXISTS clinical_audit_events (
    id UUID PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    action TEXT NOT NULL CHECK (action IN ('report.read', 'assessment_report.regenerated')),
    outcome TEXT NOT NULL CHECK (outcome IN ('allowed', 'denied', 'success')),
    effective_actor_id UUID NOT NULL,
    initiating_actor_id UUID,
    scope_kind TEXT NOT NULL DEFAULT 'legacy_practice' CHECK (scope_kind = 'legacy_practice'),
    practice_owner_id UUID NOT NULL,
    organisation_id UUID CHECK (organisation_id IS NULL),
    clinic_id UUID CHECK (clinic_id IS NULL),
    scope_reason TEXT NOT NULL DEFAULT 'legacy_unresolved' CHECK (scope_reason = 'legacy_unresolved'),
    target_type TEXT CHECK (target_type IN ('report_version', 'case')),
    target_id UUID,
    resulting_version_id UUID UNIQUE,
    correlation_id UUID NOT NULL,
    reason TEXT CHECK (reason IN ('not_found_or_not_owned', 'support_mode')),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
    CHECK ((
      (action = 'report.read' AND outcome = 'allowed' AND target_type = 'report_version'
       AND target_id IS NOT NULL AND resulting_version_id IS NULL AND reason IS NULL)
      OR (outcome = 'denied' AND target_type IS NULL AND target_id IS NULL
          AND resulting_version_id IS NULL AND reason IS NOT NULL)
      OR (action = 'assessment_report.regenerated' AND outcome = 'success'
          AND target_type = 'case' AND target_id IS NOT NULL
          AND resulting_version_id IS NOT NULL AND reason IS NULL)
    ) IS TRUE)
);
CREATE INDEX IF NOT EXISTS ix_clinical_audit_scope_time ON clinical_audit_events(practice_owner_id, occurred_at, id);
CREATE TABLE IF NOT EXISTS clinical_audit_reviewer_scopes (
    reviewer_name NAME NOT NULL,
    practice_owner_id UUID NOT NULL,
    PRIMARY KEY (reviewer_name, practice_owner_id)
);
CREATE OR REPLACE FUNCTION clinical_audit_refuse_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Clinical audit events are append only' USING ERRCODE = '42501';
END;
$$;
DROP TRIGGER IF EXISTS clinical_audit_immutable ON clinical_audit_events;
CREATE TRIGGER clinical_audit_immutable BEFORE UPDATE OR DELETE ON clinical_audit_events
FOR EACH ROW EXECUTE FUNCTION clinical_audit_refuse_mutation();
DROP TRIGGER IF EXISTS clinical_audit_no_truncate ON clinical_audit_events;
CREATE TRIGGER clinical_audit_no_truncate BEFORE TRUNCATE ON clinical_audit_events
FOR EACH STATEMENT EXECUTE FUNCTION clinical_audit_refuse_mutation();
ALTER TABLE clinical_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical_audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical_audit_reviewer_scopes ENABLE ROW LEVEL SECURITY;
-- The independently controlled owner assigns scopes; readers remain RLS scoped.
ALTER TABLE clinical_audit_reviewer_scopes NO FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS clinical_append ON clinical_audit_events;
CREATE POLICY clinical_append ON clinical_audit_events FOR INSERT WITH CHECK (true);
DROP POLICY IF EXISTS clinical_review ON clinical_audit_events;
CREATE POLICY clinical_review ON clinical_audit_events FOR SELECT USING (
    EXISTS (SELECT 1 FROM clinical_audit_reviewer_scopes s
            WHERE s.reviewer_name = session_user AND s.practice_owner_id = clinical_audit_events.practice_owner_id)
);
DROP POLICY IF EXISTS clinical_review_scope ON clinical_audit_reviewer_scopes;
CREATE POLICY clinical_review_scope ON clinical_audit_reviewer_scopes FOR SELECT
    USING (reviewer_name = session_user);
REVOKE ALL ON clinical_audit_events, clinical_audit_reviewer_scopes FROM PUBLIC;
REVOKE ALL ON FUNCTION clinical_audit_refuse_mutation() FROM PUBLIC;

-- Compatibility for the restricted application login. Previously runtime DDL;
-- identical legacy definitions, additive and preserving rows/identifiers.
CREATE TABLE IF NOT EXISTS auth_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id UUID NULL, email TEXT NULL,
    event_type TEXT NOT NULL, status TEXT NOT NULL, ip_address TEXT NULL, user_agent TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), meta JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_auth_events_created_at ON auth_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_auth_events_type_status ON auth_events(event_type,status,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_auth_events_email ON auth_events(LOWER(email));
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE, expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), used_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user_id ON password_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_expires_at ON password_reset_tokens(expires_at);
