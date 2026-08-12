-- ============================================
-- Migration 005: Phase 5 Features
-- VidGrab — Playlist Manager, Webhook Callbacks
-- ============================================
-- Safe to run multiple times (IF NOT EXISTS / OR REPLACE)

-- ── saved_playlists ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS saved_playlists (
  id               UUID         DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id          TEXT         NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  platform         TEXT         NOT NULL,
  url              TEXT         NOT NULL,
  title            TEXT,
  item_count       INTEGER      NOT NULL DEFAULT 0,
  last_refreshed_at TIMESTAMPTZ,
  created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS saved_playlists_user_url ON saved_playlists (user_id, url);
CREATE INDEX IF NOT EXISTS saved_playlists_user_id ON saved_playlists (user_id);

-- RLS
ALTER TABLE saved_playlists ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "saved_playlists_self" ON saved_playlists;
CREATE POLICY "saved_playlists_self" ON saved_playlists
  FOR ALL USING (user_id = auth.uid()::TEXT)
  WITH CHECK (user_id = auth.uid()::TEXT);

-- ── user_webhooks ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_webhooks (
  user_id      TEXT        PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
  webhook_url  TEXT        NOT NULL,
  secret_hash  TEXT        NOT NULL,
  is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RLS
ALTER TABLE user_webhooks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "user_webhooks_self" ON user_webhooks;
CREATE POLICY "user_webhooks_self" ON user_webhooks
  FOR ALL USING (user_id = auth.uid()::TEXT)
  WITH CHECK (user_id = auth.uid()::TEXT);
