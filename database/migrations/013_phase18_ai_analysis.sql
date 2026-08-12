-- Phase 18 AI Media Analysis
-- Smart trim/clip/GIF suggestion engine backed by FFmpeg heuristics.
-- Tables: analysis_jobs, analysis_results, analysis_usage
-- Safe to run multiple times (all CREATE ... IF NOT EXISTS).

-- ─────────────────────────────────────────────
-- 0. updated_at trigger function (idempotent — may exist from 012)
-- ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- ─────────────────────────────────────────────
-- 1. analysis_jobs
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analysis_jobs (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              TEXT,
    tenant_id            UUID        REFERENCES tenants(id) ON DELETE SET NULL,
    media_url            TEXT,
    media_path           TEXT,
    media_fingerprint    TEXT,
    duration_seconds     FLOAT,
    analyses_requested   TEXT[]      NOT NULL DEFAULT '{}',
    status               TEXT        NOT NULL DEFAULT 'queued'
                             CHECK (status IN ('queued', 'processing', 'done', 'failed', 'cached')),
    celery_task_id       TEXT,
    analyzer_version     TEXT        NOT NULL DEFAULT '1.0',
    error_message        TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at           TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days')
);

CREATE INDEX IF NOT EXISTS analysis_jobs_fingerprint_idx
    ON analysis_jobs(media_fingerprint);

CREATE INDEX IF NOT EXISTS analysis_jobs_user_status_idx
    ON analysis_jobs(user_id, status);

CREATE INDEX IF NOT EXISTS analysis_jobs_tenant_created_idx
    ON analysis_jobs(tenant_id, created_at);

ALTER TABLE analysis_jobs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'analysis_jobs' AND policyname = 'service_role_bypass'
    ) THEN
        CREATE POLICY service_role_bypass ON analysis_jobs
            USING (auth.role() = 'service_role');
    END IF;
END;
$$;

CREATE OR REPLACE TRIGGER analysis_jobs_set_updated_at
    BEFORE UPDATE ON analysis_jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─────────────────────────────────────────────
-- 2. analysis_results
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analysis_results (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id                  UUID        UNIQUE NOT NULL
                                REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    trim_suggestions        JSONB       NOT NULL DEFAULT '[]',
    clip_suggestions        JSONB       NOT NULL DEFAULT '[]',
    gif_suggestions         JSONB       NOT NULL DEFAULT '[]',
    metadata_suggestions    JSONB       NOT NULL DEFAULT '{}',
    summary_suggestions     JSONB       NOT NULL DEFAULT '{}',
    signals_used            TEXT[]      NOT NULL DEFAULT '{}',
    warnings                TEXT[]      NOT NULL DEFAULT '{}',
    fallback_used           BOOLEAN     NOT NULL DEFAULT false,
    processing_time_ms      INT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS analysis_results_job_id_idx
    ON analysis_results(job_id);

ALTER TABLE analysis_results ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'analysis_results' AND policyname = 'service_role_bypass'
    ) THEN
        CREATE POLICY service_role_bypass ON analysis_results
            USING (auth.role() = 'service_role');
    END IF;
END;
$$;

CREATE OR REPLACE TRIGGER analysis_results_set_updated_at
    BEFORE UPDATE ON analysis_results
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─────────────────────────────────────────────
-- 3. analysis_usage  (metering per user/tenant per day)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analysis_usage (
    id                          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     TEXT    NOT NULL,
    tenant_id                   UUID,
    date                        DATE    NOT NULL DEFAULT CURRENT_DATE,
    analyses_count              INT     NOT NULL DEFAULT 0,
    clip_suggestions_count      INT     NOT NULL DEFAULT 0,
    ai_apply_jobs_count         INT     NOT NULL DEFAULT 0,
    media_minutes_analyzed      FLOAT   NOT NULL DEFAULT 0,
    UNIQUE (user_id, date)
);

CREATE INDEX IF NOT EXISTS analysis_usage_user_date_idx
    ON analysis_usage(user_id, date);

CREATE INDEX IF NOT EXISTS analysis_usage_tenant_date_idx
    ON analysis_usage(tenant_id, date);

ALTER TABLE analysis_usage ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'analysis_usage' AND policyname = 'service_role_bypass'
    ) THEN
        CREATE POLICY service_role_bypass ON analysis_usage
            USING (auth.role() = 'service_role');
    END IF;
END;
$$;
