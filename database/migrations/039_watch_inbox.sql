-- 039: Channel Watch inbox ("Hộp thư") — read / unread state (Phase 33E).
--
-- HOW TO RUN (owner): open the Supabase SQL editor for the production project,
-- paste this whole file, run it once. ADDITIVE and IDEMPOTENT — it creates one
-- new table and touches no existing one. NOT applied automatically by deploys.
--
-- Without this table the inbox still lists new videos (all shown as unread);
-- only "mark as read" answers 503 inbox_not_ready. Nothing else depends on it.
--
-- One row per user:
--   read_before    every item discovered at or before this instant is read
--                  ("Đánh dấu đã xem tất cả")
--   read_item_ids  ids of single items read after that (bounded by the
--                  backend to the 30-day inbox window, at most 1000 ids)
--
-- Access: RLS enabled with NO policy (service role only), same as 038.
-- user_id is UUID without a foreign key to auth.users (same as 038).

CREATE TABLE IF NOT EXISTS watch_inbox_state (
    user_id        UUID        PRIMARY KEY,
    read_before    TIMESTAMPTZ,
    read_item_ids  JSONB       NOT NULL DEFAULT '[]'::jsonb,
    updated_at     TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE watch_inbox_state ENABLE ROW LEVEL SECURITY;

-- VERIFY (after):
--   SELECT rowsecurity FROM pg_tables WHERE tablename = 'watch_inbox_state';  -- true
--   SELECT count(*) FROM pg_policies WHERE tablename = 'watch_inbox_state';   -- 0

-- ROLLBACK (drops read state only; videos and subscriptions stay):
--   DROP TABLE IF EXISTS watch_inbox_state;
