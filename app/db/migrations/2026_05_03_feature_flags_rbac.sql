CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE users
ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'practitioner';

CREATE INDEX IF NOT EXISTS ix_users_role ON users(role);

INSERT INTO app_settings (key, value)
VALUES (
    'features_config',
    jsonb_build_object(
        'referrals_enabled', true,
        'paid_bundles_enabled', true,
        'trend_reports_enabled', true,
        'share_analytics_enabled', true,
        'fx_goal_tracking_enabled', true,
        'ai_recommendations_enabled', true,
        'platform_settings_enabled', true
    )
)
ON CONFLICT (key) DO NOTHING;
