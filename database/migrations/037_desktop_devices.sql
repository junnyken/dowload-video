-- 037: Windows app machines (task #6087, PLAN-32D §4 / §7.1).
-- One row per machine: hash of the Windows MachineGuid (64 hex, salted in the
-- app; the raw GUID never reaches the server), the name the app reports
-- ("PC-213 (Windows 10.0.26200)"), last user signed in on it, last IP.
-- GET /api/v1/client/quota upserts on device_hash (best effort: without this
-- table the app keeps working, the admin just has no device list).
-- user_id is TEXT without FK, same convention as 034_desktop_downloads.sql.
-- Idempotent. NOT applied automatically — run in the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS desktop_devices (
    device_hash    TEXT        PRIMARY KEY CHECK (device_hash ~ '^[0-9a-f]{64}$'),
    display_name   TEXT,
    client_version TEXT,
    user_id        TEXT,
    last_ip        TEXT,
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_desktop_devices_user ON desktop_devices(user_id);
CREATE INDEX IF NOT EXISTS idx_desktop_devices_last_seen ON desktop_devices(last_seen DESC);

ALTER TABLE desktop_devices ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'desktop_devices' AND policyname = 'service_role_bypass'
    ) THEN
        CREATE POLICY service_role_bypass ON desktop_devices
            USING (auth.role() = 'service_role');
    END IF;
END;
$$;

-- VERIFY (after):
--   SELECT count(*) FROM desktop_devices;   -- 0 until an app 0.6.0 opens
