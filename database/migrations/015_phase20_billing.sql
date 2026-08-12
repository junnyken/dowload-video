-- ============================================================
-- Migration 015 — Phase 20: Full Monetization & Billing
-- ============================================================
-- Adds:
--   plans             — plan definitions (DB-driven, seeds all tiers)
--   usage_events      — raw metering events (idempotent, precise)
--   payment_events    — Stripe webhook idempotency log
--   credit_grants     — promotional / comp credits
-- Extends:
--   profiles          — billing_period, trial_ends_at, grace_period_ends_at, seats_*
--   user_usage        — ai_analyses_today, zip_exports_today
--   user_credits      — ensure table exists (created in migration 004 logic)
-- ============================================================

-- ─── plans (source of truth for tier definitions) ─────────────────────

CREATE TABLE IF NOT EXISTS plans (
  code             TEXT PRIMARY KEY,              -- 'free', 'pro', 'team', 'api', 'enterprise'
  name             TEXT NOT NULL,
  description      TEXT,
  price_monthly_cents   INTEGER,                  -- NULL = custom / contact-sales
  price_yearly_cents    INTEGER,
  stripe_price_id_monthly  TEXT,
  stripe_price_id_yearly   TEXT,
  limits           JSONB NOT NULL DEFAULT '{}',
  features         JSONB NOT NULL DEFAULT '[]',   -- list of feature flags
  is_public        BOOLEAN DEFAULT TRUE,
  sort_order       INTEGER DEFAULT 0,
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Seed / upsert plan definitions
INSERT INTO plans (code, name, description, price_monthly_cents, price_yearly_cents, limits, features, sort_order)
VALUES
  ('free', 'Free', 'Perfect for occasional use', 0, 0,
    '{"downloads_per_day": 10, "batch_size_max": 5, "max_height": 1080, "history_days": 7,
      "ai_analyses_per_day": 0, "concurrent_jobs": 2, "api_calls_per_month": 0,
      "history_limit": 20, "seats_max": 1}',
    '["basic_platforms"]',
    0),
  ('pro', 'Pro', 'For power users who download daily', 999, 7900,
    '{"downloads_per_day": 100, "batch_size_max": 100, "max_height": 2160, "history_days": 90,
      "ai_analyses_per_day": 50, "concurrent_jobs": 10, "api_calls_per_month": 3000,
      "history_limit": null, "seats_max": 1}',
    '["bulk_zip","youtube_video","spotify_full","cloud_save","chapters","logo_inpaint",
      "api_key","webhook","ai_tools","priority_queue"]',
    1),
  ('team', 'Team', 'Shared workspace for agencies & teams', 2999, 23900,
    '{"downloads_per_day": 500, "batch_size_max": 200, "max_height": 2160, "history_days": 90,
      "ai_analyses_per_day": 200, "concurrent_jobs": 30, "api_calls_per_month": 10000,
      "history_limit": null, "seats_max": 5}',
    '["bulk_zip","youtube_video","spotify_full","cloud_save","chapters","logo_inpaint",
      "api_key","webhook","ai_tools","priority_queue","team_workspace","shared_history"]',
    2),
  ('api', 'API Partner', 'Programmatic access for developers', 1999, 15900,
    '{"downloads_per_day": 1000, "batch_size_max": 500, "max_height": 2160, "history_days": 90,
      "ai_analyses_per_day": 100, "concurrent_jobs": 50, "api_calls_per_month": 1000,
      "history_limit": null, "seats_max": 1}',
    '["bulk_zip","youtube_video","spotify_full","cloud_save","chapters","logo_inpaint",
      "api_key","webhook","ai_tools","priority_queue","partner_api","hmac_webhooks"]',
    3),
  ('enterprise', 'Enterprise', 'Custom contract, self-hosted, SLA', NULL, NULL,
    '{"downloads_per_day": -1, "batch_size_max": -1, "max_height": 2160, "history_days": -1,
      "ai_analyses_per_day": -1, "concurrent_jobs": -1, "api_calls_per_month": -1,
      "history_limit": null, "seats_max": -1}',
    '["bulk_zip","youtube_video","spotify_full","cloud_save","chapters","logo_inpaint",
      "api_key","webhook","ai_tools","priority_queue","team_workspace","shared_history",
      "partner_api","hmac_webhooks","sso","white_label","self_hosted","sla","dedicated_support"]',
    4)
ON CONFLICT (code) DO UPDATE SET
  limits     = EXCLUDED.limits,
  features   = EXCLUDED.features,
  price_monthly_cents = EXCLUDED.price_monthly_cents,
  price_yearly_cents  = EXCLUDED.price_yearly_cents,
  updated_at = NOW();

-- ─── usage_events (raw metering — idempotent via idempotency_key) ──────

