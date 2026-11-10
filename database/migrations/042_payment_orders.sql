-- ============================================================
-- Migration 042 — Payment Orders (Auto VietQR + PayPal)
-- ============================================================
-- Bảng đơn thanh toán tự động cho cả VietQR (SePay) và PayPal.
-- Mã đơn: VGP + 8 ký tự (khác mã tay VG + 8 hex).
-- Idempotent: chạy lại không lỗi.
-- ============================================================

-- ─── payment_orders ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS payment_orders (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          TEXT NOT NULL,                       -- auth UID → profiles.id
  plan             TEXT NOT NULL DEFAULT 'pro_month',   -- 'pro_month','pro_year'
  rail             TEXT NOT NULL DEFAULT 'vietqr',      -- 'vietqr','paypal','stripe'
  currency         TEXT NOT NULL DEFAULT 'VND',         -- 'VND','USD'
  amount           BIGINT NOT NULL,                     -- VND đồng / USD cents
  code             TEXT NOT NULL UNIQUE,                -- 'VGP' + 8 chars (Crockford base32)
  status           TEXT NOT NULL DEFAULT 'pending',     -- 'pending','paid','underpaid','expired','canceled','failed'
  paid_amount      BIGINT DEFAULT 0,
  paid_at          TIMESTAMPTZ,
  activated_at     TIMESTAMPTZ,
  expires_at       TIMESTAMPTZ,                         -- order TTL (24h default)
  provider_ref     TEXT,                                -- SePay tx id / PayPal order id
  evidence         JSONB DEFAULT '{}',                  -- sanitized payment evidence (no PII)
  admin_note       TEXT,
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_orders_user
  ON payment_orders (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payment_orders_status
  ON payment_orders (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_payment_orders_code
  ON payment_orders (code);

-- ─── Extend payment_events for order linking ─────────────────

ALTER TABLE payment_events
  ADD COLUMN IF NOT EXISTS order_id    UUID,
  ADD COLUMN IF NOT EXISTS match_type  TEXT,          -- 'matched','unmatched','duplicate','underpaid','late'
  ADD COLUMN IF NOT EXISTS amount      BIGINT;

-- ─── RLS ─────────────────────────────────────────────────────

ALTER TABLE payment_orders ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'payment_orders' AND policyname = 'payment_orders_own') THEN
    CREATE POLICY "payment_orders_own" ON payment_orders
      FOR SELECT USING (user_id = current_setting('request.jwt.claims', true)::json->>'sub');
  END IF;
END $$;
