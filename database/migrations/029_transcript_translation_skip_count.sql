-- Phase T3-D — Transcript Translation: surface silently-dropped cue blocks
-- parse_srt/parse_vtt drop malformed blocks (missing/broken timestamp line)
-- without raising — cue_count only ever reflected what parsed successfully.
-- This column lets the user see "we found N valid cues, M looked malformed
-- and were skipped" instead of silently under-translating a broken file.
--
-- Safe to run multiple times (ADD COLUMN IF NOT EXISTS).

ALTER TABLE transcript_translation_jobs
    ADD COLUMN IF NOT EXISTS skipped_block_count INT NOT NULL DEFAULT 0;
