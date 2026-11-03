-- 043 — Strict Sign-Up Email Guard (5 Trusted Providers Whitelist + Anti-Gmail Alias/Dot Abuse)
--
-- Rules enforced on BEFORE INSERT ON auth.users:
--   1. Only 5 major email providers (17 domains) are allowed:
--      - Gmail:          gmail.com, googlemail.com
--      - Outlook/Hotmail: outlook.com, hotmail.com, live.com, msn.com, outlook.com.vn, hotmail.com.vn
--      - Apple iCloud:   icloud.com, me.com, mac.com
--      - Yahoo Mail:     yahoo.com, yahoo.com.vn, ymail.com, rocketmail.com
--      - ProtonMail:     proton.me, protonmail.com, pm.me
--      (Unless exact email is in public.signup_email_allowlist).
--   2. Rejects any sub-address containing '+' in the local part (e.g. ten+1@gmail.com).
--   3. Normalizes Gmail dots and googlemail.com domain:
--      a.b.c@gmail.com / a.b.c@googlemail.com -> abc@gmail.com
--      and rejects sign-up if another account in auth.users already maps to the same canonical Gmail.

BEGIN;

CREATE TABLE IF NOT EXISTS public.signup_email_allowlist (
    email      text PRIMARY KEY CHECK (email = lower(email) AND position('@' in email) > 1),
    note       text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION public.reject_disposable_email_signup()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_email      text := lower(btrim(coalesce(NEW.email, '')));
    v_local      text;
    v_domain     text;
    v_norm_local text;
    v_allowed     constant text[] := ARRAY[
        'gmail.com', 'googlemail.com',
        'outlook.com', 'hotmail.com', 'live.com', 'msn.com', 'outlook.com.vn', 'hotmail.com.vn',
        'icloud.com', 'me.com', 'mac.com',
        'yahoo.com', 'yahoo.com.vn', 'ymail.com', 'rocketmail.com',
        'proton.me', 'protonmail.com', 'pm.me'
    ];
BEGIN
    IF v_email = '' OR position('@' in v_email) = 0 THEN
        RETURN NEW; -- phone / anonymous users
    END IF;

    -- Owner override allowlist
    IF EXISTS (SELECT 1 FROM public.signup_email_allowlist a WHERE a.email = v_email) THEN
        RETURN NEW;
    END IF;

    v_local  := split_part(v_email, '@', 1);
    v_domain := rtrim(split_part(v_email, '@', 2), '.');

    IF v_local = '' OR v_domain = '' THEN
        RAISE EXCEPTION 'VIDGRAB_INVALID_EMAIL: Invalid email address'
            USING ERRCODE = 'P0001';
    END IF;

    -- Rule 1: Block '+' sub-addresses (e.g. ten+1@gmail.com)
    IF position('+' in v_local) > 0 THEN
        RAISE EXCEPTION 'VIDGRAB_PLUS_EMAIL: Sub-address emails containing (+) are not allowed'
            USING ERRCODE = 'P0001',
                  HINT = 'Remove the +alias part and use your main email address.';
    END IF;

    -- Rule 2: Strict 5-provider domain whitelist
    IF NOT (v_domain = ANY (v_allowed)) THEN
        RAISE EXCEPTION 'VIDGRAB_DISPOSABLE_EMAIL: Email domain (%) is not in the allowed provider list', v_domain
            USING ERRCODE = 'P0001',
                  HINT = 'Only Gmail, Outlook/Hotmail, iCloud, Yahoo Mail, and ProtonMail are accepted.';
    END IF;

    -- Rule 3: Normalize Gmail dots (a.b.c@gmail.com -> abc@gmail.com) & prevent duplicate Gmail accounts
    IF v_domain IN ('gmail.com', 'googlemail.com') THEN
        v_norm_local := replace(v_local, '.', '');
        IF v_norm_local = '' THEN
            RAISE EXCEPTION 'VIDGRAB_INVALID_EMAIL: Invalid Gmail username'
                USING ERRCODE = 'P0001';
        END IF;

        IF EXISTS (
            SELECT 1
              FROM auth.users u
             WHERE u.id IS DISTINCT FROM NEW.id
               AND u.email IS NOT NULL
               AND rtrim(split_part(lower(u.email), '@', 2), '.') IN ('gmail.com', 'googlemail.com')
               AND replace(split_part(split_part(lower(u.email), '@', 1), '+', 1), '.', '') = v_norm_local
        ) THEN
            RAISE EXCEPTION 'VIDGRAB_DUPLICATE_GMAIL: A Gmail account with this canonical address already exists'
                USING ERRCODE = 'P0001',
                      HINT = 'Dots in Gmail addresses do not create a separate account.';
        END IF;

        NEW.email := v_norm_local || '@gmail.com';
    ELSE
        NEW.email := v_local || '@' || v_domain;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_block_disposable ON auth.users;
CREATE TRIGGER on_auth_user_block_disposable
    BEFORE INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.reject_disposable_email_signup();

COMMIT;
