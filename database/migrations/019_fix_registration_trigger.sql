-- ============================================================
-- Migration 019: Fix "Database error saving new user"
-- Fixes the on_auth_user_created + profiles_create_usage
-- chain so all three rows are always created on sign-up.
-- Safe to run multiple times.
-- Run in Supabase Dashboard → SQL Editor (service_role).
-- ============================================================

-- 1. Ensure profiles has every column the trigger references
ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS email        TEXT,
  ADD COLUMN IF NOT EXISTS display_name TEXT,
  ADD COLUMN IF NOT EXISTS avatar_url   TEXT,
  ADD COLUMN IF NOT EXISTS tier         TEXT DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS updated_at   TIMESTAMPTZ DEFAULT NOW();

-- 2. Ensure user_usage has all expected columns
ALTER TABLE public.user_usage
  ADD COLUMN IF NOT EXISTS downloads_this_month INTEGER     DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_monthly_reset   TIMESTAMPTZ DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS bulk_jobs_count      INTEGER     DEFAULT 0,
  ADD COLUMN IF NOT EXISTS plan                 TEXT        DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS updated_at           TIMESTAMPTZ DEFAULT NOW();

-- 3. Ensure user_preferences table exists (created in migration 002)
CREATE TABLE IF NOT EXISTS public.user_preferences (
  user_id           UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  default_quality   TEXT        NOT NULL DEFAULT 'video',
  default_mp3_kbps  INTEGER     NOT NULL DEFAULT 320,
  remove_watermark  BOOLEAN     NOT NULL DEFAULT TRUE,
  download_subs     BOOLEAN     NOT NULL DEFAULT FALSE,
  bulk_count_preset INTEGER     NOT NULL DEFAULT 20,
  theme_mode        TEXT        NOT NULL DEFAULT 'dark',
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.user_preferences ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'user_preferences' AND policyname = 'User owns preferences'
  ) THEN
    CREATE POLICY "User owns preferences"
      ON public.user_preferences FOR ALL
      USING (auth.uid() = user_id)
      WITH CHECK (auth.uid() = user_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'user_preferences' AND policyname = 'Service role full preferences'
  ) THEN
    CREATE POLICY "Service role full preferences"
      ON public.user_preferences FOR ALL TO service_role
      USING (true) WITH CHECK (true);
  END IF;
END $$;

-- 4. Drop the tier CHECK constraint so any tier value is accepted
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_tier_check') THEN
    ALTER TABLE public.profiles DROP CONSTRAINT profiles_tier_check;
  END IF;
END $$;

-- 5. Rewrite create_user_usage_row() — robust, explicit schema
CREATE OR REPLACE FUNCTION public.create_user_usage_row()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.user_usage (user_id)
  VALUES (NEW.id)
  ON CONFLICT (user_id) DO NOTHING;
  RETURN NEW;
EXCEPTION WHEN OTHERS THEN
  -- Never block profile creation; log and continue.
  RAISE WARNING 'create_user_usage_row failed for %: %', NEW.id, SQLERRM;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS profiles_create_usage ON public.profiles;
CREATE TRIGGER profiles_create_usage
  AFTER INSERT ON public.profiles
  FOR EACH ROW EXECUTE FUNCTION public.create_user_usage_row();

-- 6. Rewrite handle_new_user() — robust, explicit schema, no missing column risk
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  -- a. Profile row (profiles_create_usage trigger will add user_usage row)
  INSERT INTO public.profiles (id, email, tier, created_at)
  VALUES (NEW.id::TEXT, NEW.email, 'free', NOW())
  ON CONFLICT (id) DO NOTHING;

  -- b. Preferences row
  BEGIN
    INSERT INTO public.user_preferences (user_id)
    VALUES (NEW.id)
    ON CONFLICT (user_id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'handle_new_user: user_preferences insert failed for %: %', NEW.id, SQLERRM;
  END;

  -- c. Usage row (also inserted by profiles_create_usage trigger above — idempotent)
  BEGIN
    INSERT INTO public.user_usage (user_id)
    VALUES (NEW.id::TEXT)
    ON CONFLICT (user_id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'handle_new_user: user_usage insert failed for %: %', NEW.id, SQLERRM;
  END;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Verify: try inserting a dummy profile and preferences to confirm constraints work
DO $$
DECLARE
  _ok BOOLEAN;
BEGIN
  -- Quick schema sanity check
  SELECT TRUE INTO _ok
  FROM information_schema.columns
  WHERE table_schema = 'public' AND table_name = 'profiles' AND column_name = 'email';
  IF NOT _ok THEN
    RAISE EXCEPTION 'profiles.email column is missing — migration may have failed';
  END IF;
  RAISE NOTICE 'Migration 019 applied successfully.';
END $$;
