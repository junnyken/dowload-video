-- Phase 19: PWA push subscriptions and notification preferences

CREATE TABLE IF NOT EXISTS push_subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  workspace_id UUID,
  endpoint TEXT NOT NULL,
  p256dh TEXT NOT NULL,
  auth_key TEXT NOT NULL,
  user_agent TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  last_used_at TIMESTAMPTZ DEFAULT NOW(),
  is_active BOOLEAN DEFAULT TRUE,
  UNIQUE(endpoint)
);

CREATE INDEX IF NOT EXISTS idx_push_sub_user
  ON push_subscriptions(user_id) WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS notification_preferences (
  user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  job_completed BOOLEAN DEFAULT TRUE,
  job_failed BOOLEAN DEFAULT TRUE,
  batch_completed BOOLEAN DEFAULT TRUE,
  storage_warning BOOLEAN DEFAULT TRUE,
  browser_push_enabled BOOLEAN DEFAULT FALSE,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_preferences ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'push_subscriptions' AND policyname = 'users_own_push_subs'
  ) THEN
    CREATE POLICY "users_own_push_subs" ON push_subscriptions
      FOR ALL USING (auth.uid() = user_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'notification_preferences' AND policyname = 'users_own_notif_prefs'
  ) THEN
    CREATE POLICY "users_own_notif_prefs" ON notification_preferences
      FOR ALL USING (auth.uid() = user_id);
  END IF;
END $$;
