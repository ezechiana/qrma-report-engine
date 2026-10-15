CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fx_rates_monthly (
    id SERIAL PRIMARY KEY,
    effective_month DATE NOT NULL UNIQUE,
    base_currency TEXT NOT NULL DEFAULT 'USD',
    rates JSONB NOT NULL,
    source TEXT DEFAULT 'manual',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE practitioner_settings
ADD COLUMN IF NOT EXISTS preferred_currency TEXT DEFAULT 'USD';

ALTER TABLE practitioner_settings
ADD COLUMN IF NOT EXISTS monthly_goal_minor INTEGER DEFAULT 200000;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS referral_code TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS ix_users_referral_code_unique
ON users(referral_code)
WHERE referral_code IS NOT NULL;

INSERT INTO app_settings (key, value)
VALUES
('referral_config', jsonb_build_object('enabled', true, 'reward_type', 'free_months', 'reward_months', 1, 'trigger', 'referred_user_becomes_paid')),
('revenue_config', jsonb_build_object('fee_model', 'platform_absorbs', 'default_currency', 'USD', 'default_monthly_goal_minor', 200000)),
('fx_config', jsonb_build_object('provider_url', 'https://open.er-api.com/v6/latest/USD', 'provider_timeout_seconds', 6, 'update_schedule_label', '1st of each month, 01:00 UK time')),
('subscription_config', jsonb_build_object('trial_days', 30, 'plan_code', 'practitioner_monthly', 'plan_name', 'go360 Practitioner', 'price_label', '$59/month', 'trial_label', '30-day free trial', 'allow_promotion_codes', true, 'subscription_required', true)),
('share_config', jsonb_build_object('default_share_price_amount', 2500, 'default_share_price_currency', 'gbp', 'share_access_cookie_max_age', 43200)),
('stripe_connect_config', jsonb_build_object('enabled', true, 'account_type', 'express', 'country', 'GB', 'fallback_to_platform', true, 'platform_fee_percent', 15, 'platform_fee_fixed_amount', 0)),
('report_config', jsonb_build_object('recommendation_mode', 'vitalhealth_clinical_optimised', 'tone', 'clinical', 'include_toc', true, 'include_appendix', true, 'include_product_recommendations', true, 'max_sections', 6, 'max_markers_per_section', 3))
ON CONFLICT (key) DO NOTHING;

INSERT INTO fx_rates_monthly (effective_month, base_currency, rates, source)
VALUES (
    DATE_TRUNC('month', CURRENT_DATE)::date,
    'USD',
    '{"USD":1,"GBP":0.79,"EUR":0.92,"AED":3.6725,"JPY":155,"KRW":1350}'::jsonb,
    'migration_fallback_seed'
)
ON CONFLICT (effective_month) DO NOTHING;
