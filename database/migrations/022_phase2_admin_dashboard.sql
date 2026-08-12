-- Phase 2 Admin Dashboard — Platform health metrics + audit log promotion
-- Run order: after 021_create_admin_account.sql

-- ── platform_health_metrics ───────────────────────────────────────────────────
-- Hourly aggregate written by Celery task `aggregate-platform-health` (1h)
-- Used by GET /admin/platforms/{p}/detail for historical phase heatmap.

CREATE TABLE IF NOT EXISTS platform_health_metrics (
    id              BIGSERIAL PRIMARY KEY,
    platform        TEXT        NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    hour_bucket     TIMESTAMPTZ NOT NULL,                  -- truncated to hour
    total_jobs      INT         NOT NULL DEFAULT 0,
    success_jobs    INT         NOT NULL DEFAULT 0,
    failed_jobs     INT         NOT NULL DEFAULT 0,
    avg_duration_ms INT,
    p95_duration_ms INT,
    error_counts    JSONB       NOT NULL DEFAULT '{}',     -- {error_type: count}
    phase_fail_heatmap JSONB    NOT NULL DEFAULT '{}',     -- {phase: {error_type: count}}
    circuit_opens   INT         NOT NULL DEFAULT 0,
    UNIQUE (platform, hour_bucket)
);

CREATE INDEX IF NOT EXISTS idx_phm_platform_bucket
    ON platform_health_metrics (platform, hour_bucket DESC);

CREATE INDEX IF NOT EXISTS idx_phm_bucket
    ON platform_health_metrics (hour_bucket DESC);

-- Auto-prune rows older than 90 days (keep 90d of hourly data)
CREATE OR REPLACE FUNCTION prune_platform_health_metrics() RETURNS void
    LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM platform_health_metrics
    WHERE hour_bucket < now() - INTERVAL '90 days';
END;
$$;

-- ── admin_audit_log ───────────────────────────────────────────────────────────
-- Postgres-backed audit trail for admin actions.
-- Mirrors audit_logs but scoped to admin operations for easy querying.

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id              BIGSERIAL PRIMARY KEY,
    action          TEXT        NOT NULL,
    actor_email     TEXT,
    resource_type   TEXT,
    resource_id     TEXT,
    metadata        JSONB       NOT NULL DEFAULT '{}',
    ip_address      TEXT,
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_aal_action      ON admin_audit_log (action);
CREATE INDEX IF NOT EXISTS idx_aal_created_at  ON admin_audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_aal_actor       ON admin_audit_log (actor_email);
