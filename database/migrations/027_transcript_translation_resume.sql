-- Phase T3-B — Transcript Translation: chunk-level resume
-- Adds a resume pointer so a redelivered/restarted task (worker died
-- mid-translation) can skip chunks it already completed, using an on-disk
-- checkpoint file (backend/downloads/transcript_jobs/{job_id}/progress.ndjson)
-- as the source of truth for already-translated text — see
-- app.tasks.transcript_translation_tasks._load_resume_state.
--
-- Safe to run multiple times (ADD COLUMN IF NOT EXISTS).

ALTER TABLE transcript_translation_jobs
    ADD COLUMN IF NOT EXISTS resume_chunk_index INT NOT NULL DEFAULT 0;
