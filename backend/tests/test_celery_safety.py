"""
Unit tests for Phase 2 Celery safety fixes (`fix/celery-safety`):
1. `flush_event_buffer`: atomic batch pop (`LPOP key count` + `MULTI/EXEC` fallback)
   and FIFO re-queue on DB insert failure.
2. Daily Redis `SET NX EX` locks across `expire_subscriptions`, `reset_daily_quota`,
   `backup_database_daily`, and periodic Telegram report tasks (`daily_summary_report`,
   `send_phase14_daily_digest`, `check_cookie_expiry`, `send_weekly_metrics_report`).
3. `expire_subscriptions`: conditional `UPDATE ... WHERE subscription_expiry < now()`
   preventing downgrade when a concurrent renewal extends `subscription_expiry`
   or flips `billing_status` to `active` between `SELECT` and `UPDATE`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import fakeredis
import pytest

from app.core import periodic_lock


@pytest.fixture()
def fake_rc(monkeypatch):
    rc = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(periodic_lock, "_get_redis", lambda: rc)
    monkeypatch.setattr("app.core.redis_client.get_redis", lambda: rc)
    return rc


# ── 1. flush_event_buffer atomic batch pop ───────────────────────────────────


class TestFlushEventBufferAtomic:
    def test_lpop_count_flushes_exact_batches_without_loss_or_duplication(self, monkeypatch):
        from app.tasks import analytics_tasks as at

        rc = fakeredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr("redis.from_url", lambda *_a, **_kw: rc)

        inserted_batches: list[list[dict]] = []

        class _FakeTable:
            def insert(self, rows):
                inserted_batches.append(list(rows))
                return self

            def execute(self):
                return MagicMock(data=[])

        fake_sb = MagicMock()
        fake_sb.table.return_value = _FakeTable()
        monkeypatch.setattr("app.core.database.get_supabase_client", lambda: fake_sb)

        for i in range(750):
            rc.rpush(at._EVENT_BUFFER_KEY, json.dumps({"event_name": f"ev_{i}", "ts": 1700000000 + i}))

        at.flush_event_buffer()
        assert rc.llen(at._EVENT_BUFFER_KEY) == 250
        assert len(inserted_batches) == 1
        assert len(inserted_batches[0]) == 500
        assert inserted_batches[0][0]["event_name"] == "ev_0"
        assert inserted_batches[0][-1]["event_name"] == "ev_499"

        # Push 10 new events concurrently before the 2nd flush
        for j in range(750, 760):
            rc.rpush(at._EVENT_BUFFER_KEY, json.dumps({"event_name": f"ev_{j}", "ts": 1700000000 + j}))

        at.flush_event_buffer()
        assert rc.llen(at._EVENT_BUFFER_KEY) == 0
        assert len(inserted_batches) == 2
        assert len(inserted_batches[1]) == 260
        assert inserted_batches[1][0]["event_name"] == "ev_500"
        assert inserted_batches[1][-1]["event_name"] == "ev_759"

    def test_fallback_uses_multi_exec_pipeline_when_lpop_count_unsupported(self):
        from app.tasks.analytics_tasks import _pop_event_batch

        real_rc = fakeredis.FakeRedis(decode_responses=True)
        for i in range(5):
            real_rc.rpush("vg:events:buffer", f"item-{i}")

        pipeline_calls = []

        class _LegacyRedisProxy:
            def lpop(self, key, *args):
                if args:
                    raise TypeError("lpop() takes 1 positional argument but 2 were given")
                return real_rc.lpop(key)

            def pipeline(self, transaction=True):
                pipeline_calls.append(transaction)
                return real_rc.pipeline(transaction=transaction)

        batch = _pop_event_batch(_LegacyRedisProxy(), "vg:events:buffer", batch_size=3)
        assert batch == ["item-0", "item-1", "item-2"]
        assert pipeline_calls == [True], "Fallback must use MULTI/EXEC transaction=True"
        assert real_rc.lrange("vg:events:buffer", 0, -1) == ["item-3", "item-4"]

    def test_db_failure_requeues_popped_events_in_fifo_order(self, monkeypatch):
        from app.tasks import analytics_tasks as at

        rc = fakeredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr("redis.from_url", lambda *_a, **_kw: rc)

        for i in range(3):
            rc.rpush(at._EVENT_BUFFER_KEY, json.dumps({"event_name": f"ev_{i}", "ts": 1700000000 + i}))
        rc.rpush(at._EVENT_BUFFER_KEY, json.dumps({"event_name": "ev_tail", "ts": 1700000099}))
        monkeypatch.setattr(at, "_EVENT_BATCH_SIZE", 3)

        fake_sb = MagicMock()
        fake_sb.table.return_value.insert.side_effect = RuntimeError("db down")
        monkeypatch.setattr("app.core.database.get_supabase_client", lambda: fake_sb)

        at.flush_event_buffer()
        remaining = [json.loads(x)["event_name"] for x in rc.lrange(at._EVENT_BUFFER_KEY, 0, -1)]
        assert remaining == ["ev_0", "ev_1", "ev_2", "ev_tail"]


# ── 2. Daily Redis SET NX EX idempotency locks ───────────────────────────────


class TestDailyPeriodicLocks:
    def test_set_nx_ex_ttl_and_safe_token_release(self, fake_rc):
        d1 = datetime(2026, 11, 11, 0, 5, tzinfo=timezone.utc)
        d2 = datetime(2026, 11, 12, 0, 5, tzinfo=timezone.utc)

        ok1, key1, tok1 = periodic_lock.acquire_daily_lock("sample_task", now=d1)
        assert ok1 is True and tok1 is not None
        assert key1 == "vidgrab:daily_lock:sample_task:2026-11-11"
        ttl = fake_rc.ttl(key1)
        assert 89_000 < ttl <= periodic_lock.DAILY_LOCK_TTL_SEC

        # Second acquire on same UTC date is blocked
        ok2, _, tok2 = periodic_lock.acquire_daily_lock("sample_task", now=d1)
        assert ok2 is False and tok2 is None

        # Next UTC date succeeds independently
        ok_next, key_next, _ = periodic_lock.acquire_daily_lock("sample_task", now=d2)
        assert ok_next is True and key_next.endswith(":2026-11-12")

        # Wrong token cannot release d1's lock; owner token can
        assert periodic_lock.release_daily_lock(key1, "wrong-token") is False
        assert periodic_lock.release_daily_lock(key1, tok1) is True
        ok_retry, _, _ = periodic_lock.acquire_daily_lock("sample_task", now=d1)
        assert ok_retry is True

    def test_reset_daily_quota_runs_once_per_day_and_retries_if_db_fails(self, fake_rc, monkeypatch):
        from app.tasks import video_tasks as vt

        calls = []

        class _UsageQuery:
            def update(self, payload):
                calls.append(payload)
                return self

            def neq(self, *_a):
                return self

            def execute(self):
                return MagicMock(data=[{"user_id": "u1"}, {"user_id": "u2"}])

        sb = MagicMock()
        sb.table.return_value = _UsageQuery()
        monkeypatch.setattr(vt, "get_supabase_client", lambda: sb)

        r1 = vt.reset_daily_quota()
        r2 = vt.reset_daily_quota()
        assert r1 == {"reset_count": 2}
        assert r2 == {"skipped": "duplicate_daily_run"}
        assert len(calls) == 1

    def test_backup_database_daily_deduplicates_same_day_and_releases_on_crash(self, fake_rc, monkeypatch):
        from app.tasks import backup_tasks as bt

        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc-key")
        monkeypatch.delenv("BACKUP_PG_DSN", raising=False)
        monkeypatch.delenv("BACKUP_S3_ENDPOINT", raising=False)
        monkeypatch.setattr("app.core.database.using_postgres", lambda: False)
        monkeypatch.setattr(bt, "_list_tables", lambda *_a: ["profiles"])
        monkeypatch.setattr(bt, "_dump_table", lambda *_a: ([{"id": "u1"}], None))
        monkeypatch.setattr(bt, "_dump_auth_users", lambda *_a: ([{"id": "u1"}], None))
        monkeypatch.setattr(bt, "_ensure_bucket", lambda *_a: None)
        uploads = []
        monkeypatch.setattr(bt, "_upload", lambda *_a: uploads.append(_a[3]))
        monkeypatch.setattr(bt, "_prune", lambda *_a: 0)
        monkeypatch.setattr(bt, "_backup_redis_config", lambda *_a, **_kw: {"ok": True, "keys": 1, "bytes": 64, "warnings": []})
        monkeypatch.setattr("app.core.notifications.send_telegram_message_sync", lambda *_a, **_kw: True)

        first = bt.backup_database_daily()
        second = bt.backup_database_daily()
        assert first["ok"] is True and "file" in first
        assert second == {"ok": True, "skipped": "duplicate_daily_run"}
        assert len(uploads) == 1

    def test_periodic_telegram_reports_run_at_most_once_per_day(self, fake_rc, monkeypatch):
        from app.tasks import analytics_tasks as at
        from app.tasks import video_tasks as vt
        from app.services import unit_metrics as um

        tg_messages: list[str] = []
        monkeypatch.setattr("app.core.notifications.send_telegram_message_sync", lambda msg, **_kw: tg_messages.append(msg) or True)
        monkeypatch.setattr("app.core.notifications.send_daily_summary_sync", lambda: tg_messages.append("daily_summary") or True)

        # 1) daily_summary_report
        assert vt.daily_summary_report() == {"sent": True}
        assert vt.daily_summary_report() == {"skipped": "duplicate_daily_run"}
        assert tg_messages.count("daily_summary") == 1

        # 2) send_phase14_daily_digest
        sb = MagicMock()
        sb.table.return_value.select.return_value.gte.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{"status": "success", "platform": "tiktok", "source": "web", "error_message": None}]
        )
        sb.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(count=3)
        monkeypatch.setattr("app.core.database.get_supabase_client", lambda: sb)

        assert at.send_phase14_daily_digest() == {"sent": True}
        assert at.send_phase14_daily_digest() == {"skipped": "duplicate_daily_run"}
        assert sum(1 for m in tg_messages if "VidGrab Daily Digest" in m) == 1

        # 3) check_cookie_expiry
        monkeypatch.setattr("app.core.cookie_pool.pooled_platforms", lambda: ["youtube"])
        monkeypatch.setattr(
            "app.core.cookie_pool.get_expiry_report",
            lambda _p: [{"expiry_status": "expired", "label": "yt1", "hash": "abcdef1234", "days_left": 0, "expires_at_str": "2026-11-10"}],
        )
        assert vt.check_cookie_expiry() == {"sent": True}
        assert vt.check_cookie_expiry() == {"skipped": "duplicate_daily_run"}
        assert sum(1 for m in tg_messages if "Cookie Expiry Alert" in m) == 1

        # 4) send_weekly_metrics_report
        monkeypatch.setattr(um, "_r", lambda: fake_rc)
        um.set_weekly_report_enabled(True)
        r_w1 = at.send_weekly_metrics_report()
        r_w2 = at.send_weekly_metrics_report()
        assert r_w1["sent"] is True
        assert r_w2["skipped"] == "duplicate_daily_run"


# ── 3. expire_subscriptions conditional UPDATE (WHERE subscription_expiry < now()) ──


class _StatefulSubscriptionDB:
    """In-memory DB double that enforces `.eq()`, `.in_()`, and `.lt()` at execute()
    time and supports a hook right before `profiles` UPDATE to simulate a concurrent
    payment webhook renewing a subscription between SELECT and UPDATE."""

    def __init__(self, profiles: list[dict], usage: list[dict], before_profile_update=None):
        self.profiles = {r["id"]: dict(r) for r in profiles}
        self.usage = {r["user_id"]: dict(r) for r in usage}
        self.before_profile_update = before_profile_update
        self.profile_update_filters: list[list[tuple]] = []

    def table(self, name: str):
        return _StatefulQuery(self, name)


class _StatefulQuery:
    def __init__(self, db: _StatefulSubscriptionDB, name: str):
        self.db = db
        self.name = name
        self.action = "select"
        self.payload = None
        self.filters: list[tuple] = []

    def select(self, *_cols):
        self.action = "select"
        return self

    def update(self, payload: dict):
        self.action = "update"
        self.payload = dict(payload)
        return self

    def eq(self, col: str, val):
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col: str, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def lt(self, col: str, val):
        self.filters.append(("lt", col, val))
        return self

    def _matches(self, row: dict) -> bool:
        for op, col, val in self.filters:
            rv = row.get(col)
            if op == "eq" and rv != val:
                return False
            if op == "in" and rv not in val:
                return False
            if op == "lt":
                if rv is None:
                    return False
                if datetime.fromisoformat(rv) >= datetime.fromisoformat(val):
                    return False
        return True

    def execute(self):
        store = self.db.profiles if self.name == "profiles" else self.db.usage
        if self.action == "select":
            matched = [dict(r) for r in store.values() if self._matches(r)]
            return MagicMock(data=matched)

        if self.name == "profiles" and self.db.before_profile_update:
            uid = next((v for op, c, v in self.filters if op == "eq" and c == "id"), None)
            self.db.before_profile_update(self.db, uid)
        if self.name == "profiles":
            self.db.profile_update_filters.append(list(self.filters))

        updated = []
        for row in store.values():
            if self._matches(row):
                row.update(self.payload)
                updated.append(dict(row))
        return MagicMock(data=updated)


class TestExpireSubscriptionsConditionalUpdate:
    def test_does_not_downgrade_user_renewed_between_select_and_update(self, fake_rc, monkeypatch):
        from app.tasks import video_tasks as vt

        past_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        future_iso = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        def _concurrent_webhook_renewal(db: _StatefulSubscriptionDB, uid: str | None):
            if uid == "u-renewed-expiry":
                # Webhook extended subscription_expiry into the future while keeping manual status
                db.profiles[uid]["subscription_expiry"] = future_iso
            elif uid == "u-renewed-active":
                # Stripe webhook flipped billing_status to active
                db.profiles[uid]["billing_status"] = "active"
                db.profiles[uid]["subscription_expiry"] = future_iso

        db = _StatefulSubscriptionDB(
            profiles=[
                {"id": "u-expired", "tier": "pro", "billing_status": "manual", "subscription_expiry": past_iso, "stripe_subscription_id": None},
                {"id": "u-renewed-expiry", "tier": "pro", "billing_status": "manual", "subscription_expiry": past_iso, "stripe_subscription_id": None},
                {"id": "u-renewed-active", "tier": "team", "billing_status": "past_due", "subscription_expiry": past_iso, "stripe_subscription_id": "sub_1"},
            ],
            usage=[
                {"user_id": "u-expired", "plan": "pro"},
                {"user_id": "u-renewed-expiry", "plan": "pro"},
                {"user_id": "u-renewed-active", "plan": "team"},
            ],
            before_profile_update=_concurrent_webhook_renewal,
        )
        monkeypatch.setattr(vt, "get_supabase_client", lambda: db)

        out = vt.expire_subscriptions()
        assert out == {"processed": 3, "downgraded": 1, "skipped_renewed": 2, "failed": 0}

        # Every UPDATE must carry (`eq id`, `in billing_status`, `lt subscription_expiry`)
        for flt in db.profile_update_filters:
            ops = {(op, col) for op, col, _ in flt}
            assert ("eq", "id") in ops
            assert ("in", "billing_status") in ops
            assert ("lt", "subscription_expiry") in ops

        # Truly expired user is downgraded
        assert db.profiles["u-expired"]["tier"] == "free"
        assert db.profiles["u-expired"]["billing_status"] == "canceled"
        assert db.usage["u-expired"]["plan"] == "free"

        # Concurrently renewed users keep their paid plans and expiry intact
        assert db.profiles["u-renewed-expiry"]["tier"] == "pro"
        assert db.profiles["u-renewed-expiry"]["subscription_expiry"] == future_iso
        assert db.usage["u-renewed-expiry"]["plan"] == "pro"

        assert db.profiles["u-renewed-active"]["tier"] == "team"
        assert db.profiles["u-renewed-active"]["billing_status"] == "active"
        assert db.usage["u-renewed-active"]["plan"] == "team"

        # Duplicate beat dispatch on the same UTC day is blocked by SET NX EX
        assert vt.expire_subscriptions() == {"skipped": "duplicate_daily_run"}

    def test_pg_client_compiles_subscription_expiry_lt_into_update_where_clause(self):
        """Verify PgClient._exec_update emits `WHERE ... AND ("subscription_expiry" < %s::timestamptz)`."""
        from app.core.pg_client import Query

        executed: list[tuple[str, list]] = []

        class _FakePool:
            def table_columns(self, _table):
                return {
                    "id": "uuid",
                    "tier": "text",
                    "billing_status": "text",
                    "subscription_expiry": "timestamptz",
                    "stripe_subscription_id": "text",
                }

            def run(self, sql, params, fetch=True):
                executed.append((sql, list(params)))
                return []

        now_iso = datetime.now(timezone.utc).isoformat()
        (
            Query(_FakePool(), "profiles")
            .update({
                "tier": "free",
                "billing_status": "canceled",
                "subscription_expiry": None,
                "stripe_subscription_id": None,
            })
            .eq("id", "00000000-0000-0000-0000-000000000001")
            .in_("billing_status", ["canceling", "past_due", "manual"])
            .lt("subscription_expiry", now_iso)
            .execute()
        )
        assert len(executed) == 1
        sql, params = executed[0]
        assert sql.startswith('UPDATE public."profiles" AS t SET ')
        assert '("subscription_expiry" < %s::timestamptz)' in sql
        assert '("billing_status" = ANY(%s::text[]))' in sql
        assert params[-1] == now_iso
