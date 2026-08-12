-- ============================================================
-- Migration 016 — Phase 21: Convenience & Workflow
-- ============================================================
-- Adds:
--   user_presets        — saved download presets per user
--   user_platform_prefs — per-platform default overrides
-- ============================================================

-- ─── user_presets ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_presets (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      TEXT NOT NULL,
  name         TEXT NOT NULL,
  platform     TEXT,                     -- 'tiktok'|'spotify'|null (universal)
  settings     JSONB NOT NULL DEFAULT '{}',
  -- settings keys: quality, format, no_watermark, download_subs, cloud_save,
  --                sub_to_cloud, use_cookies, zip, output (gif), duration
  is_default   BOOLEAN DEFAULT FALSE,    -- default for this platform
  is_system    BOOLEAN DEFAULT FALSE,    -- system presets cannot be deleted
  sort_order   INTEGER DEFAULT 0,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_presets_user
  ON user_presets (user_id, platform, is_default);

-- Only one default preset per user per platform
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_presets_default
  ON user_presets (user_id, platform) WHERE is_default = TRUE AND platform IS NOT NULL;

-- ─── user_platform_prefs (lightweight per-platform learning) ─────────────────

CREATE TABLE IF NOT EXISTS user_platform_prefs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      TEXT NOT NULL,
  platform     TEXT NOT NULL,
  prefs        JSONB NOT NULL DEFAULT '{}',
  -- prefs keys: quality, format, no_watermark, cloud_save, last_preset_id
  last_used_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (user_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_platform_prefs_user
  ON user_platform_prefs (user_id, platform);

-- ─── Extend download_jobs to store applied preset_id for history rerun ────────

ALTER TABLE download_jobs
  ADD COLUMN IF NOT EXISTS preset_id    UUID,
  ADD COLUMN IF NOT EXISTS download_settings JSONB;  -- snapshot of all settings at job time

-- ─── RLS ─────────────────────────────────────────────────────────────────────

ALTER TABLE user_presets ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_platform_prefs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_presets' AND policyname = 'presets_own') THEN
    CREATE POLICY "presets_own" ON user_presets
      FOR ALL USING (auth.uid()::TEXT = user_id);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_platform_prefs' AND policyname = 'platform_prefs_own') THEN
    CREATE POLICY "platform_prefs_own" ON user_platform_prefs
      FOR ALL USING (auth.uid()::TEXT = user_id);
  END IF;
END $$;
