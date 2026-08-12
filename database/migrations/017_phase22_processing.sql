-- Phase 22: Post-Processing Suite 2.0
-- derived_jobs: source → processed output trace
CREATE TABLE IF NOT EXISTS derived_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_job_id UUID,  -- soft FK to download_jobs.id (no CASCADE — download_jobs may not exist)
    user_id UUID,
    process_type TEXT NOT NULL, -- 'trim','gif','mp4_loop','extract_audio','subtitle','package_zip','burn_subtitle','frame_thumb'
    input_path TEXT,    -- server-side local path used as input
    output_path TEXT,   -- server-side local path of processed file
    output_url TEXT,    -- download URL returned to client
    preset_name TEXT,
    params JSONB DEFAULT '{}',
    status TEXT DEFAULT 'done',
    error_text TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_derived_jobs_source  ON derived_jobs(source_job_id);
CREATE INDEX IF NOT EXISTS idx_derived_jobs_user    ON derived_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_derived_jobs_expires ON derived_jobs(expires_at);

-- naming_presets
CREATE TABLE IF NOT EXISTS naming_presets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    name TEXT NOT NULL,
    template TEXT NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_naming_presets_user ON naming_presets(user_id);
