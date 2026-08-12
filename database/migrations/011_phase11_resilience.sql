-- Phase 11 SRE — Job Resilience & Observability
-- Adds retry tracking, flexible file expiry, and queue visibility columns.
-- Safe to run on existing tables (ADD COLUMN IF NOT EXISTS).

ALTER TABLE download_jobs
    ADD COLUMN IF NOT EXISTS retry_count   INTEGER      DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_error    TEXT,
    ADD COLUMN IF NOT EXISTS error_type    TEXT,         -- 'retryable' | 'permanent' | null
    ADD COLUMN IF NOT EXISTS completed_at  TIMESTAMPTZ,  -- set at success OR final fail
    ADD COLUMN IF NOT EXISTS job_type      TEXT DEFAULT 'single'; -- 'single'|'bulk'|'artist_all'

-- Fast queue-position queries: how many pending/processing jobs before mine?
CREATE INDEX IF NOT EXISTS download_jobs_status_created_idx
    ON download_jobs(status, created_at)
    WHERE status IN ('pending', 'processing');

-- Fast stale-job scans in recovery.py
CREATE INDEX IF NOT EXISTS download_jobs_status_updated_idx
    ON download_jobs(status, updated_at)
    WHERE status IN ('pending', 'processing');

-- Fast expiry queries in periodic_cleanup
CREATE INDEX IF NOT EXISTS download_jobs_expiry_idx
    ON download_jobs(file_expires_at)
    WHERE file_retention_status = 'temporary' AND file_expires_at IS NOT NULL;
