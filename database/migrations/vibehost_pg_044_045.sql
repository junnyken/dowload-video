-- ============================================================================
-- Migrations 044 + 045 — Bản tham khảo PostgreSQL thuần trên Vibe Host
-- Target DB: vidgrab-backup (PostgreSQL 13+, mặc định PG 16 trên Vibe Host)
-- LƯU Ý: Từ đợt phát hành fix/post-deploy-2026-11-11, backend đã TỰ ĐỘNG tạo
-- 2 bảng này lúc khởi động qua `app.core.migrations_044_045.ensure_044_045_once_on_startup()`.
-- File SQL này được giữ lại làm tài liệu tham khảo / chạy tay khi cần kiểm tra.
-- ============================================================================
-- Điểm khác biệt so với chạy trực tiếp 044 + 045 gốc trên Supabase:
-- 1. gen_random_uuid() là hàm core từ PostgreSQL 13+ (không cần cài pgcrypto).
-- 2. Trên Postgres thuần của Vibe Host, user kết nối qua DATABASE_URL không có
--    quyền CREATEROLE nên role `service_role` của Supabase thường KHÔNG tồn tại
--    (xem backend/app/core/db_migration.py:253-256). Chạy nguyên văn 045 gốc sẽ
--    lỗi `ERROR: role "service_role" does not exist` ở mệnh đề `TO service_role`.
-- 3. Bản này kiểm tra `pg_roles` trước khi gắn policy `TO service_role`.
--    Khi `DB_BACKEND=postgres`, backend kết nối bằng chính role sở hữu bảng
--    (table owner qua DATABASE_URL); PostgreSQL mặc định cho phép table owner
--    bypass RLS (trừ khi bật FORCE ROW LEVEL SECURITY), trong khi các role khác
--    vẫn bị chặn mặc định (default-deny) nhờ ENABLE ROW LEVEL SECURITY.
-- 4. Chạy trọn gói trong 1 transaction (BEGIN ... COMMIT), idempotent.
-- ============================================================================

BEGIN;

-- ─── 044: flow_consent_audit_logs ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.flow_consent_audit_logs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     TEXT NOT NULL,
  temp_id     TEXT NOT NULL,
  action      TEXT NOT NULL DEFAULT 'process_confirm',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flow_consent_audit_logs_user
  ON public.flow_consent_audit_logs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_flow_consent_audit_logs_temp_id
  ON public.flow_consent_audit_logs (temp_id);

ALTER TABLE public.flow_consent_audit_logs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'flow_consent_audit_logs'
      AND policyname = 'flow_consent_audit_logs_own'
  ) THEN
    CREATE POLICY "flow_consent_audit_logs_own" ON public.flow_consent_audit_logs
      FOR SELECT
      USING (user_id = current_setting('request.jwt.claims', true)::json->>'sub');
  END IF;
END $$;

-- ─── 045: payment_webhook_events ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.payment_webhook_events (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider          TEXT NOT NULL,
  provider_event_id TEXT,
  event_type        TEXT,
  user_id           TEXT,
  order_id          TEXT,
  current_tier      TEXT,
  target_tier       TEXT,
  amount            BIGINT,
  status            TEXT NOT NULL DEFAULT 'needs_manual_review',
  reason            TEXT,
  raw_payload       JSONB DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_webhook_events_status
  ON public.payment_webhook_events (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_payment_webhook_events_user
  ON public.payment_webhook_events (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_payment_webhook_events_provider_event
  ON public.payment_webhook_events (provider, provider_event_id);

ALTER TABLE public.payment_webhook_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname = 'public'
        AND tablename = 'payment_webhook_events'
        AND policyname = 'payment_webhook_events_service_role_only'
    ) THEN
      CREATE POLICY "payment_webhook_events_service_role_only"
        ON public.payment_webhook_events
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
    END IF;
  END IF;
END $$;

COMMIT;

-- ─── Kiểm tra sau khi chạy (CHỈ ĐỌC) ─────────────────────────────────────────

SELECT t.table_name,
       pg_t.tableowner,
       pg_t.rowsecurity AS rls_enabled
  FROM information_schema.tables t
  JOIN pg_tables pg_t
    ON pg_t.schemaname = t.table_schema
   AND pg_t.tablename  = t.table_name
 WHERE t.table_schema = 'public'
   AND t.table_name IN ('flow_consent_audit_logs', 'payment_webhook_events')
 ORDER BY t.table_name;

SELECT tablename, indexname, indexdef
  FROM pg_indexes
 WHERE schemaname = 'public'
   AND tablename IN ('flow_consent_audit_logs', 'payment_webhook_events')
 ORDER BY tablename, indexname;

SELECT tablename, policyname, roles, cmd
  FROM pg_policies
 WHERE schemaname = 'public'
   AND tablename IN ('flow_consent_audit_logs', 'payment_webhook_events')
 ORDER BY tablename, policyname;
