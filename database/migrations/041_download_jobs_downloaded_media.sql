-- 28/10/2026 (quality #3): what the finished file really is, measured with
-- ffprobe (format_probe.parse_media_json) — shown again in the history.
-- On the Vibe Host Postgres the API adds this itself at startup
-- (app.core.database.ensure_columns); run by hand only on a Supabase backend.
ALTER TABLE download_jobs ADD COLUMN IF NOT EXISTS downloaded_media jsonb;
