-- RBAC + Feature Flags migration
-- Safe to run repeatedly through the existing schema_migrations runner.

ALTER TABLE users
ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'practitioner';

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO app_settings (key, value, updated_at)
VALUES (
    'feature_flags_config',
    '{
      "referrals_enabled": true,
      "subscriptions_enabled": true,
      "paid_shares_enabled": true,
      "stripe_connect_enabled": true,
      "revenue_dashboard_enabled": true,
      "trend_reports_enabled": true,
      "ai_recommendations_enabled": true,
      "platform_settings_enabled": true
    }'::jsonb,
    NOW()
)
ON CONFLICT (key) DO NOTHING;
