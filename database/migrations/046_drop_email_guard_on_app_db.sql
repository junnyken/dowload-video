-- 046 — Drop sign-up email guard trigger on the app Postgres replica (vidgrab-backup)
--
-- Why:
--   Real sign-ups happen exclusively on Supabase Auth (SUPABASE_DB_URL), where
--   migration 043's BEFORE INSERT trigger `on_auth_user_block_disposable`
--   enforces the 5-provider allowlist, '+' alias block, and Gmail dot normalization.
--   On the Vibe Host app Postgres (`DB_BACKEND=postgres`), `auth.users` is only a
--   mirror populated by `app.core.db_migration.ensure_app_user()` via
--   `INSERT INTO auth.users ... ON CONFLICT (id) DO UPDATE`.
--   Because PostgreSQL fires `BEFORE INSERT` triggers BEFORE checking `ON CONFLICT`,
--   having `on_auth_user_block_disposable` on the app DB blocked syncing existing
--   pre-allowlist or custom-domain accounts (e.g. `@matbao.com`).
--
-- Scope:
--   Run on the app Postgres (`target_dsn` / `vidgrab-backup`) ONLY — NEVER on Supabase.
--   Preserves `public.reject_disposable_email_signup()` and `public.signup_email_allowlist`.

BEGIN;

DROP TRIGGER IF EXISTS on_auth_user_block_disposable ON auth.users;

COMMIT;
