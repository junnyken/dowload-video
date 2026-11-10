-- ============================================================
-- Migration 044 — Flow Cleanup Consent Audit Logs (P5.6.6)
-- ============================================================
-- Lưu bản ghi xác nhận quyền sử dụng & chỉnh sửa video (user_id, temp_id, action, created_at)
-- trong bảng DB thay vì lưu file trong downloads/.
-- Tuyệt đối không lưu tên file gốc, đường dẫn hay dữ liệu khung hình video.
-- Idempotent: chạy lại không lỗi.
-- ============================================================

CREATE TABLE IF NOT EXISTS flow_consent_audit_logs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     TEXT NOT NULL,
  temp_id     TEXT NOT NULL,
  action      TEXT NOT NULL DEFAULT 'process_confirm',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flow_consent_audit_logs_user
  ON flow_consent_audit_logs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_flow_consent_audit_logs_temp_id
  ON flow_consent_audit_logs (temp_id);

-- ─── RLS ─────────────────────────────────────────────────────

ALTER TABLE flow_consent_audit_logs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'flow_consent_audit_logs'
      AND policyname = 'flow_consent_audit_logs_own'
  ) THEN
    CREATE POLICY "flow_consent_audit_logs_own" ON flow_consent_audit_logs
      FOR SELECT USING (user_id = current_setting('request.jwt.claims', true)::json->>'sub');
  END IF;
END $$;
