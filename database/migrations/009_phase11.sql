-- ============================================================
-- Phase 11 — Workspaces, RBAC, Audit Logs, Approvals
-- ============================================================
-- All statements are additive / idempotent.
-- Run in Supabase SQL Editor (new tab).
-- ============================================================

-- ── 1. WORKSPACES ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS workspaces (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE NOT NULL,
    type            TEXT NOT NULL DEFAULT 'personal'
                        CHECK (type IN ('personal', 'team', 'enterprise')),
    owner_user_id   TEXT NOT NULL REFERENCES profiles(id),
    plan            TEXT NOT NULL DEFAULT 'free'
                        CHECK (plan IN ('free', 'team', 'enterprise')),
    -- Workspace-level quotas (override per-user quotas for team workspaces)
    quota_schedules         INT     DEFAULT 3,
    quota_archive_items     INT     DEFAULT 100,
    quota_pinned_files      INT     DEFAULT 3,
    quota_storage_bytes     BIGINT  DEFAULT 10737418240,  -- 10 GB
    quota_api_calls_per_day INT     DEFAULT 1000,
    quota_bulk_jobs_per_day INT     DEFAULT 10,
    -- Approval policy thresholds (0 = no approval required)
    approval_bulk_threshold  INT     DEFAULT 0,
    approval_schedules       BOOLEAN DEFAULT FALSE,
    approval_webhooks        BOOLEAN DEFAULT FALSE,
    approval_archive_delete  BOOLEAN DEFAULT FALSE,
    -- SSO / enterprise extensions (schema-ready, not fully enforced in Phase 11)
    sso_provider  TEXT,   -- 'google' | 'azure' | null
    sso_domain    TEXT,
    -- Feature flags
    features      JSONB   DEFAULT '{}',
    -- Metadata
    settings      JSONB   DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ── 2. WORKSPACE MEMBERSHIPS ──────────────────────────────────
CREATE TABLE IF NOT EXISTS workspace_memberships (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL REFERENCES profiles(id),
    role         TEXT NOT NULL DEFAULT 'viewer'
                     CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    invited_by   TEXT REFERENCES profiles(id),
    joined_at    TIMESTAMPTZ DEFAULT NOW(),
    status       TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'suspended')),
    UNIQUE (workspace_id, user_id)
);

