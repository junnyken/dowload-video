-- Phase T3-C — Transcript Translation: bounded self-requeue on transient failure
-- SoftTimeLimitExceeded (large job legitimately needs more than one run) and
-- TranslationAlignmentError (a chunk's LLM response was malformed — often
-- succeeds on a fresh attempt) are worth retrying via the checkpoint built
-- in T3-B, rather than being treated as permanent failures that also delete
-- the checkpoint files. Bounded by timeout_retry_count so a job that keeps
-- failing the same way doesn't requeue forever.
--
-- Safe to run multiple times (ADD COLUMN IF NOT EXISTS).

ALTER TABLE transcript_translation_jobs
    ADD COLUMN IF NOT EXISTS timeout_retry_count INT NOT NULL DEFAULT 0;
