-- ============================================
-- Migration 003: Reliability Layer
-- VidGrab — Job stage tracking + auto-recovery
-- ============================================
-- Safe to run multiple times (IF NOT EXISTS / OR REPLACE)

-- ── Reliability columns on download_jobs ─────────────────────────────

ALTER TABLE download_jobs
  ADD COLUMN IF NOT EXISTS job_stage          TEXT         DEFAULT 'queued',
  ADD COLUMN IF NOT EXISTS link_expires_at    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS temp_artifact_path TEXT,
  ADD COLUMN IF NOT EXISTS recovery_attempts  INTEGER      DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_recovery_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMPTZ  DEFAULT NOW();

-- ── Auto-update trigger (keeps updated_at current on every UPDATE) ────

CREATE OR REPLACE FUNCTION update_download_job_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS download_jobs_updated_at ON download_jobs;
CREATE TRIGGER download_jobs_updated_at
  BEFORE UPDATE ON download_jobs
  FOR EACH ROW EXECUTE FUNCTION update_download_job_timestamp();

-- ── Indexes ───────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_download_jobs_job_stage
  ON download_jobs (job_stage);

CREATE INDEX IF NOT EXISTS idx_download_jobs_updated_at
  ON download_jobs (updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_download_jobs_link_expires
  ON download_jobs (link_expires_at)
  WHERE link_expires_at IS NOT NULL;

-- Compound: fast lookup for stale-job scanner
CREATE INDEX IF NOT EXISTS idx_download_jobs_status_updated
  ON download_jobs (status, updated_at)
  WHERE status IN ('processing', 'pending');

-- ── Backfill job_stage for existing rows ─────────────────────────────

UPDATE download_jobs SET job_stage = 'completed' WHERE status = 'success'    AND job_stage = 'queued';
UPDATE download_jobs SET job_stage = 'failed'    WHERE status = 'failed'     AND job_stage = 'queued';
-- pending rows keep job_stage = 'queued' (already the default)
