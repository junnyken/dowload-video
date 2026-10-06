-- 035 — Users may not change their own plan or billing fields.
--
-- Found 2026-10-06 while adding per-platform download limits: migration 002
-- gives signed-in users the policy "User owns profile" (FOR ALL, id =
-- auth.uid()), so with the PUBLIC anon key and their own JWT a user can
-- PATCH /rest/v1/profiles and set tier = 'enterprise' (unlimited downloads,
-- Pro features). Not verified on the live database — run CHECK first.
--
-- Fix: a trigger rejects changes to the plan/billing columns unless the
-- request is NOT from a regular client role. The backend (service_role key),
-- the SQL editor (postgres) and the signup trigger keep working; the
-- "User owns profile" policy stays, so users can still edit other fields.
--
-- CHECK (run before, read-only) — shows the policy that allows self-edit:
--   SELECT policyname, cmd, roles, qual FROM pg_policies WHERE tablename = 'profiles';
--
-- Safe to run more than once.

CREATE OR REPLACE FUNCTION protect_profile_billing_columns()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    _role text := coalesce(auth.role(), '');
BEGIN
    IF _role NOT IN ('authenticated', 'anon') THEN
        RETURN NEW;            -- service_role, postgres, signup trigger
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF coalesce(NEW.tier, 'free') <> 'free'
           OR coalesce(NEW.billing_status, 'none') <> 'none'
           OR NEW.subscription_expiry IS NOT NULL
           OR NEW.stripe_customer_id IS NOT NULL
           OR NEW.stripe_subscription_id IS NOT NULL THEN
            RAISE EXCEPTION 'profile plan/billing fields can only be set by the server'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.tier IS DISTINCT FROM OLD.tier
       OR NEW.billing_status IS DISTINCT FROM OLD.billing_status
       OR NEW.subscription_expiry IS DISTINCT FROM OLD.subscription_expiry
       OR NEW.stripe_customer_id IS DISTINCT FROM OLD.stripe_customer_id
       OR NEW.stripe_subscription_id IS DISTINCT FROM OLD.stripe_subscription_id THEN
        RAISE EXCEPTION 'profile plan/billing fields can only be changed by the server'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS profiles_protect_billing ON public.profiles;
CREATE TRIGGER profiles_protect_billing
    BEFORE INSERT OR UPDATE ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION protect_profile_billing_columns();

-- VERIFY (after): who already has a paid tier? Review anything unexpected.
--   SELECT id, email, tier, billing_status, subscription_expiry
--   FROM public.profiles WHERE coalesce(tier, 'free') <> 'free' ORDER BY tier;
