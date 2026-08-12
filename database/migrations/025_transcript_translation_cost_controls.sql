-- Phase — Transcript Translation Cost Controls
-- Two levers to bound LLM spend as usage scales:
--   1. transcript_translation_cache — dedup identical (text, target_lang) pairs
--      across ALL jobs/users (e.g. repeated "[Music]" filler lines within a
--      file, or the same popular video's subtitles uploaded by many users)
--      so identical content is never re-translated.
--   2. transcript_translation_usage — per-identity daily cue counter, enforced
--      by the API before a job is queued, mirroring this app's existing
--      FREE_DAILY_LIMIT-style quota philosophy.
-- Safe to run multiple times (all CREATE ... IF NOT EXISTS).

-- ─────────────────────────────────────────────
-- 1. transcript_translation_cache
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transcript_translation_cache (
    content_hash     TEXT        PRIMARY KEY,   -- sha256(normalized_text + "\x1f" + target_lang)
    target_lang      TEXT        NOT NULL,
    translated_text  TEXT        NOT NULL,
    hit_count        INT         NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_hit_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS transcript_translation_cache_target_lang_idx
    ON transcript_translation_cache(target_lang);

ALTER TABLE transcript_translation_cache ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'transcript_translation_cache' AND policyname = 'service_role_bypass'
    ) THEN
        CREATE POLICY service_role_bypass ON transcript_translation_cache
            USING (auth.role() = 'service_role');
    END IF;
END;
$$;

-- Atomically record a cache hit (bump hit_count/last_hit_at) — best-effort,
-- never blocks translation if it fails.
CREATE OR REPLACE FUNCTION bump_transcript_cache_hit(p_hash TEXT)
RETURNS VOID LANGUAGE SQL AS $$
    UPDATE transcript_translation_cache
    SET hit_count = hit_count + 1, last_hit_at = NOW()
    WHERE content_hash = p_hash;
$$;

-- ─────────────────────────────────────────────
-- 2. transcript_translation_usage — daily per-identity cue counter
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transcript_translation_usage (
    user_id          TEXT        NOT NULL,   -- real auth user id OR anon X-Session-ID, plain text, no FK (mirrors transcript_translation_jobs.user_id)
    usage_date       DATE        NOT NULL DEFAULT CURRENT_DATE,
    cues_translated  INT         NOT NULL DEFAULT 0,
    jobs_count       INT         NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, usage_date)
);

ALTER TABLE transcript_translation_usage ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'transcript_translation_usage' AND policyname = 'service_role_bypass'
    ) THEN
        CREATE POLICY service_role_bypass ON transcript_translation_usage
            USING (auth.role() = 'service_role');
    END IF;
END;
$$;

-- Atomically reserve quota for a newly-queued job (called at upload time,
-- before the Celery task runs, so parallel uploads can't race past the cap).
CREATE OR REPLACE FUNCTION increment_transcript_translation_usage(
    p_user_id TEXT,
    p_cues    INT
)
RETURNS VOID LANGUAGE SQL AS $$
    INSERT INTO transcript_translation_usage (user_id, usage_date, cues_translated, jobs_count, updated_at)
    VALUES (p_user_id, CURRENT_DATE, p_cues, 1, NOW())
    ON CONFLICT (user_id, usage_date) DO UPDATE
    SET cues_translated = transcript_translation_usage.cues_translated + EXCLUDED.cues_translated,
        jobs_count       = transcript_translation_usage.jobs_count + 1,
        updated_at       = NOW();
$$;
