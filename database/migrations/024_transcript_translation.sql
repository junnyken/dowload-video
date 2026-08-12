-- Phase — Transcript Translation
-- Customers upload .srt/.vtt transcript files (batch), the system translates
-- cue text via LLM (meaning-preserving, not literal) into a target language,
-- and produces a downloadable translated .srt/.vtt with original timing kept.
-- Table: transcript_translation_jobs
-- Safe to run multiple times (all CREATE ... IF NOT EXISTS).

-- ─────────────────────────────────────────────
-- 0. updated_at trigger function (idempotent — may already exist from 013)
-- ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- ─────────────────────────────────────────────
-- 1. transcript_translation_jobs
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transcript_translation_jobs (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 TEXT,
    source_filename         TEXT        NOT NULL,
    source_format           TEXT        NOT NULL CHECK (source_format IN ('srt', 'vtt')),
    source_lang             TEXT,
    target_lang             TEXT        NOT NULL,
    cue_count               INT,
    translated_cue_count    INT         NOT NULL DEFAULT 0,
    status                  TEXT        NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued', 'detecting', 'translating', 'done', 'failed')),
    progress_pct            INT         NOT NULL DEFAULT 0,
    error_message           TEXT,
    input_path              TEXT        NOT NULL,
    result_path             TEXT,
    celery_task_id          TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at              TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days')
);

CREATE INDEX IF NOT EXISTS transcript_translation_jobs_user_created_idx
    ON transcript_translation_jobs(user_id, created_at);

ALTER TABLE transcript_translation_jobs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'transcript_translation_jobs' AND policyname = 'service_role_bypass'
    ) THEN
        CREATE POLICY service_role_bypass ON transcript_translation_jobs
            USING (auth.role() = 'service_role');
    END IF;
END;
$$;

CREATE OR REPLACE TRIGGER transcript_translation_jobs_set_updated_at
    BEFORE UPDATE ON transcript_translation_jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
