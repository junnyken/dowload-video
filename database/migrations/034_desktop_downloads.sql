-- 034: VidGrab Desktop (Windows app) download history sync (C1).
-- Metadata only: the app never uploads local file paths. One row per
-- (user, client-generated id); POST /api/v1/client/history upserts on it.
-- user_id is TEXT without FK, same convention as 031_transcript_asr.sql.
-- Idempotent. NOT applied automatically — run in the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS desktop_downloads (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        TEXT        NOT NULL,
    client_id      TEXT        NOT NULL,
    device_id      TEXT,
    client_version TEXT,
    url            TEXT        NOT NULL,
    title          TEXT,
    platform       TEXT,
    format_label   TEXT,
    file_size      BIGINT,
    state          TEXT        NOT NULL CHECK (state IN ('completed', 'failed')),
    error_code     TEXT,
    finished_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, client_id)
);

CREATE INDEX IF NOT EXISTS idx_desktop_downloads_user_created
    ON desktop_downloads(user_id, created_at DESC);

ALTER TABLE desktop_downloads ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'desktop_downloads' AND policyname = 'service_role_bypass'
    ) THEN
        CREATE POLICY service_role_bypass ON desktop_downloads
            USING (auth.role() = 'service_role');
    END IF;
END;
$$;
