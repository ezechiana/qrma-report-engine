CREATE TABLE IF NOT EXISTS assessment_quota_reservations (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    request_key TEXT NOT NULL,
    case_id UUID NOT NULL,
    report_id UUID UNIQUE REFERENCES report_versions(id) ON DELETE SET NULL,
    period_start DATE NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('reserved', 'consumed', 'released')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, request_key)
);

CREATE INDEX IF NOT EXISTS ix_assessment_quota_usage
ON assessment_quota_reservations (user_id, period_start, state);

-- Carry forward Assessment work already delivered in the current UTC month.
INSERT INTO assessment_quota_reservations (user_id, request_key, case_id, report_id, period_start, state)
SELECT created_by_user_id, 'legacy:' || id::text, case_id, id,
       date_trunc('month', coalesce(completed_at, generated_at) AT TIME ZONE 'UTC')::date,
       'consumed'
FROM report_versions
WHERE report_type = 'assessment' AND status::text IN ('ready', 'final')
  AND coalesce(completed_at, generated_at) >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
ON CONFLICT DO NOTHING;
