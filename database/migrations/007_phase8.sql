-- ============================================
-- Migration 007: Phase 8 — Personal Video Archive + Scheduled Download
-- VidGrab
-- ============================================

-- ── archive_collections ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS archive_collections (
  id          UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id     TEXT        NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  name        TEXT        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
  description TEXT,
  color       TEXT,
  icon        TEXT,
  item_count  INT         NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS archive_collections_user_id ON archive_collections (user_id);

ALTER TABLE archive_collections ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "archive_collections_own" ON archive_collections;
CREATE POLICY "archive_collections_own" ON archive_collections
  FOR ALL
  USING (user_id = auth.uid()::TEXT)
  WITH CHECK (user_id = auth.uid()::TEXT);

-- ── archive_items ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS archive_items (
  id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id             TEXT        REFERENCES profiles(id) ON DELETE CASCADE,
  job_id              UUID        REFERENCES download_jobs(id) ON DELETE SET NULL,
  original_url        TEXT        NOT NULL,
  platform            TEXT        NOT NULL DEFAULT 'other',
  title               TEXT        NOT NULL,
  creator_handle      TEXT,
  creator_name        TEXT,
  duration_seconds    INT,
  upload_date         DATE,
  thumbnail_url       TEXT,
  language_detected   TEXT,
  hashtags            TEXT[]      NOT NULL DEFAULT '{}',
  description_snippet TEXT,
  view_count          BIGINT,
  user_notes          TEXT,
  is_starred          BOOLEAN     NOT NULL DEFAULT false,
  is_downloaded       BOOLEAN     NOT NULL DEFAULT true,
  collection_ids      UUID[]      NOT NULL DEFAULT '{}',
  tags_user           TEXT[]      NOT NULL DEFAULT '{}',
  archived_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_accessed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS archive_items_user_id     ON archive_items (user_id);
CREATE INDEX IF NOT EXISTS archive_items_platform    ON archive_items (platform);
CREATE INDEX IF NOT EXISTS archive_items_starred     ON archive_items (user_id, is_starred) WHERE is_starred = true;
CREATE INDEX IF NOT EXISTS archive_items_archived    ON archive_items (user_id, archived_at DESC);
CREATE INDEX IF NOT EXISTS archive_items_job_id      ON archive_items (job_id) WHERE job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS archive_items_hashtags    ON archive_items USING GIN (hashtags);
CREATE INDEX IF NOT EXISTS archive_items_tags_user   ON archive_items USING GIN (tags_user);
CREATE INDEX IF NOT EXISTS archive_items_collections ON archive_items USING GIN (collection_ids);

ALTER TABLE archive_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "archive_items_own" ON archive_items;
CREATE POLICY "archive_items_own" ON archive_items
  FOR ALL
  USING (user_id = auth.uid()::TEXT)
  WITH CHECK (user_id = auth.uid()::TEXT);

-- ── scheduled_jobs ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scheduled_jobs (
  id                 UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id            TEXT        REFERENCES profiles(id) ON DELETE CASCADE,
  job_type           TEXT        NOT NULL CHECK (job_type IN ('single', 'channel', 'keyword')),
  input_payload      JSONB       NOT NULL,
  schedule_type      TEXT        NOT NULL CHECK (schedule_type IN ('once', 'daily', 'weekly')),
  run_at             TIME,
  run_on_weekday     INT         CHECK (run_on_weekday BETWEEN 0 AND 6),
  next_run_at        TIMESTAMPTZ NOT NULL,
  last_run_at        TIMESTAMPTZ,
  last_run_status    TEXT        CHECK (last_run_status IN ('success', 'failed', 'running')),
  is_active          BOOLEAN     NOT NULL DEFAULT true,
  auto_collection_id UUID        REFERENCES archive_collections(id) ON DELETE SET NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS scheduled_jobs_user_id  ON scheduled_jobs (user_id);
CREATE INDEX IF NOT EXISTS scheduled_jobs_next_run ON scheduled_jobs (next_run_at) WHERE is_active = true;

ALTER TABLE scheduled_jobs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "scheduled_jobs_own" ON scheduled_jobs;
CREATE POLICY "scheduled_jobs_own" ON scheduled_jobs
  FOR ALL
  USING (user_id = auth.uid()::TEXT)
  WITH CHECK (user_id = auth.uid()::TEXT);
