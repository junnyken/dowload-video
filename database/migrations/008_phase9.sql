-- ============================================
-- Migration 008: Phase 9 — Storage Governance & Stabilization
-- VidGrab
-- ============================================
-- All changes are additive (ALTER TABLE ADD COLUMN IF NOT EXISTS).
-- No existing data is removed. Old rows get sensible defaults.
-- Safe to re-run.

-- ── Storage governance fields on download_jobs ────────────────────────

ALTER TABLE download_jobs
  ADD COLUMN IF NOT EXISTS file_retention_status  TEXT         DEFAULT 'temporary'
    CHECK (file_retention_status IN ('temporary', 'pinned', 'expired_metadata_only')),
  ADD COLUMN IF NOT EXISTS file_expires_at        TIMESTAMPTZ,   -- when binary should be deleted
  ADD COLUMN IF NOT EXISTS pinned_at              TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS pin_expires_at         TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS storage_bytes          BIGINT       DEFAULT 0;

-- Backfill: old successful local-file jobs → file_expires_at = created_at + 24h
-- (they are likely already gone from disk, but this gives them an explicit state)
UPDATE download_jobs
SET    file_expires_at = created_at + INTERVAL '24 hours'
WHERE  file_expires_at IS NULL
  AND  status = 'success'
  AND  local_file_path IS NOT NULL;

-- Backfill: successful old jobs with no local path → already expired_metadata_only
UPDATE download_jobs
SET    file_retention_status = 'expired_metadata_only'
WHERE  status = 'success'
  AND  local_file_path IS NULL
  AND  direct_mp4_url IS NOT NULL
  AND  file_retention_status = 'temporary';

CREATE INDEX IF NOT EXISTS idx_download_jobs_retention
  ON download_jobs (file_retention_status)
  WHERE file_retention_status IN ('temporary', 'pinned');

CREATE INDEX IF NOT EXISTS idx_download_jobs_file_expires
  ON download_jobs (file_expires_at)
  WHERE file_expires_at IS NOT NULL;

-- ── Storage governance on archive_items ──────────────────────────────

ALTER TABLE archive_items
  ADD COLUMN IF NOT EXISTS file_retention_status  TEXT         DEFAULT 'temporary'
    CHECK (file_retention_status IN ('temporary', 'pinned', 'expired_metadata_only')),
  ADD COLUMN IF NOT EXISTS file_job_id            UUID         REFERENCES download_jobs(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS pin_expires_at         TIMESTAMPTZ;

-- Backfill: items linked to a job inherit that job's retention status lazily
-- (done at runtime, not via mass UPDATE to avoid long lock)

CREATE INDEX IF NOT EXISTS idx_archive_items_retention
  ON archive_items (user_id, file_retention_status);

CREATE INDEX IF NOT EXISTS idx_archive_items_file_job
  ON archive_items (file_job_id) WHERE file_job_id IS NOT NULL;

-- ── file_assets — explicit binary asset tracking ──────────────────────
-- New architecture: each binary output gets its own row.
-- Old records continue using download_jobs.local_file_path directly.

CREATE TABLE IF NOT EXISTS file_assets (
  id                UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  archive_item_id   UUID        REFERENCES archive_items(id) ON DELETE CASCADE,
  job_id            UUID        REFERENCES download_jobs(id) ON DELETE CASCADE,
  file_path         TEXT,
  file_url          TEXT,
  mime_type         TEXT,
  file_size_bytes   BIGINT      DEFAULT 0,
  retention_status  TEXT        NOT NULL DEFAULT 'temporary'
    CHECK (retention_status IN ('temporary', 'pinned', 'expired_metadata_only')),
  expires_at        TIMESTAMPTZ,
  pinned_at         TIMESTAMPTZ,
  pin_expires_at    TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS file_assets_archive_item  ON file_assets (archive_item_id);
CREATE INDEX IF NOT EXISTS file_assets_job_id        ON file_assets (job_id);
CREATE INDEX IF NOT EXISTS file_assets_expires       ON file_assets (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS file_assets_retention     ON file_assets (retention_status);

ALTER TABLE file_assets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "file_assets_service" ON file_assets;
CREATE POLICY "file_assets_service" ON file_assets FOR ALL USING (true) WITH CHECK (true);

-- ── Scheduled download hardening ──────────────────────────────────────

ALTER TABLE scheduled_jobs
  ADD COLUMN IF NOT EXISTS duplicate_suppression  TEXT         DEFAULT 'off'
    CHECK (duplicate_suppression IN ('off', 'url_24h', 'canonical_id')),
  ADD COLUMN IF NOT EXISTS min_interval_hours     INT          DEFAULT 6,
  ADD COLUMN IF NOT EXISTS last_run_error         TEXT,
  ADD COLUMN IF NOT EXISTS run_count              INT          DEFAULT 0;

-- ── Performance indexes ───────────────────────────────────────────────

-- Archive search performance
CREATE INDEX IF NOT EXISTS archive_items_creator ON archive_items (creator_handle);

-- Scheduled jobs active scan
CREATE INDEX IF NOT EXISTS scheduled_jobs_active_user
  ON scheduled_jobs (user_id, is_active);

-- Download jobs user-scoped performance
CREATE INDEX IF NOT EXISTS idx_download_jobs_user_status
  ON download_jobs (user_id, status, created_at DESC)
  WHERE user_id IS NOT NULL;

-- ── Pin limit tracking view ───────────────────────────────────────────
CREATE OR REPLACE VIEW pinned_files_by_user AS
SELECT
  dj.user_id,
  COUNT(*)                    AS pinned_count,
  COALESCE(SUM(dj.storage_bytes), 0) AS pinned_bytes
FROM download_jobs dj
WHERE dj.file_retention_status = 'pinned'
  AND dj.user_id IS NOT NULL
GROUP BY dj.user_id;
