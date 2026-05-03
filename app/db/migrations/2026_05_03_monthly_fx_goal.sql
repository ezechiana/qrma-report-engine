-- Monthly revenue goal + FX snapshot support
-- Actual revenue remains stored and reported in original transaction currencies.

ALTER TABLE practitioner_settings
ADD COLUMN IF NOT EXISTS preferred_currency TEXT DEFAULT 'USD';

ALTER TABLE practitioner_settings
ADD COLUMN IF NOT EXISTS monthly_goal_minor INTEGER DEFAULT 200000;

CREATE TABLE IF NOT EXISTS fx_rates_monthly (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    effective_month DATE NOT NULL UNIQUE,
    base_currency TEXT NOT NULL DEFAULT 'USD',
    rates JSONB NOT NULL,
    source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fx_rates_monthly_effective_month
ON fx_rates_monthly (effective_month DESC);

-- Seed a safe fallback for the current deployment month. The app will replace or
-- add the current monthly snapshot from the configured FX provider when reachable.
INSERT INTO fx_rates_monthly (effective_month, base_currency, rates, source)
VALUES (
    DATE_TRUNC('month', NOW())::date,
    'USD',
    '{"USD":1,"GBP":0.79,"EUR":0.92,"AED":3.6725,"JPY":155,"KRW":1350}'::jsonb,
    'migration_fallback_seed'
)
ON CONFLICT (effective_month) DO NOTHING;
