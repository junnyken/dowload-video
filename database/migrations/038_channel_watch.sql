-- 038: Channel Watch ("Theo dõi kênh") — Phase 33A core (task #6257).
--
-- HOW TO RUN (owner): open the Supabase SQL editor for the production project,
-- paste this whole file, run it once. It is ADDITIVE and IDEMPOTENT (CREATE
-- TABLE / INDEX IF NOT EXISTS) — running it twice is harmless, it touches no
-- existing table. NOT applied automatically by any deploy.
--
-- The feature stays OFF until WATCH_ENABLED=true is set on the backend; with
-- the flag off nothing reads or writes these tables. Without these tables and
-- with the flag ON, every watch endpoint fails (500) — so run this BEFORE
-- turning the flag on.
--
-- Access: RLS is ENABLED on every table with NO policy at all, so the anon /
-- authenticated roles can neither read nor write; only the backend's
-- service-role key (which bypasses RLS) touches them. Authorization is done in
-- application code (each query filters on the user id from the verified JWT).
--
-- Never stored here: cookies, tokens, signed media URLs, file paths. Item URLs
-- are canonical public page URLs (https://www.tiktok.com/@user/video/<id>).
--
-- user_id is UUID without a foreign key to auth.users (same reasoning as
-- 034/037: deleting an account must not fail on these rows; the backend
-- cleans them up).

CREATE TABLE IF NOT EXISTS watch_sources (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    platform              TEXT        NOT NULL,
    external_channel_id   TEXT        NOT NULL,
    canonical_url         TEXT        NOT NULL,
    display_name          TEXT,
    -- active | degraded (repeated scan failures, still scanned with backoff)
    -- | idle (no subscriber left) | paused_admin
    status                TEXT        NOT NULL DEFAULT 'active',
    scan_provider         TEXT,
    last_scan_at          TIMESTAMPTZ,          -- NULL = baseline not done yet
    next_scan_at          TIMESTAMPTZ,
    scan_interval_sec     INTEGER,
    activity_score        NUMERIC     DEFAULT 0,
    consecutive_failures  INTEGER     DEFAULT 0,
    last_failure_category TEXT,
    subscriber_count      INTEGER     DEFAULT 0,
    created_at            TIMESTAMPTZ DEFAULT now(),
    UNIQUE (platform, external_channel_id)
);

CREATE TABLE IF NOT EXISTS watch_subscriptions (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID        NOT NULL,
    source_id             UUID        REFERENCES watch_sources(id) ON DELETE CASCADE,
    mode                  TEXT        NOT NULL DEFAULT 'one_tap',   -- notify_only | one_tap
    quality_preset        TEXT,
    baseline_completed_at TIMESTAMPTZ,          -- NULL = gets no deliveries yet
    status                TEXT        NOT NULL DEFAULT 'active',    -- active | paused | removed
    delivery_interval_sec INTEGER,              -- tier entitlement (Free 86400, Pro 21600)
    tier                  TEXT,                 -- tier at creation/refresh: free | pro | ... (global Free cap)
    last_notified_at      TIMESTAMPTZ,          -- last push sent (delivery throttle)
    created_at            TIMESTAMPTZ DEFAULT now(),
    removed_at            TIMESTAMPTZ,
    created_ip_hash       TEXT,                 -- sha256 of the client IP, never the IP
    UNIQUE (user_id, source_id)
);

CREATE TABLE IF NOT EXISTS watch_items_seen (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id             UUID        NOT NULL REFERENCES watch_sources(id) ON DELETE CASCADE,
    external_item_id      TEXT        NOT NULL,
    canonical_item_url    TEXT,
    title                 TEXT,
    published_at          TIMESTAMPTZ,
    discovered_at         TIMESTAMPTZ DEFAULT now(),
    state                 TEXT        NOT NULL DEFAULT 'new',       -- baseline | new
    UNIQUE (source_id, external_item_id)
);

CREATE TABLE IF NOT EXISTS watch_deliveries (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id       UUID        NOT NULL REFERENCES watch_subscriptions(id) ON DELETE CASCADE,
    item_id               UUID        NOT NULL REFERENCES watch_items_seen(id) ON DELETE CASCADE,
    -- pending | sending | notified | no_push_target | skipped
    status                TEXT        NOT NULL DEFAULT 'pending',
    job_id                UUID,
    notified_at           TIMESTAMPTZ,
    created_at            TIMESTAMPTZ DEFAULT now(),
    UNIQUE (subscription_id, item_id)
);

CREATE TABLE IF NOT EXISTS watch_scan_runs (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id             UUID        REFERENCES watch_sources(id) ON DELETE CASCADE,
    provider              TEXT,
    items_requested       INTEGER,
    items_returned        INTEGER,
    new_items             INTEGER,
    possible_gap          BOOLEAN     DEFAULT false,
    estimated_cost_usd    NUMERIC,
    actual_cost_usd       NUMERIC,
    latency_ms            INTEGER,
    outcome               TEXT,                 -- ok | baseline | failed | locked
    failure_category      TEXT,
    created_at            TIMESTAMPTZ DEFAULT now()
);

-- Beat tick: due sources.
CREATE INDEX IF NOT EXISTS idx_watch_sources_due
    ON watch_sources (status, next_scan_at);
-- Per-user listing, per-source fan-out, global Free cap count.
CREATE INDEX IF NOT EXISTS idx_watch_subscriptions_user
    ON watch_subscriptions (user_id, status);
CREATE INDEX IF NOT EXISTS idx_watch_subscriptions_source
    ON watch_subscriptions (source_id, status);
CREATE INDEX IF NOT EXISTS idx_watch_subscriptions_tier_status
    ON watch_subscriptions (tier, status);
-- GET /watch/items?since=
CREATE INDEX IF NOT EXISTS idx_watch_items_seen_source_discovered
    ON watch_items_seen (source_id, discovered_at DESC);
-- Delivery sweep.
CREATE INDEX IF NOT EXISTS idx_watch_deliveries_status
    ON watch_deliveries (status, created_at);
-- Admin overview: last scan runs.
CREATE INDEX IF NOT EXISTS idx_watch_scan_runs_created
    ON watch_scan_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_watch_scan_runs_source
    ON watch_scan_runs (source_id, created_at DESC);

-- RLS on, no policies: only the service role (bypasses RLS) can read/write.
ALTER TABLE watch_sources       ENABLE ROW LEVEL SECURITY;
ALTER TABLE watch_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE watch_items_seen    ENABLE ROW LEVEL SECURITY;
ALTER TABLE watch_deliveries    ENABLE ROW LEVEL SECURITY;
ALTER TABLE watch_scan_runs     ENABLE ROW LEVEL SECURITY;

-- VERIFY (after):
--   SELECT tablename, rowsecurity FROM pg_tables WHERE tablename LIKE 'watch_%';
--     -- 5 rows, rowsecurity = true
--   SELECT count(*) FROM pg_policies WHERE tablename LIKE 'watch_%';   -- 0

-- ROLLBACK (only if the feature is abandoned; set WATCH_ENABLED=false first —
-- this deletes every watch row):
--   DROP TABLE IF EXISTS watch_scan_runs;
--   DROP TABLE IF EXISTS watch_deliveries;
--   DROP TABLE IF EXISTS watch_items_seen;
--   DROP TABLE IF EXISTS watch_subscriptions;
--   DROP TABLE IF EXISTS watch_sources;
