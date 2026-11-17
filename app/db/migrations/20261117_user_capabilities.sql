CREATE TABLE IF NOT EXISTS user_capabilities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    capability VARCHAR(40) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, capability)
);

CREATE INDEX IF NOT EXISTS idx_user_capabilities_user_id
ON user_capabilities (user_id);

INSERT INTO user_capabilities (user_id, capability)
SELECT id, 'practitioner'
FROM users
WHERE lower(role) = 'practitioner'
ON CONFLICT (user_id, capability) DO NOTHING;

INSERT INTO user_capabilities (user_id, capability)
SELECT id, 'admin'
FROM users
WHERE lower(role) = 'admin'
ON CONFLICT (user_id, capability) DO NOTHING;
