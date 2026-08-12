-- Phase 10: API Keys (multi-key) + Telegram Account Linking
-- Run once on Supabase SQL Editor or via migration runner.

-- ── New multi-key API key table ─────────────────────────────────────────────
-- Replaces the single-key user_api_keys pattern for the new self-service flow.
-- user_api_keys still exists for backward-compat with existing Bearer tokens.
CREATE TABLE IF NOT EXISTS api_keys (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT         NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    key_hash        TEXT         NOT NULL UNIQUE,   -- SHA-256 of raw key, never store plaintext
    key_prefix      TEXT         NOT NULL,          -- "vidgrab_" + first 8 hex chars (display only)
    label           TEXT         NOT NULL DEFAULT 'My API Key',
    is_active       BOOLEAN      NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    requests_today  INTEGER      NOT NULL DEFAULT 0,
    requests_total  INTEGER      NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS api_keys_user_id_idx ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS api_keys_key_hash_idx ON api_keys(key_hash);

-- ── Telegram account linking ─────────────────────────────────────────────────
-- Maps a Telegram user ID to a VidGrab account. Quota is shared with web.
CREATE TABLE IF NOT EXISTS telegram_links (
    telegram_user_id  BIGINT       PRIMARY KEY,
    vidgrab_user_id   TEXT         NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    telegram_username TEXT,
    linked_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS telegram_links_vidgrab_user_id_idx
    ON telegram_links(vidgrab_user_id);

-- ── Helper: atomically increment api_key usage counters ─────────────────────
CREATE OR REPLACE FUNCTION increment_api_key_usage(key_id_arg UUID)
RETURNS VOID LANGUAGE SQL AS $$
    UPDATE api_keys
    SET last_used_at   = NOW(),
        requests_total = requests_total + 1
    WHERE id = key_id_arg;
$$;
