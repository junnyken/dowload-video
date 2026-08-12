-- ============================================
-- Migration 006: Phase 6 — Smart Metadata & Clip Notes
-- VidGrab
-- ============================================
-- Uses DROP IF EXISTS to ensure clean state (safe if no data yet).

-- ── Drop stale objects from any failed partial run ────────────────────
DROP VIEW  IF EXISTS download_jobs_with_notes CASCADE;
DROP TABLE IF EXISTS clip_notes   CASCADE;
DROP TABLE IF EXISTS job_metadata CASCADE;

-- ── job_metadata ──────────────────────────────────────────────────────

CREATE TABLE job_metadata (
  id                  UUID         DEFAULT gen_random_uuid() PRIMARY KEY,
  job_id              UUID         NOT NULL UNIQUE REFERENCES download_jobs(id) ON DELETE CASCADE,
  creator_handle      TEXT,
  creator_name        TEXT,
  duration_seconds    INT,
  view_count          BIGINT,
  like_count          BIGINT,
  upload_date         DATE,
  language_detected   TEXT,
  hashtags            TEXT[],
  categories          TEXT[],
  description_snippet TEXT,
  thumbnail_url       TEXT,
  created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX job_metadata_job_id  ON job_metadata (job_id);
CREATE INDEX job_metadata_creator ON job_metadata (creator_name);

-- GIN index for hashtag array search (containment / @> queries)
CREATE INDEX job_metadata_hashtags_gin ON job_metadata USING GIN (hashtags);

-- RLS
ALTER TABLE job_metadata ENABLE ROW LEVEL SECURITY;

CREATE POLICY "job_metadata_read_own" ON job_metadata
  FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM download_jobs dj
      WHERE dj.id::text = job_metadata.job_id::text
        AND dj.user_id = auth.uid()  -- download_jobs.user_id is UUID (migration 002), not TEXT like most other tables
    )
  );

-- ── clip_notes ────────────────────────────────────────────────────────

CREATE TABLE clip_notes (
  id                  UUID         DEFAULT gen_random_uuid() PRIMARY KEY,
  job_id              UUID         NOT NULL REFERENCES download_jobs(id) ON DELETE CASCADE,
  user_id             TEXT,
  session_id          TEXT,
  timestamp_seconds   INT         CHECK (timestamp_seconds >= 0),
  note_text           TEXT        NOT NULL CHECK (char_length(note_text) <= 500),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ
);

CREATE INDEX clip_notes_job_id  ON clip_notes (job_id);
CREATE INDEX clip_notes_user_id ON clip_notes (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX clip_notes_session ON clip_notes (session_id) WHERE session_id IS NOT NULL;

-- RLS
ALTER TABLE clip_notes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "clip_notes_logged_in" ON clip_notes
  FOR ALL
  USING (user_id = auth.uid()::TEXT)
  WITH CHECK (user_id = auth.uid()::TEXT);

-- ── Cleanup function for anonymous notes ──────────────────────────────
CREATE OR REPLACE FUNCTION cleanup_old_anonymous_notes()
RETURNS void AS $$
BEGIN
  DELETE FROM clip_notes
  WHERE user_id IS NULL
    AND created_at < NOW() - INTERVAL '7 days';
END;
$$ LANGUAGE plpgsql;

-- ── View: download_jobs with note count ───────────────────────────────
-- Both job_id (clip_notes) and id (download_jobs) are UUID — join directly.
CREATE OR REPLACE VIEW download_jobs_with_notes AS
SELECT
  dj.*,
  COALESCE(n.note_count, 0) AS note_count
FROM download_jobs dj
LEFT JOIN (
  SELECT job_id, COUNT(*) AS note_count
  FROM clip_notes
  GROUP BY job_id
) n ON n.job_id = dj.id;
