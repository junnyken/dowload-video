-- Phase 3 Admin Dashboard — System config + extended admin tables
-- Run after: 022_phase2_admin_dashboard.sql

-- admin_config: Postgres-backed config store (Redis is primary, this is durable backup)
CREATE TABLE IF NOT EXISTS admin_config (
    key             TEXT        PRIMARY KEY,
    value           TEXT        NOT NULL,
    description     TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      TEXT                           -- admin email
);

-- admin_users: Named admin accounts with RBAC roles
CREATE TABLE IF NOT EXISTS admin_users (
    id              BIGSERIAL   PRIMARY KEY,
    email           TEXT        NOT NULL UNIQUE,
    role            TEXT        NOT NULL DEFAULT 'viewer',  -- viewer|operator|admin|superadmin
    invited_by      TEXT,
    invited_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ,
    is_active       BOOLEAN     NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_admin_users_email ON admin_users (email);
CREATE INDEX IF NOT EXISTS idx_admin_users_role  ON admin_users (role);

-- Seed superadmin if not exists (placeholder — update email to real admin)
INSERT INTO admin_users (email, role, invited_by)
VALUES ('admin@vidgrab.io', 'superadmin', 'system')
ON CONFLICT (email) DO NOTHING;
