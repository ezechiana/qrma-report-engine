-- Stripe revenue finaliser schema.
-- Safe to run repeatedly. Required by revenue dashboard and Stripe webhook pipeline.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE share_bundles
ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS platform_fee_amount INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS platform_fee_currency TEXT,
ADD COLUMN IF NOT EXISTS practitioner_payout_amount INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS stripe_fee_amount INTEGER,
ADD COLUMN IF NOT EXISTS stripe_fee_currency TEXT,
ADD COLUMN IF NOT EXISTS stripe_connect_mode TEXT DEFAULT 'platform_only',
ADD COLUMN IF NOT EXISTS stripe_connect_account_id TEXT,
ADD COLUMN IF NOT EXISTS stripe_session_id TEXT,
ADD COLUMN IF NOT EXISTS stripe_payment_intent_id TEXT,
ADD COLUMN IF NOT EXISTS stripe_charge_id TEXT,
ADD COLUMN IF NOT EXISTS stripe_transfer_id TEXT;

CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_share_bundles_payment_status
ON share_bundles(payment_status);

CREATE INDEX IF NOT EXISTS idx_share_bundles_stripe_session_id
ON share_bundles(stripe_session_id);

CREATE INDEX IF NOT EXISTS idx_share_bundles_stripe_payment_intent_id
ON share_bundles(stripe_payment_intent_id);

CREATE INDEX IF NOT EXISTS idx_share_bundles_created_by_payment
ON share_bundles(created_by_user_id, requires_payment, payment_status);

UPDATE share_bundles
SET stripe_connect_mode = COALESCE(stripe_connect_mode, 'platform_only');

UPDATE share_bundles
SET platform_fee_amount = COALESCE(platform_fee_amount, 0);

UPDATE share_bundles
SET practitioner_payout_amount = GREATEST(COALESCE(price_amount, 0) - COALESCE(platform_fee_amount, 0), 0)
WHERE requires_payment = true
  AND (practitioner_payout_amount IS NULL OR practitioner_payout_amount = 0);

UPDATE share_bundles
SET paid_at = COALESCE(paid_at, created_at)
WHERE payment_status = 'paid'
  AND paid_at IS NULL;
