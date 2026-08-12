-- Phase 16 Enterprise / API-first
-- Multi-tenant billing, partner API keys, usage metering,
-- white-label settings, HMAC webhooks, and API-submitted jobs.
-- Safe to run multiple times (all CREATE ... IF NOT EXISTS).

-- ─────────────────────────────────────────────
-- 0. updated_at trigger function (idempotent)
-- ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- ─────────────────────────────────────────────
-- 1. tenants
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenants (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id               UUID UNIQUE,   -- optional: links to workspaces if Phase 11 is applied
    name                       TEXT NOT NULL,
    slug                       TEXT UNIQUE NOT NULL,
    plan                       TEXT NOT NULL DEFAULT 'starter'
                                   CHECK (plan IN ('starter', 'growth', 'enterprise')),
    plan_seats                 INT  DEFAULT 1,
    plan_api_calls_per_month   INT  DEFAULT 1000,
    plan_storage_gb            INT  DEFAULT 5,
    status                     TEXT DEFAULT 'active'
                                   CHECK (status IN ('active', 'suspended', 'canceled')),
    trial_ends_at              TIMESTAMPTZ,
    subscription_id            TEXT,
    license_key                TEXT,
    license_expires_at         TIMESTAMPTZ,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tenants_workspace_id_idx  ON tenants(workspace_id);
CREATE INDEX IF NOT EXISTS tenants_slug_idx          ON tenants(slug);
CREATE INDEX IF NOT EXISTS tenants_status_idx        ON tenants(status);

CREATE OR REPLACE TRIGGER tenants_set_updated_at
    BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─────────────────────────────────────────────
-- 2. tenant_api_keys  (partner keys, prefix vgp_)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenant_api_keys (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id            UUID,   -- optional: links to workspaces if Phase 11 is applied
    key_hash                TEXT UNIQUE NOT NULL,   -- SHA-256 of raw key
    key_prefix              TEXT NOT NULL,          -- vgp_xxxxxxxx display only
    label                   TEXT NOT NULL DEFAULT 'API Key',
    scopes                  TEXT[] NOT NULL DEFAULT '{read,write,batch}',
                            -- allowed values: read | write | batch | webhook | admin
    rate_limit_per_min      INT  NOT NULL DEFAULT 60,
    rate_limit_per_day      INT  NOT NULL DEFAULT 10000,
    is_active               BOOLEAN NOT NULL DEFAULT true,
    created_by              TEXT,                   -- user_id of creator
    last_used_at            TIMESTAMPTZ,
    requests_today          INT    NOT NULL DEFAULT 0,
    requests_this_month     INT    NOT NULL DEFAULT 0,
    requests_total          BIGINT NOT NULL DEFAULT 0,
    ip_allowlist            TEXT[] DEFAULT NULL,    -- NULL = unrestricted
    expires_at              TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tenant_api_keys_tenant_id_idx    ON tenant_api_keys(tenant_id);
CREATE INDEX IF NOT EXISTS tenant_api_keys_workspace_id_idx ON tenant_api_keys(workspace_id);
CREATE INDEX IF NOT EXISTS tenant_api_keys_key_hash_idx     ON tenant_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS tenant_api_keys_is_active_idx    ON tenant_api_keys(is_active)
    WHERE is_active = true;

CREATE OR REPLACE TRIGGER tenant_api_keys_set_updated_at
    BEFORE UPDATE ON tenant_api_keys
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─────────────────────────────────────────────
-- 3. tenant_usage_daily  (billing metering)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenant_usage_daily (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    date                DATE NOT NULL DEFAULT CURRENT_DATE,
    api_calls           INT    NOT NULL DEFAULT 0,
    downloads           INT    NOT NULL DEFAULT 0,
    batch_jobs          INT    NOT NULL DEFAULT 0,
    storage_bytes_used  BIGINT NOT NULL DEFAULT 0,
    webhook_deliveries  INT    NOT NULL DEFAULT 0,
    active_seats        INT    NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, date)
);

CREATE INDEX IF NOT EXISTS tenant_usage_daily_tenant_id_idx      ON tenant_usage_daily(tenant_id);
CREATE INDEX IF NOT EXISTS tenant_usage_daily_tenant_date_idx    ON tenant_usage_daily(tenant_id, date DESC);

CREATE OR REPLACE TRIGGER tenant_usage_daily_set_updated_at
    BEFORE UPDATE ON tenant_usage_daily
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─────────────────────────────────────────────
-- 4. tenant_settings  (white-label + feature flags)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenant_settings (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID UNIQUE NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    branding      JSONB NOT NULL DEFAULT '{
        "app_name":       null,
        "logo_url":       null,
        "favicon_url":    null,
        "primary_color":  null,
        "accent_color":   null,
        "support_email":  null,
        "hide_powered_by": false
    }',
    features      JSONB NOT NULL DEFAULT '{
        "batch_download":      true,
        "webhooks":            true,
        "white_label":         false,
        "sso":                 false,
        "audit_log_export":    false,
        "priority_queue":      false
    }',
    custom_domain TEXT,
    notifications JSONB NOT NULL DEFAULT '{
        "download_complete": true,
        "quota_warning":     true
    }',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tenant_settings_tenant_id_idx ON tenant_settings(tenant_id);

