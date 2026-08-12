-- ============================================
-- Migration 004: Monetization Layer
-- VidGrab — Tier system, Stripe billing, API keys
-- ============================================
-- Safe to run multiple times (IF NOT EXISTS / OR REPLACE / ADD COLUMN IF NOT EXISTS)

-- ── Tier + billing columns on profiles ───────────────────────────────

ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS tier                    TEXT         DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS billing_status          TEXT         DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS subscription_expiry     TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS stripe_customer_id      TEXT,
  ADD COLUMN IF NOT EXISTS stripe_subscription_id  TEXT;

-- Constrain tier values
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'profiles_tier_check'
  ) THEN
    ALTER TABLE profiles
      ADD CONSTRAINT profiles_tier_check CHECK (tier IN ('free', 'pro'));
  END IF;
END $$;

-- ── user_api_keys table ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_api_keys (
  user_id      TEXT        PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
  key_hash     TEXT        NOT NULL UNIQUE,
  is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_user_api_keys_key_hash   ON user_api_keys (key_hash)   WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_user_api_keys_user_active ON user_api_keys (user_id)   WHERE is_active = TRUE;

-- ── user_usage table (daily + monthly counters) ───────────────────────

CREATE TABLE IF NOT EXISTS user_usage (
  user_id              TEXT        PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
  downloads_today      INTEGER     NOT NULL DEFAULT 0,
  downloads_this_month INTEGER     NOT NULL DEFAULT 0,
  bulk_jobs_count      INTEGER     NOT NULL DEFAULT 0,
  last_reset_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Nightly reset function: called by pg_cron or external scheduler
CREATE OR REPLACE FUNCTION reset_daily_usage()
RETURNS void AS $$
BEGIN
  UPDATE user_usage SET downloads_today = 0, last_reset_at = NOW(), updated_at = NOW();
END;
$$ LANGUAGE plpgsql;

-- Monthly reset function
CREATE OR REPLACE FUNCTION reset_monthly_usage()
RETURNS void AS $$
BEGIN
  UPDATE user_usage SET downloads_this_month = 0, updated_at = NOW();
END;
$$ LANGUAGE plpgsql;

-- Trigger: auto-create usage row when profiles row is created
CREATE OR REPLACE FUNCTION create_user_usage_row()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO user_usage (user_id)
  VALUES (NEW.id)
  ON CONFLICT (user_id) DO NOTHING;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS profiles_create_usage ON profiles;
CREATE TRIGGER profiles_create_usage
  AFTER INSERT ON profiles
  FOR EACH ROW EXECUTE FUNCTION create_user_usage_row();

-- Backfill usage rows for existing profiles
INSERT INTO user_usage (user_id)
SELECT id FROM profiles
ON CONFLICT (user_id) DO NOTHING;

-- ── RLS policies ──────────────────────────────────────────────────────

-- user_api_keys: users can only see/modify their own rows
ALTER TABLE user_api_keys ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "user_api_keys_self" ON user_api_keys;
CREATE POLICY "user_api_keys_self" ON user_api_keys
  FOR ALL
  USING (user_id = auth.uid()::TEXT)
  WITH CHECK (user_id = auth.uid()::TEXT);

-- Service role bypasses RLS (used by backend service client)
-- No additional grant needed — service_role already bypasses RLS.

-- user_usage: users can only read their own row (backend writes via service role)
ALTER TABLE user_usage ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "user_usage_self_read" ON user_usage;
CREATE POLICY "user_usage_self_read" ON user_usage
  FOR SELECT
  USING (user_id = auth.uid()::TEXT);
