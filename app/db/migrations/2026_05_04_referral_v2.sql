-- Referral V2 schema hardening / migration
-- Safe to run repeatedly.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS referral_code TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code_unique
ON users (referral_code)
WHERE referral_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS referrals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    referred_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    referral_code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'signed_up',
    reward_months INTEGER NOT NULL DEFAULT 1,
    converted_at TIMESTAMPTZ NULL,
    rewarded_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE referrals ADD COLUMN IF NOT EXISTS referral_code TEXT;
ALTER TABLE referrals ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'signed_up';
ALTER TABLE referrals ADD COLUMN IF NOT EXISTS reward_months INTEGER NOT NULL DEFAULT 1;
ALTER TABLE referrals ADD COLUMN IF NOT EXISTS converted_at TIMESTAMPTZ NULL;
ALTER TABLE referrals ADD COLUMN IF NOT EXISTS rewarded_at TIMESTAMPTZ NULL;
ALTER TABLE referrals ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE referrals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS idx_referrals_referred_user_unique
ON referrals (referred_user_id);

CREATE INDEX IF NOT EXISTS idx_referrals_referrer_user_id
ON referrals (referrer_user_id);

CREATE INDEX IF NOT EXISTS idx_referrals_status
ON referrals (status);

CREATE TABLE IF NOT EXISTS referral_rewards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referral_id UUID NULL REFERENCES referrals(id) ON DELETE SET NULL,
    referrer_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    referred_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reward_type TEXT NOT NULL DEFAULT 'free_months',
    reward_months INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'applied',
    applied_at TIMESTAMPTZ NULL,
    used_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_rewards_once_per_pair
ON referral_rewards (referrer_user_id, referred_user_id);

CREATE INDEX IF NOT EXISTS idx_referral_rewards_referrer
ON referral_rewards (referrer_user_id);
