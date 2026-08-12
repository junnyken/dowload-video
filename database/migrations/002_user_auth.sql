-- ============================================
-- Migration 002: User Auth Layer
-- VidGrab — User accounts, preferences, usage
-- ============================================
-- Run this in Supabase SQL Editor (service_role)
-- Safe to run multiple times (IF NOT EXISTS / ON CONFLICT)

-- ── 1. Mở rộng profiles ──────────────────────────────────────────────
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS email        TEXT,
  ADD COLUMN IF NOT EXISTS display_name TEXT,
  ADD COLUMN IF NOT EXISTS avatar_url   TEXT,
  ADD COLUMN IF NOT EXISTS updated_at   TIMESTAMPTZ DEFAULT NOW();

-- ── 2. Thêm user_id + surface vào download_jobs ──────────────────────
ALTER TABLE download_jobs
  ADD COLUMN IF NOT EXISTS user_id          UUID    REFERENCES auth.users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS source_surface   TEXT    DEFAULT 'web',
  ADD COLUMN IF NOT EXISTS selected_quality TEXT;

CREATE INDEX IF NOT EXISTS idx_download_jobs_user_id ON download_jobs (user_id);

-- ── 3. Mở rộng user_usage ────────────────────────────────────────────
ALTER TABLE user_usage
  ADD COLUMN IF NOT EXISTS downloads_this_month INTEGER     DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_monthly_reset   TIMESTAMPTZ DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS bulk_jobs_count      INTEGER     DEFAULT 0,
  ADD COLUMN IF NOT EXISTS plan                 TEXT        DEFAULT 'free';

-- ── 4. Tạo user_preferences ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_preferences (
  user_id           UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  default_quality   TEXT        NOT NULL DEFAULT 'video',
  default_mp3_kbps  INTEGER     NOT NULL DEFAULT 320,
  remove_watermark  BOOLEAN     NOT NULL DEFAULT TRUE,
  download_subs     BOOLEAN     NOT NULL DEFAULT FALSE,
  bulk_count_preset INTEGER     NOT NULL DEFAULT 20,
  theme_mode        TEXT        NOT NULL DEFAULT 'dark',
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "User owns preferences" ON user_preferences;
CREATE POLICY "User owns preferences"
  ON user_preferences FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- Service role bypass (backend dùng service_role key)
DROP POLICY IF EXISTS "Service role full preferences" ON user_preferences;
CREATE POLICY "Service role full preferences"
  ON user_preferences FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- ── 5. Update RLS — profiles ─────────────────────────────────────────
DROP POLICY IF EXISTS "Allow all operations on profiles" ON profiles;

DROP POLICY IF EXISTS "User owns profile" ON profiles;
CREATE POLICY "User owns profile"
  ON profiles FOR ALL
  USING (id = auth.uid()::TEXT)
  WITH CHECK (id = auth.uid()::TEXT);

DROP POLICY IF EXISTS "Service role full profiles" ON profiles;
CREATE POLICY "Service role full profiles"
  ON profiles FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- ── 6. Update RLS — user_usage ───────────────────────────────────────
-- Keep permissive: backend (anon key) increments counters via quotas.py
DROP POLICY IF EXISTS "Allow all operations on user_usage" ON user_usage;

DROP POLICY IF EXISTS "Backend full usage access" ON user_usage;
CREATE POLICY "Backend full usage access"
  ON user_usage FOR ALL
  USING (true) WITH CHECK (true);

-- ── 7. Update RLS — download_jobs ────────────────────────────────────
-- Keep permissive write policy: backend uses anon key for INSERT/UPDATE
-- (Celery workers, routes.py inserts). Only restrict SELECT for users.
DROP POLICY IF EXISTS "Allow all operations for service role" ON download_jobs;

DROP POLICY IF EXISTS "Backend full access" ON download_jobs;
CREATE POLICY "Backend full access"
  ON download_jobs FOR ALL
  USING (true) WITH CHECK (true);

-- Note: user-scoped history filtering is done in application code (user.py /history endpoint),
-- not via RLS SELECT restriction, to keep backward compat with anon key workers.

-- ── 8. Trigger: auto-create profile khi user sign up ─────────────────
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO profiles (id, email, tier, created_at)
  VALUES (NEW.id::TEXT, NEW.email, 'free', NOW())
  ON CONFLICT (id) DO NOTHING;

  INSERT INTO user_preferences (user_id)
  VALUES (NEW.id)
  ON CONFLICT (user_id) DO NOTHING;

  INSERT INTO user_usage (user_id, downloads_today, last_reset_at, plan)
  VALUES (NEW.id::TEXT, 0, NOW(), 'free')
  ON CONFLICT (user_id) DO NOTHING;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION handle_new_user();
