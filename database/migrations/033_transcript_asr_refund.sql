-- Phase 32A — Transcript ASR quota refund (D1).
--
-- NOT applied automatically. Until this runs, the app still fails jobs
-- cleanly; it only logs "asr refund skipped" and the user's reserved minutes
-- stay consumed (today's behaviour).
--
-- Called by app.services.asr.jobs.refund_quota when a job fails, is
-- cancelled (DELETE of an unfinished job) or could not be queued, and by the
-- stuck-job sweeper. Idempotency is enforced by the caller: only the call
-- that moved the job row from a non-terminal status to 'failed' refunds.
--
-- p_usage_date: the UTC date the minutes were reserved on. reserve_
-- transcript_asr_usage (031) books against CURRENT_DATE, which is the UTC
-- date on Supabase's default (UTC) session timezone. NULL = CURRENT_DATE.
--
-- Safe to run multiple times (CREATE OR REPLACE).

CREATE OR REPLACE FUNCTION refund_transcript_asr_usage(
    p_user_id    TEXT,
    p_minutes    NUMERIC,
    p_usage_date DATE DEFAULT NULL
)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
BEGIN
    IF p_minutes IS NULL OR p_minutes <= 0 THEN
        RETURN FALSE;
    END IF;

    UPDATE transcript_asr_usage
    SET minutes_used = GREATEST(minutes_used - p_minutes, 0),
        jobs_count   = GREATEST(jobs_count - 1, 0),
        updated_at   = NOW()
    WHERE user_id = p_user_id
      AND usage_date = COALESCE(p_usage_date, CURRENT_DATE);

    RETURN FOUND;
END;
$$;
