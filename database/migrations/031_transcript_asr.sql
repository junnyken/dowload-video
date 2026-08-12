-- Phase T5 — Transcript ASR (auto-generate subtitles from a downloaded
-- video's audio via OpenAI Whisper). Mirrors transcript_translation_jobs'
-- structure/conventions (024-030) closely — same identity model, same
-- expiry sweep pattern, same atomic-quota-reservation approach — but keyed
-- by audio MINUTES instead of cue count, since Whisper bills/limits per
-- duration, not per line of text.
--
-- Safe to run multiple times (all CREATE ... IF NOT EXISTS / OR REPLACE).

-- ─────────────────────────────────────────────
-- 1. transcript_asr_jobs
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transcript_asr_jobs (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              TEXT,                          -- real auth user id OR anon X-Session-ID, no FK (mirrors transcript_translation_jobs)
    video_local_path     TEXT        NOT NULL,           -- download_jobs.local_file_path this job was created from
    video_title          TEXT,                           -- display label, copied from the source download_jobs row at creation time
    duration_sec         NUMERIC,                        -- probed audio duration, set once known
    detected_language     TEXT,                          -- Whisper's detected source language
    status               TEXT        NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued', 'extracting_audio', 'transcribing', 'done', 'failed')),
    progress_pct         INT         NOT NULL DEFAULT 0,
    error_message        TEXT,
    result_path          TEXT,                           -- generated .srt
    celery_task_id       TEXT,
    timeout_retry_count  INT         NOT NULL DEFAULT 0,  -- bounded self-requeue on transient failure, same pattern as T3-C
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at           TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days')
);

CREATE INDEX IF NOT EXISTS transcript_asr_jobs_user_created_idx
    ON transcript_asr_jobs(user_id, created_at);

ALTER TABLE transcript_asr_jobs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'transcript_asr_jobs' AND policyname = 'service_role_bypass'
    ) THEN
        CREATE POLICY service_role_bypass ON transcript_asr_jobs
            USING (auth.role() = 'service_role');
    END IF;
END;
$$;

CREATE OR REPLACE TRIGGER transcript_asr_jobs_set_updated_at
    BEFORE UPDATE ON transcript_asr_jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();  -- reuses the function created in 024

-- ─────────────────────────────────────────────
-- 2. transcript_asr_usage — daily per-identity minutes counter
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transcript_asr_usage (
    user_id          TEXT        NOT NULL,
    usage_date       DATE        NOT NULL DEFAULT CURRENT_DATE,
    minutes_used     NUMERIC     NOT NULL DEFAULT 0,
    jobs_count       INT         NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, usage_date)
);

ALTER TABLE transcript_asr_usage ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'transcript_asr_usage' AND policyname = 'service_role_bypass'
    ) THEN
        CREATE POLICY service_role_bypass ON transcript_asr_usage
            USING (auth.role() = 'service_role');
    END IF;
END;
$$;

-- Atomic reserve-if-it-fits — same SELECT ... FOR UPDATE pattern as
-- reserve_transcript_translation_usage (026), just keyed by minutes.
CREATE OR REPLACE FUNCTION reserve_transcript_asr_usage(
    p_user_id TEXT,
    p_minutes NUMERIC,
    p_limit   NUMERIC
)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE
    v_current NUMERIC;
BEGIN
    INSERT INTO transcript_asr_usage (user_id, usage_date, minutes_used, jobs_count)
    VALUES (p_user_id, CURRENT_DATE, 0, 0)
    ON CONFLICT (user_id, usage_date) DO NOTHING;

    SELECT minutes_used INTO v_current
    FROM transcript_asr_usage
    WHERE user_id = p_user_id AND usage_date = CURRENT_DATE
    FOR UPDATE;

    IF v_current + p_minutes > p_limit THEN
        RETURN FALSE;
    END IF;

    UPDATE transcript_asr_usage
    SET minutes_used = minutes_used + p_minutes,
        jobs_count    = jobs_count + 1,
        updated_at    = NOW()
    WHERE user_id = p_user_id AND usage_date = CURRENT_DATE;

    RETURN TRUE;
END;
$$;
