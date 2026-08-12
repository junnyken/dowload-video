-- Phase T4-A — Transcript Translation: reading-speed QA warnings
-- After translation, cues whose text is too dense to comfortably read in
-- their on-screen duration (>20 chars/sec or >42 chars, common subtitle
-- style-guide thresholds) are flagged — informational only, never blocks
-- the job. See app.services.subtitle_qa.check_reading_speed.
--
-- Safe to run multiple times (ADD COLUMN IF NOT EXISTS).

ALTER TABLE transcript_translation_jobs
    ADD COLUMN IF NOT EXISTS reading_speed_warning_count INT NOT NULL DEFAULT 0;