CREATE OR REPLACE TRIGGER tenant_settings_set_updated_at
    BEFORE UPDATE ON tenant_settings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─────────────────────────────────────────────
-- 5. webhook_endpoints
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id          UUID,   -- optional: links to workspaces if Phase 11 is applied
    url                   TEXT NOT NULL,
    -- Store bcrypt hash of the HMAC secret; raw secret lives only in app memory
    secret                TEXT NOT NULL,
    events                TEXT[] NOT NULL DEFAULT
                              '{job.completed,job.failed,batch.completed}',
    -- Supported: job.completed, job.failed, batch.completed, batch.failed,
    --            quota.warning, quota.exceeded, member.joined, member.left
    is_active             BOOLEAN NOT NULL DEFAULT true,
    created_by            TEXT,
    last_triggered_at     TIMESTAMPTZ,
    total_deliveries      INT NOT NULL DEFAULT 0,
    successful_deliveries INT NOT NULL DEFAULT 0,
    failed_deliveries     INT NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS webhook_endpoints_tenant_id_idx    ON webhook_endpoints(tenant_id);
CREATE INDEX IF NOT EXISTS webhook_endpoints_workspace_id_idx ON webhook_endpoints(workspace_id);
CREATE INDEX IF NOT EXISTS webhook_endpoints_is_active_idx    ON webhook_endpoints(tenant_id, is_active)
    WHERE is_active = true;

CREATE OR REPLACE TRIGGER webhook_endpoints_set_updated_at
    BEFORE UPDATE ON webhook_endpoints
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─────────────────────────────────────────────
-- 6. webhook_deliveries
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    endpoint_id      UUID NOT NULL
                         REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
    tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    event_type       TEXT NOT NULL,
    payload          JSONB NOT NULL,
    attempt_count    INT  NOT NULL DEFAULT 1,
    last_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    next_retry_at    TIMESTAMPTZ,
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'delivered', 'failed', 'abandoned')),
    response_status  INT,
    response_body    TEXT,
    error_message    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- No updated_at: rows are append-friendly; status transitions are the mutation.
);

CREATE INDEX IF NOT EXISTS webhook_deliveries_endpoint_id_idx   ON webhook_deliveries(endpoint_id);
CREATE INDEX IF NOT EXISTS webhook_deliveries_tenant_id_idx     ON webhook_deliveries(tenant_id);
CREATE INDEX IF NOT EXISTS webhook_deliveries_status_idx        ON webhook_deliveries(status)
    WHERE status IN ('pending', 'failed');
CREATE INDEX IF NOT EXISTS webhook_deliveries_next_retry_at_idx ON webhook_deliveries(next_retry_at)
    WHERE next_retry_at IS NOT NULL AND status = 'failed';
CREATE INDEX IF NOT EXISTS webhook_deliveries_created_at_idx    ON webhook_deliveries(endpoint_id, created_at DESC);

-- Auto-prune: keep only 30 days of delivery history per endpoint
CREATE OR REPLACE FUNCTION prune_old_webhook_deliveries()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM webhook_deliveries
    WHERE endpoint_id = NEW.endpoint_id
      AND created_at < NOW() - INTERVAL '30 days';
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER webhook_deliveries_auto_prune
    AFTER INSERT ON webhook_deliveries
    FOR EACH ROW EXECUTE FUNCTION prune_old_webhook_deliveries();

