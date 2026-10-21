-- 21/10/2026: the 5-minute cleanup (periodic_cleanup_downloads) marks deleted
-- files by local_file_path / local_mp3_path. Neither column was indexed, so
-- every update scanned all of download_jobs, and Supabase warned that the
-- disk IO budget was being depleted.
CREATE INDEX IF NOT EXISTS idx_download_jobs_local_file_path
    ON download_jobs (local_file_path) WHERE local_file_path IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_download_jobs_local_mp3_path
    ON download_jobs (local_mp3_path) WHERE local_mp3_path IS NOT NULL;
-- /user/history and /history: a user's jobs newest first.
CREATE INDEX IF NOT EXISTS idx_download_jobs_user_created
    ON download_jobs (user_id, created_at DESC);
