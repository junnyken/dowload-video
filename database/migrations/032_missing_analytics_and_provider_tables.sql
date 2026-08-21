-- ============================================================
-- Migration 032: create four tables the code has always queried
--                but that were never created.
--
-- Each one failed silently, which is why none of this showed up as an error:
--
--   analytics_events   — flush_event_buffer (every 60s) POPS the Redis buffer
--                        with LTRIM and only then inserts. The insert raised
--                        "table not found" every single run, so every product
--                        event ever collected was read out of Redis and thrown
--                        away. The task's own except printed and moved on.
--   analytics_daily    — the nightly rollup target; upsert raised the same way.
--   provider_status    — ScraperAPI credit history. admin /stats and
--                        proxy_manager both wrap it in try/except, so the
--                        "Provider credits" panel is permanently blank and the
--                        low-credit Telegram alert never has a baseline.
--   experiment_config  — GET /events/experiments returns {} on any error, so
--                        every frontend feature flag silently reads "off".
--
-- Idempotent. Run in Supabase Dashboard → SQL Editor (service_role).
-- ============================================================

-- ── 1. analytics_events ──────────────────────────────────────────────
-- Columns mirror exactly what app/tasks/analytics_tasks.py:flush_event_buffer
-- inserts, plus the id that its retention delete selects on.
CREATE TABLE IF NOT EXISTS public.analytics_events (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_name          TEXT        NOT NULL,
    user_id             TEXT,                      -- profiles.id is TEXT; no FK, events outlive accounts
    anonymous_id        TEXT,                      -- set instead of user_id for logged-out traffic
    source              TEXT        NOT NULL DEFAULT 'web',
    properties          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    experiment_variants JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Every read is a time window (daily rollup, admin 'events by source'), and
-- the rollup groups by source.
CREATE INDEX IF NOT EXISTS analytics_events_created_at_idx
    ON public.analytics_events (created_at DESC);
CREATE INDEX IF NOT EXISTS analytics_events_source_created_idx
    ON public.analytics_events (source, created_at DESC);

-- ── 2. analytics_daily ───────────────────────────────────────────────
-- flush_analytics_daily upserts on_conflict="date,metric,dimensions", so that
-- triple needs a real unique constraint or the upsert degrades to an insert
-- and the table grows a duplicate row per nightly run.
CREATE TABLE IF NOT EXISTS public.analytics_daily (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    date       DATE        NOT NULL,
    metric     TEXT        NOT NULL,
    dimensions JSONB       NOT NULL DEFAULT '{}'::jsonb,
    value      BIGINT      NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'analytics_daily_date_metric_dims_key'
    ) THEN
        ALTER TABLE public.analytics_daily
            ADD CONSTRAINT analytics_daily_date_metric_dims_key
            UNIQUE (date, metric, dimensions);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS analytics_daily_date_idx
    ON public.analytics_daily (date DESC);

-- ── 3. provider_status ───────────────────────────────────────────────
-- admin.py upserts on provider_name, so it must be unique for the upsert to
-- update rather than duplicate.
CREATE TABLE IF NOT EXISTS public.provider_status (
    provider_name      TEXT        PRIMARY KEY,
    remaining_credits  NUMERIC     NOT NULL DEFAULT 0,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 4. experiment_config ─────────────────────────────────────────────
-- GET /api/v1/events/experiments does .select("key,value").eq("active", true)
CREATE TABLE IF NOT EXISTS public.experiment_config (
    key        TEXT        PRIMARY KEY,
    value      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    active     BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 5. webhook_endpoints.label ───────────────────────────────────────
-- Not a missing table but the same class of failure: the partner API both
-- inserts and selects a `label` column that migration 012 never created, so
-- POST and GET /api/v1/partner/webhooks each returned 500 — the whole partner
-- webhook feature was unusable. The column is part of the published API
-- contract (WebhookRegisterRequest.label), so add it rather than drop it.
ALTER TABLE public.webhook_endpoints
    ADD COLUMN IF NOT EXISTS label TEXT;

-- ── 6. RLS ───────────────────────────────────────────────────────────
-- Backend-only tables. service_role bypasses RLS, so enabling it with no
-- permissive policy is what keeps them unreadable from the browser's anon key
-- — analytics_events in particular holds user ids and event properties.
ALTER TABLE public.analytics_events   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analytics_daily    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_status    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.experiment_config  ENABLE ROW LEVEL SECURITY;

-- experiment_config is the one exception: feature flags are non-secret and the
-- endpoint that serves them is public, so allow anon SELECT specifically.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'experiment_config' AND policyname = 'Public read active experiments'
    ) THEN
        CREATE POLICY "Public read active experiments"
            ON public.experiment_config FOR SELECT
            USING (active = TRUE);
    END IF;
END $$;

DO $$
BEGIN
    RAISE NOTICE 'Migration 032 applied: analytics_events, analytics_daily, provider_status, experiment_config.';
END $$;
