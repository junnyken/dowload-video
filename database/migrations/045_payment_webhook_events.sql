-- ============================================================
-- Migration 045 — Payment Webhook Events (Manual Review & Audit)
-- ============================================================
-- Lưu các sự kiện webhook thanh toán cần kiểm tra thủ công (needs_manual_review),
-- ví dụ: giao dịch thanh toán gói thấp hơn khi gói cao hơn còn hạn, hoặc
-- chuyển khoản VG lệch giá gói > 1.000₫ (underpaid).
-- Idempotent: chạy lại nhiều lần không lỗi.
-- ============================================================

CREATE TABLE IF NOT EXISTS payment_webhook_events (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider          TEXT NOT NULL,                       -- 'stripe','paypal','paypal_ipn','sepay','payment_orders'
  provider_event_id TEXT,                                -- event id / txn id / sepay id
  event_type        TEXT,
  user_id           TEXT,                                -- auth UID -> profiles.id
  order_id          TEXT,
  current_tier      TEXT,
  target_tier       TEXT,
  amount            BIGINT,
  status            TEXT NOT NULL DEFAULT 'needs_manual_review',
  reason            TEXT,
  raw_payload       JSONB DEFAULT '{}',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_webhook_events_status
  ON payment_webhook_events (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_payment_webhook_events_user
  ON payment_webhook_events (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_payment_webhook_events_provider_event
  ON payment_webhook_events (provider, provider_event_id);

-- ─── RLS (Service Role Only) ─────────────────────────────────

ALTER TABLE payment_webhook_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'payment_webhook_events'
      AND policyname = 'payment_webhook_events_service_role_only'
  ) THEN
    CREATE POLICY "payment_webhook_events_service_role_only" ON payment_webhook_events
      FOR ALL
      TO service_role
      USING (true)
      WITH CHECK (true);
  END IF;
END $$;
