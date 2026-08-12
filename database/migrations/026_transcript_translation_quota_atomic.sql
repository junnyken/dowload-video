-- Phase T3-A — Transcript Translation: atomic quota reservation
-- Replaces the check-then-increment pattern in transcript_translate.py
-- (read usage once at request start, track a Python-side counter across
-- files in the batch, blind-increment via RPC after each accepted file)
-- with a single atomic "reserve if it fits" call per file. Two near-
-- simultaneous requests for the SAME user_id now serialize on the row lock
-- instead of both reading the same pre-commit usage and both passing.
--
-- Different users never contend with each other (locks are per user_id+date
-- row), so this doesn't introduce cross-user serialization.
--
-- Safe to run multiple times (CREATE OR REPLACE).

CREATE OR REPLACE FUNCTION reserve_transcript_translation_usage(
    p_user_id TEXT,
    p_cues    INT,
    p_limit   INT
)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE
    v_current INT;
BEGIN
    -- Ensure today's row exists, then lock it — this INSERT is a no-op if
    -- the row is already there (idempotent), and only ONE concurrent caller
    -- wins the row lock acquired by the SELECT ... FOR UPDATE below.
    INSERT INTO transcript_translation_usage (user_id, usage_date, cues_translated, jobs_count)
    VALUES (p_user_id, CURRENT_DATE, 0, 0)
    ON CONFLICT (user_id, usage_date) DO NOTHING;

    SELECT cues_translated INTO v_current
    FROM transcript_translation_usage
    WHERE user_id = p_user_id AND usage_date = CURRENT_DATE
    FOR UPDATE;

    IF v_current + p_cues > p_limit THEN
        RETURN FALSE;
    END IF;

    UPDATE transcript_translation_usage
    SET cues_translated = cues_translated + p_cues,
        jobs_count       = jobs_count + 1,
        updated_at       = NOW()
    WHERE user_id = p_user_id AND usage_date = CURRENT_DATE;

    RETURN TRUE;
END;
$$;
