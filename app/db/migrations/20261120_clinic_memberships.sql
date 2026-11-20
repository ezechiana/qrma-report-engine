-- Administrative participation only. No credential, clinical grant or seat backfill.
CREATE TABLE IF NOT EXISTS clinic_memberships (
    id UUID PRIMARY KEY,
    clinic_id UUID NOT NULL REFERENCES clinics(id) ON DELETE RESTRICT,
    practitioner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    revision INTEGER NOT NULL DEFAULT 0,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    request_key VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_clinic_membership_principal UNIQUE (clinic_id, practitioner_user_id),
    CONSTRAINT uq_clinic_membership_request UNIQUE (created_by_user_id, request_key),
    CONSTRAINT ck_clinic_membership_status CHECK (status IN ('active','suspended','revoked')),
    CONSTRAINT ck_clinic_membership_revision CHECK (revision >= 0)
);
CREATE INDEX IF NOT EXISTS ix_clinic_memberships_clinic_id ON clinic_memberships(clinic_id);
CREATE INDEX IF NOT EXISTS ix_clinic_memberships_practitioner_user_id ON clinic_memberships(practitioner_user_id);
CREATE TABLE IF NOT EXISTS clinic_membership_events (
    id UUID PRIMARY KEY,
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    membership_id UUID REFERENCES clinic_memberships(id) ON DELETE RESTRICT,
    clinic_id UUID REFERENCES clinics(id) ON DELETE RESTRICT,
    action VARCHAR(40) NOT NULL,
    outcome VARCHAR(30) NOT NULL,
    prior_status VARCHAR(20),
    new_status VARCHAR(20),
    revision INTEGER,
    policy_revision VARCHAR(40) NOT NULL DEFAULT 'membership-admin-v1',
    original_admin_user_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_clinic_membership_events_membership_id ON clinic_membership_events(membership_id);