-- ─────────────────────────────────────────────
-- 7. partner_jobs  (API-submitted download jobs)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS partner_jobs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    api_key_id        UUID REFERENCES tenant_api_keys(id) ON DELETE SET NULL,
    url               TEXT NOT NULL,
    quality           TEXT NOT NULL DEFAULT 'best',
    format            TEXT,
    options           JSONB NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'queued'
                          CHECK (status IN ('queued', 'processing', 'done', 'failed', 'expired')),
    progress          INT  NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    celery_task_id    TEXT,
    result            JSONB,
    error_code        TEXT,
    error_message     TEXT,
    webhook_delivered BOOLEAN NOT NULL DEFAULT false,
    priority          TEXT NOT NULL DEFAULT 'normal'
                          CHECK (priority IN ('low', 'normal', 'high')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at        TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours')
);

CREATE INDEX IF NOT EXISTS partner_jobs_tenant_id_idx       ON partner_jobs(tenant_id);
CREATE INDEX IF NOT EXISTS partner_jobs_api_key_id_idx      ON partner_jobs(api_key_id);
CREATE INDEX IF NOT EXISTS partner_jobs_status_idx          ON partner_jobs(tenant_id, status);
CREATE INDEX IF NOT EXISTS partner_jobs_celery_task_id_idx  ON partner_jobs(celery_task_id)
    WHERE celery_task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS partner_jobs_expires_at_idx      ON partner_jobs(expires_at)
    WHERE status NOT IN ('done', 'failed', 'expired');
CREATE INDEX IF NOT EXISTS partner_jobs_webhook_pending_idx ON partner_jobs(webhook_delivered, tenant_id)
    WHERE webhook_delivered = false AND status IN ('done', 'failed');

CREATE OR REPLACE TRIGGER partner_jobs_set_updated_at
    BEFORE UPDATE ON partner_jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─────────────────────────────────────────────
-- 8. Row Level Security
-- ─────────────────────────────────────────────
ALTER TABLE tenants               ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_api_keys       ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_usage_daily    ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_settings       ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_endpoints     ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_deliveries    ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_jobs          ENABLE ROW LEVEL SECURITY;

-- service_role bypass (backend always uses service key)
DO $$ BEGIN
    -- tenants
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tenants' AND policyname = 'service_role_tenants'
    ) THEN
        CREATE POLICY service_role_tenants ON tenants
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    -- tenant_api_keys
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tenant_api_keys' AND policyname = 'service_role_tenant_api_keys'
    ) THEN
        CREATE POLICY service_role_tenant_api_keys ON tenant_api_keys
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    -- tenant_usage_daily
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tenant_usage_daily' AND policyname = 'service_role_tenant_usage_daily'
    ) THEN
        CREATE POLICY service_role_tenant_usage_daily ON tenant_usage_daily
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    -- tenant_settings
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tenant_settings' AND policyname = 'service_role_tenant_settings'
    ) THEN
        CREATE POLICY service_role_tenant_settings ON tenant_settings
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    -- webhook_endpoints
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'webhook_endpoints' AND policyname = 'service_role_webhook_endpoints'
    ) THEN
        CREATE POLICY service_role_webhook_endpoints ON webhook_endpoints
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    -- webhook_deliveries
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'webhook_deliveries' AND policyname = 'service_role_webhook_deliveries'
    ) THEN
        CREATE POLICY service_role_webhook_deliveries ON webhook_deliveries
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    -- partner_jobs
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'partner_jobs' AND policyname = 'service_role_partner_jobs'
    ) THEN
        CREATE POLICY service_role_partner_jobs ON partner_jobs
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- ─────────────────────────────────────────────
-- 9. Backfill: create a tenant for every existing workspace (only if workspaces table exists)
-- ─────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'workspaces'
    ) THEN
        INSERT INTO tenants (workspace_id, name, slug, plan)
        SELECT
            w.id,
            COALESCE(w.name, 'Workspace ' || w.id::TEXT),
            LOWER(REGEXP_REPLACE(
                COALESCE(w.name, 'workspace'),
                '[^a-zA-Z0-9]+', '-', 'g'
            )) || '-' || SUBSTR(w.id::TEXT, 1, 8),
            'starter'
        FROM workspaces w
        WHERE NOT EXISTS (
            SELECT 1 FROM tenants t WHERE t.workspace_id = w.id
        );
    END IF;
END $$;

-- ─────────────────────────────────────────────
-- 10. Backfill: create default settings for every tenant
-- ─────────────────────────────────────────────
INSERT INTO tenant_settings (tenant_id)
SELECT t.id
FROM tenants t
WHERE NOT EXISTS (
    SELECT 1 FROM tenant_settings ts WHERE ts.tenant_id = t.id
);
