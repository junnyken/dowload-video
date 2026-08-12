-- Tạo tài khoản admin enterprise
-- Paste toàn bộ vào Supabase SQL Editor → Run

DO $$
DECLARE
  v_uid   UUID;
  v_email TEXT := 'thientrieu753@gmail.com';
BEGIN
  -- Kiểm tra đã tồn tại chưa
  SELECT id INTO v_uid FROM auth.users WHERE email = v_email;

  IF v_uid IS NULL THEN
    v_uid := gen_random_uuid();

    INSERT INTO auth.users (
      id, instance_id, aud, role, email,
      encrypted_password,
      email_confirmed_at,
      created_at, updated_at,
      raw_user_meta_data, raw_app_meta_data,
      is_super_admin, is_sso_user,
      confirmation_token, recovery_token,
      email_change_token_new, email_change
    ) VALUES (
      v_uid,
      '00000000-0000-0000-0000-000000000000',
      'authenticated', 'authenticated',
      v_email,
      crypt('Aquarius21!!', gen_salt('bf', 10)),
      NOW(), NOW(), NOW(),
      '{"display_name":"Admin"}'::jsonb,
      '{"provider":"email","providers":["email"]}'::jsonb,
      false, false,
      '', '', '', ''
    );

    RAISE NOTICE 'Auth user created: %', v_uid;
  ELSE
    RAISE NOTICE 'User already exists with id: %', v_uid;
  END IF;

  -- Tạo / cập nhật profile với tier enterprise
  INSERT INTO public.profiles (id, email, display_name, tier, created_at)
  VALUES (v_uid::TEXT, v_email, 'Admin', 'enterprise', NOW())
  ON CONFLICT (id) DO UPDATE SET tier = 'enterprise', email = v_email;

  -- Usage row
  INSERT INTO public.user_usage (user_id)
  VALUES (v_uid::TEXT)
  ON CONFLICT (user_id) DO NOTHING;

  -- Preferences row (ignore nếu lỗi FK)
  BEGIN
    INSERT INTO public.user_preferences (user_id)
    VALUES (v_uid)
    ON CONFLICT (user_id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN NULL;
  END;

  RAISE NOTICE '✅ Done — enterprise account: %', v_email;
END $$;
