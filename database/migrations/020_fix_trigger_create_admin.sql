-- ============================================================
-- Migration 020: Fix trigger (silent) + tạo tài khoản admin
-- Chạy trong Supabase Dashboard → SQL Editor
-- ============================================================

-- ── 1. Rewrite trigger — mọi lỗi đều bị catch, KHÔNG BAO GIỜ block signup ──

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  BEGIN
    INSERT INTO public.profiles (id, email, tier, created_at)
    VALUES (NEW.id::TEXT, NEW.email, 'free', NOW())
    ON CONFLICT (id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN NULL;
  END;

  BEGIN
    INSERT INTO public.user_preferences (user_id)
    VALUES (NEW.id)
    ON CONFLICT (user_id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN NULL;
  END;

  BEGIN
    INSERT INTO public.user_usage (user_id)
    VALUES (NEW.id::TEXT)
    ON CONFLICT (user_id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN NULL;
  END;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.create_user_usage_row()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  BEGIN
    INSERT INTO public.user_usage (user_id)
    VALUES (NEW.id)
    ON CONFLICT (user_id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN NULL;
  END;
  RETURN NEW;
END;
$$;

-- ── 2. Tạo tài khoản admin enterprise ────────────────────────────────────────

DO $$
DECLARE
  v_uid  UUID;
  v_email TEXT := 'thientrieu753@gmail.com';
  v_pass  TEXT := 'Aquarius21!!';
BEGIN
  -- Kiểm tra email đã tồn tại chưa
  SELECT id INTO v_uid FROM auth.users WHERE email = v_email LIMIT 1;

  IF v_uid IS NULL THEN
    v_uid := gen_random_uuid();

    -- No DISABLE/ENABLE TRIGGER here: on a Supabase-managed project, auth.users
    -- is owned by supabase_auth_admin, not postgres (the SQL Editor's role) —
    -- ALTER TABLE on it fails with "must be owner of table users" even though
    -- INSERT is allowed. Not needed anyway: handle_new_user() (redefined above
    -- in this same file) wraps every insert in its own BEGIN/EXCEPTION block
    -- with ON CONFLICT DO NOTHING, so letting it fire here is harmless — it
    -- just does the same profile/preferences/usage inserts this block already
    -- does explicitly below.
    INSERT INTO auth.users (
      id, instance_id, aud, role, email,
      encrypted_password,
      email_confirmed_at, confirmation_sent_at,
      created_at, updated_at,
      raw_user_meta_data, raw_app_meta_data,
      is_super_admin, is_sso_user,
      phone, phone_confirmed_at,
      confirmation_token, recovery_token,
      email_change_token_new, email_change
    ) VALUES (
      v_uid,
      '00000000-0000-0000-0000-000000000000',
      'authenticated', 'authenticated',
      v_email,
      crypt(v_pass, gen_salt('bf', 10)),
      NOW(), NOW(), NOW(), NOW(),
      jsonb_build_object('display_name', 'Admin'),
      jsonb_build_object('provider', 'email', 'providers', ARRAY['email']),
      false, false,
      NULL, NULL, '', '', '', ''
    );

    RAISE NOTICE 'Auth user created: %', v_uid;
  ELSE
    RAISE NOTICE 'User already exists: %', v_uid;
  END IF;

  -- Tạo / cập nhật profile với tier enterprise
  INSERT INTO public.profiles (id, email, display_name, tier, created_at)
  VALUES (v_uid::TEXT, v_email, 'Admin', 'enterprise', NOW())
  ON CONFLICT (id) DO UPDATE SET tier = 'enterprise', email = v_email;

  -- Tạo user_usage nếu chưa có
  INSERT INTO public.user_usage (user_id)
  VALUES (v_uid::TEXT)
  ON CONFLICT (user_id) DO NOTHING;

  -- Tạo user_preferences nếu chưa có
  BEGIN
    INSERT INTO public.user_preferences (user_id)
    VALUES (v_uid)
    ON CONFLICT (user_id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN NULL;
  END;

  RAISE NOTICE '✅ Done. Email: %, Tier: enterprise', v_email;
END $$;