-- ── 3. WORKSPACE INVITES ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS workspace_invites (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'viewer'
                     CHECK (role IN ('admin', 'editor', 'viewer')),
    token        TEXT UNIQUE NOT NULL DEFAULT encode(gen_random_bytes(32), 'hex'),
    invited_by   TEXT NOT NULL REFERENCES profiles(id),
    status       TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'accepted', 'expired', 'revoked')),
    expires_at   TIMESTAMPTZ DEFAULT NOW() + INTERVAL '7 days',
    accepted_at  TIMESTAMPTZ,
    accepted_by  TEXT REFERENCES profiles(id),
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ── 4. AUDIT LOGS (immutable — no UPDATE/DELETE) ──────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id   UUID REFERENCES workspaces(id),
    actor_user_id  TEXT REFERENCES profiles(id),
    actor_email    TEXT,                           -- snapshot at log time
    action         TEXT NOT NULL,                  -- e.g. 'download.created'
    resource_type  TEXT,                           -- 'download_job', 'archive_item', ...
    resource_id    TEXT,
    metadata       JSONB    DEFAULT '{}',
    ip_address     TEXT,
    user_agent     TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ── 5. APPROVAL REQUESTS ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS approval_requests (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    requester_user_id   TEXT NOT NULL REFERENCES profiles(id),
    action_type         TEXT NOT NULL,  -- 'bulk_download', 'schedule_create', 'webhook_create', 'archive_delete'
    action_payload      JSONB NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'awaiting_approval'
                            CHECK (status IN ('draft','awaiting_approval','approved','rejected','expired','executed')),
    approvers           TEXT[] DEFAULT '{}',       -- user_ids eligible to approve
    approved_by         TEXT REFERENCES profiles(id),
    rejected_by         TEXT REFERENCES profiles(id),
    rejection_reason    TEXT,
    expires_at          TIMESTAMPTZ DEFAULT NOW() + INTERVAL '48 hours',
    resolved_at         TIMESTAMPTZ,
    executed_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ── 6. ARCHIVE COMMENTS ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS archive_comments (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    archive_item_id  UUID NOT NULL REFERENCES archive_items(id) ON DELETE CASCADE,
    workspace_id     UUID REFERENCES workspaces(id),
    user_id          TEXT NOT NULL REFERENCES profiles(id),
    comment_text     TEXT NOT NULL CHECK (length(comment_text) BETWEEN 1 AND 1000),
    is_deleted       BOOLEAN DEFAULT FALSE,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ── 7. WORKSPACE NOTIFICATION CHANNELS ───────────────────────
CREATE TABLE IF NOT EXISTS workspace_notification_channels (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    channel_type  TEXT NOT NULL
                      CHECK (channel_type IN ('telegram', 'webhook', 'email', 'browser_push')),
    config        JSONB NOT NULL DEFAULT '{}',  -- secrets stored hashed
    event_filters TEXT[] DEFAULT ARRAY['job.failed','quota.warning','approval.requested'],
    is_active     BOOLEAN DEFAULT TRUE,
    created_by    TEXT REFERENCES profiles(id),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (workspace_id, channel_type)          -- one channel per type per workspace
);

-- ── 8. WORKSPACE EXPORT JOBS ──────────────────────────────────
CREATE TABLE IF NOT EXISTS workspace_export_jobs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    requested_by   TEXT NOT NULL REFERENCES profiles(id),
    export_type    TEXT NOT NULL
                       CHECK (export_type IN ('archive','audit_logs','schedules','collections','full')),
    format         TEXT NOT NULL DEFAULT 'json' CHECK (format IN ('json','csv')),
    filters        JSONB  DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','processing','ready','expired','failed')),
    row_count      INT,
    file_size_bytes BIGINT,
    download_url   TEXT,
    expires_at     TIMESTAMPTZ,
    error_message  TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    completed_at   TIMESTAMPTZ
);

-- ── 9. EXTEND EXISTING TABLES ─────────────────────────────────

-- archive_items: workspace scope + item status + assignment
ALTER TABLE archive_items
    ADD COLUMN IF NOT EXISTS workspace_id  UUID REFERENCES workspaces(id),
    ADD COLUMN IF NOT EXISTS item_status   TEXT DEFAULT 'new'
        CHECK (item_status IN ('new','reviewed','needs_attention','approved_for_reuse')),
    ADD COLUMN IF NOT EXISTS assigned_to   TEXT REFERENCES profiles(id),
    ADD COLUMN IF NOT EXISTS workspace_tags TEXT[] DEFAULT '{}';

-- archive_collections: workspace scope + sharing
ALTER TABLE archive_collections
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id),
    ADD COLUMN IF NOT EXISTS is_shared    BOOLEAN DEFAULT FALSE;

-- scheduled_jobs: workspace scope + approval gate
ALTER TABLE scheduled_jobs
    ADD COLUMN IF NOT EXISTS workspace_id      UUID REFERENCES workspaces(id),
    ADD COLUMN IF NOT EXISTS approval_required BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS approval_id       UUID REFERENCES approval_requests(id);

-- download_jobs: workspace scope
ALTER TABLE download_jobs
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id);

