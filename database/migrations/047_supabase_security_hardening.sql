-- ============================================================================
-- Migration 047: Supabase & PostgreSQL Security Hardening
-- Resolves Supabase Advisor findings on project wtwnbagcqyedindtcadv:
--   1. ERROR rls_disabled_in_public (0013):
--        - public.video_analysis
--        - public.video_analysis_history
--   2. ERROR security_definer_view (0010):
--        - public.download_jobs_with_notes
--        - public.pinned_files_by_user
--        - public.user_workspaces
--   3. WARN function_search_path_mutable (0011):
--        Pins `search_path = public, pg_temp` on all 14 public helper/RPC functions.
--
-- Safe & Idempotent:
--   - Neither frontend nor anon/authenticated clients read `video_analysis*` or
--     the 3 views directly; backend uses service_role / table owner (bypasses RLS).
--   - Requires PostgreSQL 15+ for `ALTER VIEW ... SET (security_invoker = true)`
--     (production runs PostgreSQL 17.0.6 on Supabase & PG 16 on Vibe Host).
-- ============================================================================

BEGIN;

-- ─── 1. Enable RLS on exposed public tables ─────────────────────────────────
ALTER TABLE IF EXISTS public.video_analysis ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.video_analysis_history ENABLE ROW LEVEL SECURITY;

-- ─── 2. Switch public views from SECURITY DEFINER to SECURITY INVOKER ───────
ALTER VIEW IF EXISTS public.download_jobs_with_notes SET (security_invoker = true);
ALTER VIEW IF EXISTS public.pinned_files_by_user SET (security_invoker = true);
ALTER VIEW IF EXISTS public.user_workspaces SET (security_invoker = true);

-- ─── 3. Pin immutable search_path on public functions ───────────────────────
ALTER FUNCTION public.bump_transcript_cache_hit(text) SET search_path = public, pg_temp;
ALTER FUNCTION public.cleanup_old_anonymous_notes() SET search_path = public, pg_temp;
ALTER FUNCTION public.increment_api_key_usage(uuid) SET search_path = public, pg_temp;
ALTER FUNCTION public.increment_transcript_translation_usage(text, integer) SET search_path = public, pg_temp;
ALTER FUNCTION public.protect_profile_billing_columns() SET search_path = public, pg_temp;
ALTER FUNCTION public.prune_old_webhook_deliveries() SET search_path = public, pg_temp;
ALTER FUNCTION public.prune_platform_health_metrics() SET search_path = public, pg_temp;
ALTER FUNCTION public.refund_transcript_asr_usage(text, numeric, date) SET search_path = public, pg_temp;
ALTER FUNCTION public.reserve_transcript_asr_usage(text, numeric, numeric) SET search_path = public, pg_temp;
ALTER FUNCTION public.reserve_transcript_translation_usage(text, integer, integer) SET search_path = public, pg_temp;
ALTER FUNCTION public.reset_daily_usage() SET search_path = public, pg_temp;
ALTER FUNCTION public.reset_monthly_usage() SET search_path = public, pg_temp;
ALTER FUNCTION public.set_updated_at() SET search_path = public, pg_temp;
ALTER FUNCTION public.update_download_job_timestamp() SET search_path = public, pg_temp;

COMMIT;
