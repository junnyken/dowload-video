-- Phase 14 Analytics & Attribution schema
-- Run against Supabase SQL editor (idempotent)

-- Raw event stream
CREATE TABLE IF NOT EXISTS analytics_events (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_name     TEXT NOT NULL,
  user_id        UUID,
  anonymous_id   TEXT,
  source         TEXT CHECK (source IN ('web','extension','telegram_bot','api')),
  properties     JSONB DEFAULT '{}',
  experiment_variants JSONB DEFAULT '{}',
  created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ae_name_ts  ON analytics_events (event_name, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ae_user     ON analytics_events (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ae_source   ON analytics_events (source, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ae_anon     ON analytics_events (anonymous_id, created_at DESC);

-- Aggregated daily metrics (flushed by cron)
CREATE TABLE IF NOT EXISTS analytics_daily (
  date        DATE    NOT NULL,
  metric      TEXT    NOT NULL,
  dimensions  JSONB   NOT NULL DEFAULT '{}',
  value       NUMERIC NOT NULL,
  PRIMARY KEY (date, metric, dimensions)
);

-- Lightweight A/B experiment flags
CREATE TABLE IF NOT EXISTS experiment_config (
  key         TEXT PRIMARY KEY,
  value       JSONB NOT NULL DEFAULT '{}',
  description TEXT,
  active      BOOLEAN DEFAULT TRUE,
  updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default experiments (upsert)
INSERT INTO experiment_config (key, value, description) VALUES
  ('paywall_copy_variant', '{"variant": "A", "cta_text": "Nâng cấp Pro ngay"}', 'Paywall CTA copy A/B')
ON CONFLICT (key) DO NOTHING;

INSERT INTO experiment_config (key, value, description) VALUES
  ('extension_prompt_timing', '{"trigger": "after_first_success", "delay_ms": 2000}', 'When to show extension install nudge')
ON CONFLICT (key) DO NOTHING;

INSERT INTO experiment_config (key, value, description) VALUES
  ('telegram_prompt_timing', '{"trigger": "after_third_success", "mobile_only": true}', 'When to show Telegram bot nudge')
ON CONFLICT (key) DO NOTHING;

-- Alert suppression state (prevent duplicate admin alerts)
CREATE TABLE IF NOT EXISTS alert_state (
  alert_key  TEXT PRIMARY KEY,
  last_fired TIMESTAMPTZ,
  fire_count INTEGER DEFAULT 0
);

-- Source attribution on download_jobs (add if missing)
ALTER TABLE download_jobs
  ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'web',
  ADD COLUMN IF NOT EXISTS platform TEXT,
  ADD COLUMN IF NOT EXISTS estimated_cost_cents NUMERIC DEFAULT 0;

CREATE INDEX IF NOT EXISTS ix_dj_source   ON download_jobs (source, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_dj_platform ON download_jobs (platform, created_at DESC);