CREATE TABLE IF NOT EXISTS usage_events (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          TEXT,                          -- auth UID (nullable for anon)
  workspace_id     UUID,
  tenant_id        UUID,
  event_type       TEXT NOT NULL,                 -- 'download', 'ai_analysis', 'batch_job', 'api_call', 'zip_export'
  job_id           TEXT,                          -- download_jobs.id or analysis_jobs.id
  metric           TEXT NOT NULL,                 -- 'downloads', 'ai_analyses', 'batch_items', 'api_calls'
  quantity         INTEGER NOT NULL DEFAULT 1,
  idempotency_key  TEXT UNIQUE,                   -- '{event_type}:{job_id}' — prevents double-count
  plan             TEXT,                          -- plan at time of event
  metadata         JSONB,
  created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usage_events_user_date
  ON usage_events (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_events_workspace_date
  ON usage_events (workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_events_type_date
  ON usage_events (event_type, created_at);

-- ─── payment_events (Stripe webhook idempotency) ──────────────────────

CREATE TABLE IF NOT EXISTS payment_events (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider            TEXT NOT NULL DEFAULT 'stripe',
  provider_event_id   TEXT NOT NULL,              -- Stripe evt_... (UNIQUE per provider)
  event_type          TEXT NOT NULL,              -- 'checkout.session.completed' etc.
  processed           BOOLEAN DEFAULT FALSE,
  processed_at        TIMESTAMPTZ,
  error               TEXT,
  raw_payload         JSONB,
  created_at          TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (provider, provider_event_id)
);

CREATE INDEX IF NOT EXISTS idx_payment_events_provider
  ON payment_events (provider, provider_event_id);

-- ─── credit_grants (promo / comp / admin credits) ─────────────────────

CREATE TABLE IF NOT EXISTS credit_grants (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          TEXT NOT NULL,                 -- auth UID
  workspace_id     UUID,
  granted_by       TEXT,                          -- admin user_id or 'system'
  amount           INTEGER NOT NULL,              -- credits granted
  reason           TEXT NOT NULL,                 -- 'welcome_bonus','refund','promo','admin_comp','referral'
  expires_at       TIMESTAMPTZ,                   -- NULL = never expires
  is_active        BOOLEAN DEFAULT TRUE,
  used_amount      INTEGER DEFAULT 0,             -- how many credits consumed from this grant
  metadata         JSONB,
  created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_credit_grants_user
  ON credit_grants (user_id, is_active);

-- ─── Extend profiles for full billing lifecycle ───────────────────────

ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS billing_period      TEXT    DEFAULT 'monthly',     -- 'monthly' | 'yearly'
  ADD COLUMN IF NOT EXISTS trial_ends_at       TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS grace_period_ends_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS seats_used          INTEGER DEFAULT 1,
  ADD COLUMN IF NOT EXISTS seats_max           INTEGER DEFAULT 1,
  ADD COLUMN IF NOT EXISTS plan_name           TEXT    DEFAULT 'Free',        -- display label
  ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN DEFAULT FALSE;

-- ─── Extend user_usage for Phase 20 metrics ──────────────────────────

ALTER TABLE user_usage
  ADD COLUMN IF NOT EXISTS ai_analyses_today   INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS zip_exports_today    INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS batch_items_today    INTEGER DEFAULT 0;

-- ─── Ensure user_credits exists (credits.py depends on it) ────────────

CREATE TABLE IF NOT EXISTS user_credits (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        TEXT NOT NULL UNIQUE,
  balance        INTEGER DEFAULT 0,
  total_earned   INTEGER DEFAULT 0,
  total_spent    INTEGER DEFAULT 0,
  last_reset_at  TIMESTAMPTZ DEFAULT NOW(),
  created_at     TIMESTAMPTZ DEFAULT NOW(),
  updated_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ─── RLS policies ────────────────────────────────────────────────────

ALTER TABLE plans           ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events    ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_events  ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_grants   ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  -- plans: public read
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'plans' AND policyname = 'plans_public_read') THEN
    CREATE POLICY "plans_public_read" ON plans FOR SELECT USING (is_public = TRUE);
  END IF;

  -- usage_events: users see own events
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'usage_events' AND policyname = 'usage_events_own') THEN
    CREATE POLICY "usage_events_own" ON usage_events
      FOR SELECT USING (auth.uid()::TEXT = user_id);
  END IF;

  -- payment_events: service role only (no user policy — admin only)

  -- credit_grants: users see own grants
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'credit_grants' AND policyname = 'credit_grants_own') THEN
    CREATE POLICY "credit_grants_own" ON credit_grants
      FOR SELECT USING (auth.uid()::TEXT = user_id);
  END IF;
END $$;
