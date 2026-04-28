-- ============================================
-- Video Downloader - Supabase Database Schema
-- Table: download_jobs
-- ============================================

-- 1. Create the custom ENUM type for job status
CREATE TYPE job_status AS ENUM ('pending', 'processing', 'success', 'failed');

-- 2. Enable the uuid-ossp extension (if not already enabled)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 3. Create the download_jobs table
CREATE TABLE download_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_id VARCHAR(255) NOT NULL,
    original_url TEXT NOT NULL,
    title TEXT,
    slugified_name TEXT,
    direct_mp4_url TEXT,
    status job_status NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Create indexes for common query patterns
CREATE INDEX idx_download_jobs_batch_id ON download_jobs (batch_id);
CREATE INDEX idx_download_jobs_status ON download_jobs (status);
CREATE INDEX idx_download_jobs_created_at ON download_jobs (created_at DESC);
CREATE INDEX idx_download_jobs_original_url ON download_jobs (original_url);

-- 5. Enable Row Level Security (RLS) - recommended for Supabase
ALTER TABLE download_jobs ENABLE ROW LEVEL SECURITY;

-- 6. Create a permissive policy (adjust based on your auth needs)
-- This policy allows all operations for authenticated users via the service key.
-- For production, you should create more restrictive policies.
CREATE POLICY "Allow all operations for service role"
    ON download_jobs
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- 7. Add table comment
COMMENT ON TABLE download_jobs IS 'Stores video download job metadata and status tracking';
COMMENT ON COLUMN download_jobs.batch_id IS 'Groups multiple URLs submitted from a single paste action';
COMMENT ON COLUMN download_jobs.original_url IS 'The original video URL provided by the user';
COMMENT ON COLUMN download_jobs.direct_mp4_url IS 'The resolved direct MP4 download link';
COMMENT ON COLUMN download_jobs.status IS 'Current processing status of the download job';
COMMENT ON COLUMN download_jobs.error_message IS 'Error details if the job failed';

-- ============================================
-- Table: user_usage (Quota Tracking)
-- ============================================
CREATE TABLE IF NOT EXISTS user_usage (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL UNIQUE,
    downloads_today INTEGER NOT NULL DEFAULT 0,
    last_reset_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_usage_user_id ON user_usage (user_id);

ALTER TABLE user_usage ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all operations on user_usage"
    ON user_usage FOR ALL USING (true) WITH CHECK (true);

-- ============================================
-- Table: profiles (User Tier / Plan)
-- ============================================
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    tier TEXT NOT NULL DEFAULT 'free',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all operations on profiles"
    ON profiles FOR ALL USING (true) WITH CHECK (true);
