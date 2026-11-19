-- Make the existing individual-share payment schema reproducible on fresh DBs.
-- Existing installations retain all prices, payment states and Stripe IDs.
ALTER TABLE share_links
    ADD COLUMN IF NOT EXISTS requires_payment BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS payment_status TEXT DEFAULT 'not_required',
    ADD COLUMN IF NOT EXISTS price_amount INTEGER,
    ADD COLUMN IF NOT EXISTS price_currency TEXT DEFAULT 'gbp',
    ADD COLUMN IF NOT EXISTS access_label TEXT,
    ADD COLUMN IF NOT EXISTS stripe_checkout_session_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_payment_intent_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_connect_account_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_connect_mode TEXT,
    ADD COLUMN IF NOT EXISTS platform_fee_amount INTEGER,
    ADD COLUMN IF NOT EXISTS platform_fee_currency TEXT,
    ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ;
