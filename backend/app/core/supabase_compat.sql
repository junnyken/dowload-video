-- Runs FIRST on the Vibe Host Postgres, before the copied schema or
-- database/schema.sql + migrations/*.sql (docs/MIGRATION-DB-VIBEHOST.md;
-- app.core.db_migration runs it). Lives in backend/ because the image is
-- built from backend/ only. The migrations were written for Supabase:
-- they reference auth.users, auth.uid(), auth.role(), auth.jwt() and the
-- roles anon / authenticated / service_role. This file provides those names so
-- every migration applies unchanged.
--
-- auth.users holds the accounts copied from Supabase Auth (phase 1: synced,
-- sign-in still at Supabase; phase 2: the backend signs people in from here,
-- encrypted_password is Supabase's bcrypt hash).
-- The backend connects as the table owner, so RLS policies do not apply to it,
-- the same as Supabase's service_role.

-- Supabase keeps extensions in schema "extensions"; dumped defaults and
-- functions say extensions.uuid_generate_v4() etc.
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;
DO $$ BEGIN
  EXECUTE format('ALTER DATABASE %I SET search_path = "$user", public, extensions', current_database());
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'search_path not set: not the database owner';
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.users (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email               text UNIQUE,
    encrypted_password  text,
    email_confirmed_at  timestamptz,
    raw_user_meta_data  jsonb DEFAULT '{}'::jsonb,
    raw_app_meta_data   jsonb DEFAULT '{}'::jsonb,
    last_sign_in_at     timestamptz,
    banned_until        timestamptz,
    created_at          timestamptz DEFAULT now(),
    updated_at          timestamptz DEFAULT now()
);

-- No request JWT reaches this database: auth.uid() is NULL, as it was for
-- the backend's service-role calls on Supabase.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULL::uuid $$;
CREATE OR REPLACE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE AS $$ SELECT 'service_role'::text $$;
CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS $$ SELECT '{}'::jsonb $$;