-- ── 10. INDEXES ───────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_wm_workspace   ON workspace_memberships(workspace_id);
CREATE INDEX IF NOT EXISTS idx_wm_user        ON workspace_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_wi_workspace   ON workspace_invites(workspace_id);
CREATE INDEX IF NOT EXISTS idx_wi_token       ON workspace_invites(token) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_wi_email       ON workspace_invites(email, status);
CREATE INDEX IF NOT EXISTS idx_al_workspace   ON audit_logs(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_al_actor       ON audit_logs(actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_al_action      ON audit_logs(action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ar_workspace   ON approval_requests(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_ar_requester   ON approval_requests(requester_user_id);
CREATE INDEX IF NOT EXISTS idx_ac_item        ON archive_comments(archive_item_id) WHERE NOT is_deleted;
CREATE INDEX IF NOT EXISTS idx_ej_workspace   ON workspace_export_jobs(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_workspace   ON archive_items(workspace_id) WHERE workspace_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sj_workspace   ON scheduled_jobs(workspace_id) WHERE workspace_id IS NOT NULL;

-- ── 11. ROW-LEVEL SECURITY ────────────────────────────────────

ALTER TABLE workspaces                      ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_memberships           ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_invites               ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs                      ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_requests               ENABLE ROW LEVEL SECURITY;
ALTER TABLE archive_comments                ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_notification_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_export_jobs           ENABLE ROW LEVEL SECURITY;

-- service_role bypasses RLS (backend uses service_role key)
-- CREATE POLICY has no IF NOT EXISTS clause in Postgres — DROP+CREATE instead.
DROP POLICY IF EXISTS "service_role_workspaces" ON workspaces;
CREATE POLICY "service_role_workspaces"
    ON workspaces FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS "service_role_memberships" ON workspace_memberships;
CREATE POLICY "service_role_memberships"
    ON workspace_memberships FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS "service_role_invites" ON workspace_invites;
CREATE POLICY "service_role_invites"
    ON workspace_invites FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS "service_role_audit" ON audit_logs;
CREATE POLICY "service_role_audit"
    ON audit_logs FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS "service_role_approvals" ON approval_requests;
CREATE POLICY "service_role_approvals"
    ON approval_requests FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS "service_role_comments" ON archive_comments;
CREATE POLICY "service_role_comments"
    ON archive_comments FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS "service_role_notif" ON workspace_notification_channels;
CREATE POLICY "service_role_notif"
    ON workspace_notification_channels FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);
DROP POLICY IF EXISTS "service_role_exports" ON workspace_export_jobs;
CREATE POLICY "service_role_exports"
    ON workspace_export_jobs FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

-- ── 12. AUTO-CREATE PERSONAL WORKSPACES FOR EXISTING USERS ───
-- Idempotent: ON CONFLICT DO NOTHING
INSERT INTO workspaces (name, slug, type, owner_user_id, plan,
                        quota_schedules, quota_archive_items, quota_pinned_files)
SELECT
    COALESCE(display_name, SPLIT_PART(email, '@', 1)) || '''s Workspace' AS name,
    'personal-' || REPLACE(id, '-', '')                                  AS slug,
    'personal'                                                            AS type,
    id                                                                    AS owner_user_id,
    CASE WHEN tier = 'pro' THEN 'team' ELSE 'free' END                   AS plan,
    CASE WHEN tier = 'pro' THEN 20 ELSE 3 END                            AS quota_schedules,
    CASE WHEN tier = 'pro' THEN 0 ELSE 100 END                           AS quota_archive_items,
    CASE WHEN tier = 'pro' THEN 50 ELSE 3 END                            AS quota_pinned_files
FROM profiles
ON CONFLICT (slug) DO NOTHING;

-- Add owner membership for all personal workspaces
INSERT INTO workspace_memberships (workspace_id, user_id, role)
SELECT w.id, w.owner_user_id, 'owner'
FROM workspaces w
ON CONFLICT (workspace_id, user_id) DO NOTHING;

-- Backfill workspace_id on existing archive_items + collections
UPDATE archive_items ai
SET workspace_id = w.id
FROM workspaces w
WHERE ai.user_id = w.owner_user_id
  AND w.type = 'personal'
  AND ai.workspace_id IS NULL;

UPDATE archive_collections ac
SET workspace_id = w.id
FROM workspaces w
WHERE ac.user_id = w.owner_user_id
  AND w.type = 'personal'
  AND ac.workspace_id IS NULL;

UPDATE scheduled_jobs sj
SET workspace_id = w.id
FROM workspaces w
WHERE sj.user_id = w.owner_user_id
  AND w.type = 'personal'
  AND sj.workspace_id IS NULL;

-- ── 13. HELPER VIEW: USER WORKSPACE SUMMARY ───────────────────
CREATE OR REPLACE VIEW user_workspaces AS
SELECT
    w.id,
    w.name,
    w.slug,
    w.type,
    w.plan,
    w.owner_user_id,
    wm.user_id,
    wm.role,
    wm.status  AS membership_status,
    w.quota_schedules,
    w.quota_archive_items,
    w.quota_pinned_files,
    w.quota_storage_bytes,
    w.approval_bulk_threshold,
    w.approval_schedules,
    w.features,
    w.created_at
FROM workspaces w
JOIN workspace_memberships wm ON wm.workspace_id = w.id
WHERE wm.status = 'active';
