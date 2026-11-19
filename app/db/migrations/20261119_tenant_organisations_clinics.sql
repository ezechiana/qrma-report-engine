-- Additive registry only: existing chart ownership, shares and history stay intact.
CREATE TABLE IF NOT EXISTS organisations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name VARCHAR(255) NOT NULL CHECK (length(trim(display_name)) > 0),
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    request_key VARCHAR(120) NOT NULL,
    legacy_owner_user_id UUID UNIQUE REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_organisation_creation UNIQUE (created_by_user_id, request_key)
);
CREATE TABLE IF NOT EXISTS clinics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
    display_name VARCHAR(255) NOT NULL CHECK (length(trim(display_name)) > 0),
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    request_key VARCHAR(120) NOT NULL,
    legacy_owner_user_id UUID UNIQUE REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_clinic_creation UNIQUE (created_by_user_id, request_key)
);
CREATE INDEX IF NOT EXISTS ix_clinics_organisation_id ON clinics (organisation_id);
CREATE TABLE IF NOT EXISTS clinic_registry_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    organisation_id UUID REFERENCES organisations(id) ON DELETE RESTRICT,
    clinic_id UUID REFERENCES clinics(id) ON DELETE RESTRICT,
    action VARCHAR(40) NOT NULL,
    outcome VARCHAR(30) NOT NULL,
    policy_revision VARCHAR(40) NOT NULL DEFAULT 'clinic-registry-v1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Identity is immutable even through SQL. No deletion/update API is provided.
CREATE OR REPLACE FUNCTION protect_clinic_registry_identity() RETURNS trigger AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id THEN
        RAISE EXCEPTION 'Registry identity is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS immutable_organisation_id ON organisations;
CREATE TRIGGER immutable_organisation_id BEFORE UPDATE ON organisations
FOR EACH ROW EXECUTE FUNCTION protect_clinic_registry_identity();
DROP TRIGGER IF EXISTS immutable_clinic_id ON clinics;
CREATE TRIGGER immutable_clinic_id BEFORE UPDATE ON clinics
FOR EACH ROW EXECUTE FUNCTION protect_clinic_registry_identity();

-- Map by original user ID, never by display name. Deterministic IDs and unique
-- legacy references make an interrupted/repeated backfill safe. Settings win
-- for the display label; all original fields and timestamps are retained.
INSERT INTO organisations (id, display_name, created_by_user_id, request_key, legacy_owner_user_id, created_at)
SELECT md5('go360:legacy:organisation:' || u.id::text)::uuid,
       COALESCE(NULLIF(trim(s.clinic_name), ''), NULLIF(trim(u.clinic_name), ''), 'Legacy practice'),
       u.id, 'legacy:' || u.id::text, u.id, u.created_at
FROM users u LEFT JOIN practitioner_settings s ON s.user_id = u.id
WHERE lower(u.role) IN ('practitioner', 'admin') OR s.user_id IS NOT NULL
   OR EXISTS (SELECT 1 FROM user_capabilities c WHERE c.user_id = u.id AND c.capability = 'practitioner')
ON CONFLICT (legacy_owner_user_id) DO NOTHING;

INSERT INTO clinics (id, organisation_id, display_name, created_by_user_id, request_key, legacy_owner_user_id, created_at)
SELECT md5('go360:legacy:clinic:' || o.legacy_owner_user_id::text)::uuid,
       o.id, o.display_name, o.legacy_owner_user_id, 'legacy:' || o.legacy_owner_user_id::text,
       o.legacy_owner_user_id, o.created_at
FROM organisations o WHERE o.legacy_owner_user_id IS NOT NULL
ON CONFLICT (legacy_owner_user_id) DO NOTHING;

INSERT INTO clinic_registry_events (id, actor_user_id, organisation_id, clinic_id, action, outcome)
SELECT md5('go360:legacy:clinic-event:' || c.legacy_owner_user_id::text)::uuid,
       c.legacy_owner_user_id, c.organisation_id, c.id, 'clinic.backfill', 'mapped'
FROM clinics c WHERE c.legacy_owner_user_id IS NOT NULL
ON CONFLICT (id) DO NOTHING;
