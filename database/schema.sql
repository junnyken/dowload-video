-- ============================================
-- Video Downloader - Supabase Database Schema
-- ============================================

-- 1. Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2. ENUM type
DO $$ BEGIN
    CREATE TYPE job_status AS ENUM ('pending', 'processing', 'success', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 3. download_jobs table (full schema)
CREATE TABLE IF NOT EXISTS download_jobs (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id        VARCHAR(255) NOT NULL,
    original_url    TEXT        NOT NULL,
    title           TEXT,
    slugified_name  TEXT,
    direct_mp4_url  TEXT,
    local_file_path TEXT,
    local_mp3_path  TEXT,
    thumbnail_url   TEXT,
    file_size_mb    FLOAT       DEFAULT 0,
    downloaded_height INTEGER   DEFAULT 0,
    is_audio_only   BOOLEAN     DEFAULT FALSE,
    status          job_status  NOT NULL DEFAULT 'pending',
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Indexes
CREATE INDEX IF NOT EXISTS idx_download_jobs_batch_id    ON download_jobs (batch_id);
CREATE INDEX IF NOT EXISTS idx_download_jobs_status      ON download_jobs (status);
CREATE INDEX IF NOT EXISTS idx_download_jobs_created_at  ON download_jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_download_jobs_original_url ON download_jobs (original_url);

-- 5. RLS
ALTER TABLE download_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all operations for service role" ON download_jobs;
CREATE POLICY "Allow all operations for service role"
    ON download_jobs FOR ALL USING (true) WITH CHECK (true);

-- ============================================
-- Table: user_usage
-- ============================================
CREATE TABLE IF NOT EXISTS user_usage (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         TEXT        NOT NULL UNIQUE,
    downloads_today INTEGER     NOT NULL DEFAULT 0,
    last_reset_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_usage_user_id ON user_usage (user_id);
ALTER TABLE user_usage ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all operations on user_usage" ON user_usage;
CREATE POLICY "Allow all operations on user_usage"
    ON user_usage FOR ALL USING (true) WITH CHECK (true);

-- ============================================
-- Table: profiles
-- ============================================
CREATE TABLE IF NOT EXISTS profiles (
    id         TEXT        PRIMARY KEY,
    tier       TEXT        NOT NULL DEFAULT 'free',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all operations on profiles" ON profiles;
CREATE POLICY "Allow all operations on profiles"
    ON profiles FOR ALL USING (true) WITH CHECK (true);
