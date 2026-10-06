"""
Per-platform daily download allowance (owner decision 2026-10-06).

"Khách chưa đăng ký 5 lượt/ngày mỗi nền tảng; tài khoản đăng ký 20 lượt/ngày
mỗi nền tảng; admin không giới hạn — cho mọi nền tảng." The China access
layer's per-person paid-path limits stay 5/20 and admin becomes unlimited;
platform-wide call caps default to unlimited while the money ceilings stay.

These drive the real quota module, /fetch-link, /bulk-download, the worker
task, the container queue helper, the usage endpoints and the China budget
guard against an in-memory Redis.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.core import quotas
from app.core.quotas import QuotaRequester, REQ_ADMIN, REQ_ANON, REQ_USER

# Shared fixtures: fakeredis singleton, real route with a fake Supabase,
# outcome recording inline, and the worker harness.
from tests.test_admin_download_metrics import (  # noqa: F401
    rc, route, sync_outcomes, _ok_extractor, _fail_extractor,
)
from tests.test_download_retry_storm import worker  # noqa: F401

IP = "198.51.100.23"
TT = "https://www.tiktok.com/@x/video/{}"
IG = "https://www.instagram.com/p/{}/"


@pytest.fixture(autouse=True)
def _default_limits(monkeypatch):
    for k in ("PLATFORM_DAILY_LIMIT_ANON", "PLATFORM_DAILY_LIMIT_USER"):
        monkeypatch.delenv(k, raising=False)
    # The tests in this file were written for one allowance PER platform; the
    # owner then chose one allowance for ALL platforms (the default). They
    # still guard the per-platform mode (QUOTA_SCOPE=per_platform); the total
    # mode has its own tests in test_quota_total_scope.py.
    monkeypatch.setenv("QUOTA_SCOPE", "per_platform")


def anon(ip=IP):
    return QuotaRequester(REQ_ANON, ip)


def user(uid="u-1", tier="free"):
    return QuotaRequester(REQ_USER, uid, tier=tier)


def use(rq, platform, n, start=0):
    for i in range(start, start + n):
        assert quotas.record_platform_download(rq, platform, f"https://{platform}.example/{i}")


class _FrozenDT(datetime):
    frozen: datetime = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.frozen.astimezone(tz) if tz else cls.frozen.replace(tzinfo=None)


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(quotas, "datetime", _FrozenDT)

    def set_(dt):
        _FrozenDT.frozen = dt
    yield set_
    _FrozenDT.frozen = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def tz(monkeypatch):
    """Run under a given process TZ (restored afterwards)."""
    def set_(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield set_
    monkeypatch.undo()
    time.tzset()


# ── 1. Limits by requester ─────────────────────────────────────────────────

class TestLimits:

    def test_defaults(self):
        assert quotas.platform_limit(anon()) == 5
        assert quotas.platform_limit(user()) == 20
        assert quotas.platform_limit(QuotaRequester(REQ_ADMIN)) == -1

    def test_paid_tiers_keep_their_larger_limits_and_never_get_less_than_free(self, monkeypatch):
        assert quotas.platform_limit(user(tier="pro")) == 100       # PRO_DAILY_LIMIT
        assert quotas.platform_limit(user(tier="team")) == 500      # TEAM_DAILY_LIMIT
        assert quotas.platform_limit(user(tier="enterprise")) == -1
        monkeypatch.setenv("PLATFORM_DAILY_LIMIT_USER", "250")
        assert quotas.platform_limit(user(tier="pro")) == 250       # never below free
        assert quotas.platform_limit(user(tier="team")) == 500

    def test_free_daily_limit_1000_on_prod_does_not_leak_into_per_platform(self, monkeypatch):
        monkeypatch.setitem(quotas.TIER_PERMISSIONS["free"], "daily_limit", 1000)
        assert quotas.platform_limit(user()) == 20
        assert quotas.platform_limit(user(tier="pro")) == 100

    def test_unknown_tier_is_free(self):
        assert quotas.platform_limit(user(tier="admin")) == 20

    @pytest.mark.parametrize("raw", ["-1", "-5"])
    def test_minus_one_is_unlimited(self, rc, monkeypatch, raw):
        monkeypatch.setenv("PLATFORM_DAILY_LIMIT_ANON", raw)
        monkeypatch.setenv("PLATFORM_DAILY_LIMIT_USER", raw)
        assert quotas.platform_limit(anon()) == -1
        assert quotas.platform_limit(user()) == -1
        assert quotas.platform_limit(user(tier="pro")) == -1
        use(anon(), "tiktok", 50)
        q = quotas.check_platform_quota(anon(), "tiktok")
        assert q["allowed"] and q["daily_limit"] == -1 and q["remaining"] == -1

    def test_anon_message_mentions_unlimited_signin_when_users_are(self, rc, monkeypatch):
        monkeypatch.setenv("PLATFORM_DAILY_LIMIT_USER", "-1")
        use(anon(), "tiktok", 5)
        msg = quotas.check_platform_quota(anon(), "tiktok")["message"]
        assert msg == "Khách tải được tối đa 5 lượt/ngày cho TikTok. Đăng nhập để tải không giới hạn."


# ── 2. Counting ────────────────────────────────────────────────────────────

class TestCounting:

    def test_guest_5_per_platform_and_platforms_are_independent(self, rc):
        use(anon(), "tiktok", 5)
        q = quotas.check_platform_quota(anon(), "tiktok")
        assert not q["allowed"]
        assert q["error_code"] == "quota_exceeded_daily"
        assert q["downloads_today"] == 5 and q["daily_limit"] == 5 and q["remaining"] == 0
        assert q["message"] == ("Khách tải được tối đa 5 lượt/ngày cho TikTok. "
                                "Đăng nhập để tải 20 lượt/ngày.")
        for p in ("youtube", "instagram", "facebook", "douyin", "other"):
            assert quotas.check_platform_quota(anon(), p)["allowed"], p
        # another guest is unaffected
        assert quotas.check_platform_quota(anon("203.0.113.9"), "tiktok")["allowed"]

    def test_signed_in_20_per_platform(self, rc, clock):
        u = user()
        use(u, "tiktok", 19)
        assert quotas.check_platform_quota(u, "tiktok")["allowed"]
        use(u, "tiktok", 1, start=19)
        q = quotas.check_platform_quota(u, "tiktok")
        assert not q["allowed"] and q["daily_limit"] == 20
        assert q["message"] == ("Bạn đã dùng hết 20 lượt hôm nay cho TikTok. "
                                "Lượt mới được cộng lại lúc 07:00.")
        assert quotas.check_platform_quota(u, "youtube")["allowed"]
        # the user's counter and the guest counter of the same person's IP are separate
        assert quotas.check_platform_quota(anon(), "tiktok")["allowed"]

    def test_admin_is_never_counted_nor_refused(self, rc):
        a = QuotaRequester(REQ_ADMIN)
        for i in range(200):
            assert quotas.check_platform_quota(a, "tiktok")["allowed"]
            assert quotas.record_platform_download(a, "tiktok", TT.format(i)) is False
        assert rc.keys("vidgrab:quota:plat*") == []

    def test_same_url_same_day_counts_once_and_is_never_refused(self, rc):
        a = anon()
        for _ in range(3):   # analyse, then download in two qualities
            quotas.record_platform_download(a, "tiktok", TT.format(1))
        assert quotas.platform_used(a, "tiktok") == 1
        use(a, "tiktok", 4)
        assert not quotas.check_platform_quota(a, "tiktok", TT.format(99))["allowed"]
        again = quotas.check_platform_quota(a, "tiktok", TT.format(1) + "#t=3")
        assert again["allowed"] and again.get("already_counted")

    def test_redis_down_fails_open(self, monkeypatch):
        def _down():
            raise ConnectionError("down")
        monkeypatch.setattr("app.core.redis_client.get_redis", _down)
        assert quotas.check_platform_quota(anon(), "tiktok")["allowed"]
        assert quotas.record_platform_download(anon(), "tiktok", TT.format(1)) is False

    def test_counter_expires_after_utc_midnight(self, rc):
        quotas.record_platform_download(anon(), "tiktok", TT.format(1))
        assert 0 < rc.ttl(quotas._plat_key(anon(), "tiktok")) <= 86460
        assert 0 < rc.ttl(quotas._seen_key(anon())) <= 86460


# ── 3. Day boundary and the Vietnamese reset time ──────────────────────────

class TestReset:

    @pytest.mark.parametrize("tzname", ["UTC", "Asia/Ho_Chi_Minh", "America/Los_Angeles"])
    def test_reset_text_is_0700_whatever_the_server_tz(self, tz, clock, tzname):
        tz(tzname)
        for h in (0, 6, 16, 17, 23):
            clock(datetime(2026, 10, 6, h, 30, tzinfo=timezone.utc))
            assert quotas.reset_time_vn_text() == "07:00"
            assert quotas.next_reset_utc() == datetime(2026, 10, 7, tzinfo=timezone.utc)

    @pytest.mark.parametrize("tzname", ["UTC", "Asia/Ho_Chi_Minh"])
    def test_allowance_returns_at_utc_midnight_not_vn_midnight(self, rc, tz, clock, tzname):
        tz(tzname)
        u = user()
        clock(datetime(2026, 10, 6, 16, 59, tzinfo=timezone.utc))    # 23:59 in Vietnam
        use(u, "tiktok", 20)
        q = quotas.check_platform_quota(u, "tiktok")
        assert not q["allowed"]
        assert "Lượt mới được cộng lại lúc 07:00." in q["message"]
        assert q["reset_at"] == "2026-10-07T00:00:00+00:00" and q["reset_time_vn"] == "07:00"
        clock(datetime(2026, 10, 6, 17, 1, tzinfo=timezone.utc))     # 00:01 in Vietnam
        assert not quotas.check_platform_quota(u, "tiktok")["allowed"]
        clock(datetime(2026, 10, 6, 23, 59, 59, tzinfo=timezone.utc))  # 06:59:59 VN
        assert not quotas.check_platform_quota(u, "tiktok")["allowed"]
        clock(datetime(2026, 10, 7, 0, 0, 1, tzinfo=timezone.utc))   # 07:00:01 VN
        q = quotas.check_platform_quota(u, "tiktok")
        assert q["allowed"] and q["downloads_today"] == 0


# ── 4. /fetch-link ─────────────────────────────────────────────────────────

def _post(app, url, ip=IP, headers=None):
    return app.post("/api/v1/fetch-link", json={"url": url, "quality": "video"},
                    headers={"X-Forwarded-For": ip, **(headers or {})})


@pytest.fixture
def admin_session(monkeypatch):
    import app.api.admin as admin_mod
    monkeypatch.setattr(admin_mod, "_redis", lambda: None)
    monkeypatch.setattr(admin_mod, "_session_is_valid", lambda r, t: t == "good-session")
    return {"X-Admin-Token": "good-session"}


@pytest.fixture
def signed_in(monkeypatch):
    from app.core.auth_middleware import get_optional_user
    import app.main as main_mod
    monkeypatch.setattr(quotas, "_get_tier", lambda uid: "free")
    monkeypatch.setattr(quotas, "increment_usage", lambda *a, **k: None)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.core.metering.record_download", _noop)
    main_mod.app.dependency_overrides[get_optional_user] = lambda: {"id": "u-7", "tier": "free"}
    yield "u-7"
    main_mod.app.dependency_overrides.pop(get_optional_user, None)


class TestFetchLink:

    def test_guest_6th_tiktok_refused_instagram_still_ok(self, app, route, sync_outcomes, monkeypatch):
        _ok_extractor(route, monkeypatch)
        for i in range(5):
            r = _post(app, TT.format(7000 + i))
            assert r.status_code == 200, r.text[:300]
        r = _post(app, TT.format(7999))
        assert r.status_code == 429, r.text[:300]
        det = r.json()["detail"]
        assert det["error_code"] == "quota_exceeded_daily"
        assert det["platform"] == "tiktok" and det["daily_limit"] == 5 and det["downloads_today"] == 5
        assert det["quota_scope"] == "per_platform" and det["remaining"] == 0
        assert det["message"].startswith("Khách tải được tối đa 5 lượt/ngày cho TikTok.")
        assert _post(app, IG.format("abc")).status_code == 200
        # the same TikTok again (other quality) is not a new lượt
        assert _post(app, TT.format(7000)).status_code == 200

    def test_failure_is_not_counted(self, app, route, rc, sync_outcomes, monkeypatch):
        _fail_extractor(route, monkeypatch)
        for _ in range(7):
            _post(app, TT.format(1))
        assert quotas.platform_used(anon(), "tiktok") == 0

    def test_youtube_quota_still_applies_on_top(self, app, route, sync_outcomes, monkeypatch):
        """yt_quota (YT_QUOTA_ANON, a proxy-cost guard) stays; the per-platform
        allowance is checked first."""
        from app.core import youtube_gate, yt_quota
        _ok_extractor(route, monkeypatch)
        monkeypatch.setattr(youtube_gate, "preflight", lambda *a, **k: {"effective_height": None})
        monkeypatch.setattr(youtube_gate, "record_result", lambda *a, **k: None)
        monkeypatch.setattr(yt_quota, "global_reserve", lambda *a, **k: (True, 1, 100))
        monkeypatch.setattr(yt_quota, "reserve", lambda *a, **k: (False, 1, 1))
        r = _post(app, "https://www.youtube.com/watch?v=jNQXAC9IVRw")
        assert r.status_code == 429 and r.json()["detail"]["error"] == "youtube_quota_exceeded"

    def test_admin_session_is_unlimited_and_skips_the_personal_youtube_cap(
            self, app, route, rc, sync_outcomes, monkeypatch, admin_session):
        from app.core import youtube_gate, yt_quota
        _ok_extractor(route, monkeypatch)
        for i in range(30):
            r = _post(app, TT.format(i), headers=admin_session)
            assert r.status_code == 200, f"#{i + 1}: {r.text[:200]}"
        assert rc.keys("vidgrab:quota:plat*") == []
        reserved = []
        monkeypatch.setattr(youtube_gate, "preflight", lambda *a, **k: {"effective_height": None})
        monkeypatch.setattr(youtube_gate, "record_result", lambda *a, **k: None)
        monkeypatch.setattr(yt_quota, "global_reserve", lambda *a, **k: (True, 1, 100))
        monkeypatch.setattr(yt_quota, "reserve", lambda *a, **k: reserved.append(a) or (False, 1, 1))
        r = _post(app, "https://www.youtube.com/watch?v=jNQXAC9IVRw", headers=admin_session)
        assert r.status_code == 200, r.text[:300]
        assert reserved == []

    def test_wrong_admin_token_is_a_guest(self, app, route, sync_outcomes, monkeypatch, admin_session):
        _ok_extractor(route, monkeypatch)
        for i in range(5):
            assert _post(app, TT.format(i), headers={"X-Admin-Token": "nope"}).status_code == 200
        assert _post(app, TT.format(9), headers={"X-Admin-Token": "nope"}).status_code == 429

    def test_signed_in_user_gets_20_then_403(self, app, route, sync_outcomes, monkeypatch, signed_in):
        _ok_extractor(route, monkeypatch)
        for i in range(20):
            r = _post(app, TT.format(i))
            assert r.status_code == 200, f"#{i + 1}: {r.text[:200]}"
        r = _post(app, TT.format(99))
        assert r.status_code == 403
        det = r.json()["detail"]
        assert det["daily_limit"] == 20 and det["requester"] == "user"
        assert det["message"].startswith("Bạn đã dùng hết 20 lượt hôm nay cho TikTok.")
        assert _post(app, IG.format("x")).status_code == 200


# ── 5. /bulk-download ──────────────────────────────────────────────────────

class _BulkSB:
    def __init__(self):
        self.inserts = []
        self._n = 0

    def table(self, name):
        sb = self

        class T:
            def insert(self, row):
                sb._n += 1
                sb.inserts.append((name, {"id": f"job-{sb._n}", **row}))
                self._row = {"id": f"job-{sb._n}", **row}
                return self

            def __getattr__(self, _n):
                return lambda *a, **k: self

            def execute(self):
                return MagicMock(data=[getattr(self, "_row", {})])
        return T()


@pytest.fixture
def bulk(route, monkeypatch):
    sb = _BulkSB()
    monkeypatch.setattr(route, "get_supabase_client", lambda: sb)
    monkeypatch.setattr("app.utils.link_resolver.resolve_short_url", lambda u: u)
    monkeypatch.setattr("app.tasks.video_tasks.delete_batch_resources.apply_async", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(route.process_video_task, "apply_async", lambda *a, **k: sent.append(k))
    monkeypatch.setattr(route.scrape_channel_task, "apply_async", lambda *a, **k: sent.append(k))
    monkeypatch.setattr(quotas, "_get_tier", lambda uid: "free")
    return {"sb": sb, "sent": sent}


def _bulk(app, urls, ip=IP, headers=None, **extra):
    return app.post("/api/v1/bulk-download", json={"urls": urls, **extra},
                    headers={"X-Forwarded-For": ip, **(headers or {})})


class TestBulk:

    def test_guest_items_beyond_allowance_get_a_per_item_message(self, app, bulk, rc, monkeypatch):
        monkeypatch.setitem(quotas.TIER_PERMISSIONS["free"], "batch_limit", 50)
        use(anon(), "tiktok", 3)
        urls = [TT.format(i) for i in range(100, 104)] + [IG.format("a"), IG.format("b")]
        r = _bulk(app, urls)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["videos_queued"] == 4 and body["quota_refused"] == 2
        failed = [row for _, row in bulk["sb"].inserts if row["status"] == "failed"]
        assert [f["original_url"] for f in failed] == [TT.format(102), TT.format(103)]
        assert failed[0]["error_message"] == (
            "Đã hết 5 lượt/ngày của khách cho TikTok, mục này chưa được tải. "
            "Đăng nhập để tải 20 lượt/ngày.")
        # guests are counted at dispatch (unchanged); the worker is told so
        assert quotas.platform_used(anon(), "tiktok") == 5
        assert quotas.platform_used(anon(), "instagram") == 2
        assert all(k["kwargs"] == {"_requester": f"ip:{IP}", "_quota_precounted": True}
                   for k in bulk["sent"])

    def test_whole_batch_refused_only_when_nothing_fits(self, app, bulk, rc):
        use(anon(), "tiktok", 5)
        r = _bulk(app, [TT.format(1), TT.format(2)])
        assert r.status_code == 429
        det = r.json()["detail"]
        assert det["platform"] == "tiktok" and det["error_code"] == "quota_exceeded_daily"
        assert bulk["sb"].inserts == [] and bulk["sent"] == []

    def test_duplicate_links_in_one_batch_take_one_slot(self, app, bulk, rc):
        use(anon(), "tiktok", 4)
        r = _bulk(app, [TT.format(1), TT.format(1), TT.format(1)])
        assert r.status_code == 200 and r.json()["quota_refused"] == 0
        assert quotas.platform_used(anon(), "tiktok") == 5

    def test_signed_in_items_are_counted_by_the_worker(self, app, bulk, rc, monkeypatch, signed_in):
        monkeypatch.setitem(quotas.TIER_PERMISSIONS["free"], "batch_limit", 50)
        use(user("u-7"), "tiktok", 18)
        r = _bulk(app, [TT.format(i) for i in range(500, 505)])
        assert r.status_code == 200
        assert r.json()["videos_queued"] == 2 and r.json()["quota_refused"] == 3
        failed = [row for _, row in bulk["sb"].inserts if row["status"] == "failed"]
        assert failed[0]["error_message"] == ("Đã hết 20 lượt hôm nay cho TikTok, mục này chưa được tải. "
                                              "Lượt mới được cộng lại lúc 07:00.")
        assert quotas.platform_used(user("u-7"), "tiktok") == 18     # not yet: on success
        assert all(k["kwargs"] == {"_requester": "user:u-7", "_quota_precounted": False}
                   for k in bulk["sent"])

    def test_admin_bulk_is_unlimited(self, app, bulk, rc, monkeypatch, admin_session):
        monkeypatch.setitem(quotas.TIER_PERMISSIONS["free"], "batch_limit", 50)
        r = _bulk(app, [TT.format(i) for i in range(30)], headers=admin_session)
        assert r.status_code == 200 and r.json()["videos_queued"] == 30
        assert r.json()["quota_refused"] == 0
        assert rc.keys("vidgrab:quota:plat*") == []

    def test_channel_on_an_exhausted_platform_is_refused_per_item(self, app, bulk, rc):
        use(anon(), "tiktok", 5)
        r = _bulk(app, ["https://www.tiktok.com/@someone", IG.format("z")], channel_mode=False)
        assert r.status_code == 200
        assert r.json()["quota_refused"] == 1


# ── 6. Worker ──────────────────────────────────────────────────────────────

def _ok_worker(worker, monkeypatch):
    vt = worker["vt"]

    def _ok(*a, **k):
        worker["calls"]["extract"] += 1
        return {"title": "t", "direct_mp4_url": "https://cdn.example.com/v.mp4"}
    monkeypatch.setattr(vt, "extract_video_info_sync", _ok)
    monkeypatch.setattr(vt, "increment_usage", lambda *a, **k: None)


class TestWorker:

    def test_job_over_allowance_fails_with_message_and_does_not_run(self, worker, monkeypatch):
        _ok_worker(worker, monkeypatch)
        use(anon(), "tiktok", 5)
        worker["run_once"](0, url=TT.format(1), _requester=f"ip:{IP}")
        assert worker["calls"]["extract"] == 0
        row = worker["db"].row
        assert row["status"] == "failed"
        assert row["error_message"].startswith("Khách tải được tối đa 5 lượt/ngày cho TikTok.")

    def test_success_is_counted_once(self, worker, monkeypatch):
        _ok_worker(worker, monkeypatch)
        worker["run_once"](0, url=TT.format(1), _requester=f"ip:{IP}")
        assert worker["calls"]["extract"] == 1
        assert quotas.platform_used(anon(), "tiktok") == 1

    def test_precounted_guest_job_is_neither_checked_nor_counted_again(self, worker, monkeypatch):
        _ok_worker(worker, monkeypatch)
        use(anon(), "tiktok", 5)
        worker["run_once"](0, url=TT.format(1), _requester=f"ip:{IP}", _quota_precounted=True)
        assert worker["calls"]["extract"] == 1
        assert quotas.platform_used(anon(), "tiktok") == 5

    def test_admin_job_is_not_metered(self, worker, monkeypatch):
        _ok_worker(worker, monkeypatch)
        worker["run_once"](0, url=TT.format(1), _requester="admin")
        assert worker["calls"]["extract"] == 1
        assert worker["rc"].keys("vidgrab:quota:plat*") == []

    def test_user_id_without_requester_still_counts_against_the_user(self, worker, monkeypatch):
        """Schedules, archive re-runs and older queued tasks pass only user_id."""
        _ok_worker(worker, monkeypatch)
        monkeypatch.setattr(quotas, "_get_tier", lambda uid: "free")
        vt = worker["vt"]
        vt.process_video_task.push_request(id="t-u", retries=0)
        try:
            vt.process_video_task.run("job-1", TT.format(1), "u-9", "video")
        finally:
            vt.process_video_task.pop_request()
        assert quotas.platform_used(user("u-9"), "tiktok") == 1

    def test_no_identity_is_not_metered(self, worker, monkeypatch):
        """Crash recovery / partner API jobs carry neither (partner has its own quota)."""
        _ok_worker(worker, monkeypatch)
        worker["run_once"](0, url=TT.format(1))
        assert worker["calls"]["extract"] == 1
        assert worker["rc"].keys("vidgrab:quota:plat*") == []

    def test_worker_binds_the_china_layer_requester(self, worker, monkeypatch):
        monkeypatch.setenv("CHINA_ACCESS_ENABLED", "1")
        from app.services.china_platforms.normalized_models import current_context
        seen = []

        def _ok(*a, **k):
            seen.append(current_context())
            return {"title": "t", "direct_mp4_url": "https://cdn.example.com/v.mp4"}
        monkeypatch.setattr(worker["vt"], "extract_video_info_sync", _ok)
        worker["run_once"](0, url=TT.format(1), _requester="admin")
        assert seen[0].requester_key == "admin" and seen[0].is_admin
        worker["db"].row["status"] = "pending"     # a second job, not a duplicate run
        worker["run_once"](0, url=TT.format(2), _requester=f"ip:{IP}")
        assert seen[1].requester_key == f"ip:{IP}" and not seen[1].is_admin
        assert current_context().requester_key == "unknown"     # reset after the task


# ── 7. Container queue ─────────────────────────────────────────────────────

def test_container_queue_refuses_items_beyond_the_allowance(rc, monkeypatch):
    from app.api import container
    sb = _BulkSB()
    monkeypatch.setattr("app.core.database.get_supabase_client", lambda: sb)
    sent = []
    monkeypatch.setattr("app.tasks.video_tasks.process_video_task.apply_async",
                        lambda *a, **k: sent.append(k))
    use(anon(), "douyin", 3)
    urls = [f"https://www.douyin.com/video/70000000000000000{i:02d}" for i in range(4)]
    n = asyncio.run(container._create_bulk_jobs(urls, "b-1", "video", None, requester=anon()))
    assert n == 2
    failed = [row for _, row in sb.inserts if row["status"] == "failed"]
    assert len(failed) == 2 and "Douyin" in failed[0]["error_message"]
    assert all(k["kwargs"] == {"_requester": f"ip:{IP}"} for k in sent)


# ── 8. Usage endpoints stay backward compatible ────────────────────────────

class _UsageSB:
    def table(self, name):
        class T:
            def __getattr__(self, _n):
                return lambda *a, **k: self

            def execute(self):
                if name == "user_usage":
                    return MagicMock(data=[{"downloads_today": 9, "downloads_this_month": 40,
                                            "bulk_jobs_count": 2, "last_reset_at": None}])
                return MagicMock(data=[{"tier": "free", "billing_status": "none"}])
        return T()


def test_user_usage_keeps_old_fields_and_adds_platform_quota(app, rc, monkeypatch):
    from app.core.auth_middleware import get_required_user
    import app.api.user as user_api
    import app.main as main_mod
    monkeypatch.setattr(user_api, "get_supabase_client", lambda: _UsageSB())
    use(user("u-3"), "tiktok", 7)
    use(user("u-3"), "youtube", 2)
    main_mod.app.dependency_overrides[get_required_user] = lambda: {"id": "u-3", "email": "a@b.c"}
    try:
        d = app.get("/api/v1/user/usage").json()
    finally:
        main_mod.app.dependency_overrides.pop(get_required_user, None)
    # old fields, old meaning
    for k in ("tier", "plan", "daily_limit", "downloads_today", "downloads_this_month",
              "bulk_jobs_count", "permissions", "limits"):
        assert k in d, k
    assert d["downloads_today"] == 9 and d["limits"]["batch"] == quotas.TIER_PERMISSIONS["free"]["batch_limit"]
    # what account menu / extension read first: the closest-to-limit platform
    assert d["used"] == 7 and d["limit"] == 20 and d["used_platform"] == "tiktok"
    pq = d["platform_quota"]
    assert pq["scope"] == "per_platform" and pq["limit"] == 20 and pq["reset_time_vn"] == "07:00"
    rows = {p["platform"]: p for p in pq["platforms"]}
    assert rows["tiktok"]["remaining"] == 13 and rows["youtube"]["used"] == 2
    assert rows["instagram"]["used"] == 0 and rows["other"]["label"] == "các trang khác"


def test_quota_endpoint_answers_for_the_guest_ip(app, rc):
    use(anon(), "tiktok", 5)
    d = app.get("/api/v1/quota", headers={"X-Forwarded-For": IP}).json()
    q = d["quota"]
    assert d["success"] and q["allowed"] is True       # other platforms remain
    assert q["downloads_today"] == 5 and q["daily_limit"] == 5 and q["plan"] == "anon"
    assert {p["platform"]: p["remaining"] for p in q["platform_quota"]["platforms"]}["tiktok"] == 0


# ── 9. China access layer ──────────────────────────────────────────────────

@pytest.fixture
def china_env(monkeypatch, rc):
    for k in ("CHINA_ACCESS_ADMIN_DAILY_RESOLVE_LIMIT", "CHINA_ACCESS_FREE_DAILY_RESOLVE_LIMIT",
              "CHINA_ACCESS_ANON_DAILY_RESOLVE_LIMIT", "CHINA_ACCESS_DOUYIN_MANAGED_DAILY_CALL_LIMIT",
              "CHINA_ACCESS_DOUYIN_MANAGED_DAILY_SPEND_CEILING_USD", "CHINA_ACCESS_APIFY_DAILY_CALL_LIMIT",
              "CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD", "CHINA_ACCESS_APIFY_MONTHLY_SPEND_CEILING_USD"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


class TestChinaLayer:

    def _cand(self, micros=1):
        from app.services.china_platforms import budget_guard
        return budget_guard.PaidCandidate("apify_douyin", "apify", micros)

    def test_defaults(self, china_env):
        from app.services.china_platforms import budget_guard, settings
        assert budget_guard.requester_limit("admin") == -1
        assert budget_guard.requester_limit("user:u") == 20
        assert budget_guard.requester_limit("ip:1.2.3.4") == 5
        for p in ("douyin", "kuaishou", "xiaohongshu"):
            assert settings.platform_managed_daily_call_limit(p) == -1
        assert settings.provider_daily_call_limit("apify") == -1
        # money ceilings unchanged
        assert settings.platform_managed_daily_spend_micros("douyin") == 1_000_000
        assert settings.platform_managed_daily_spend_micros("kuaishou") == 100_000
        assert settings.provider_daily_spend_micros("apify") == 1_000_000
        assert settings.provider_monthly_spend_micros("apify") == 5_000_000

    def test_admin_is_unlimited_and_call_caps_do_not_bind(self, china_env):
        from app.services.china_platforms import budget_guard
        for _ in range(120):      # > old admin 50, platform 50, provider 50
            assert budget_guard.precheck("douyin", "admin", [self._cand()]) is None
            budget_guard.reserve("douyin", "admin", self._cand())

    def test_per_person_limits_stay(self, china_env):
        from app.services.china_platforms import budget_guard
        for _ in range(20):
            budget_guard.reserve("douyin", "user:u1", self._cand())
        with pytest.raises(budget_guard.BudgetDenied) as ei:
            budget_guard.reserve("douyin", "user:u1", self._cand())
        assert ei.value.level == "user_quota"
        # another person is not blocked by the first one's use
        budget_guard.reserve("douyin", "user:u2", self._cand())
        for _ in range(5):
            budget_guard.reserve("douyin", "ip:1.1.1.1", self._cand())
        assert budget_guard.precheck("douyin", "ip:1.1.1.1", [self._cand()]).level == "user_quota"

    def test_admin_limit_env_still_honoured(self, china_env):
        from app.services.china_platforms import budget_guard
        china_env.setenv("CHINA_ACCESS_ADMIN_DAILY_RESOLVE_LIMIT", "2")
        budget_guard.reserve("douyin", "admin", self._cand())
        budget_guard.reserve("douyin", "admin", self._cand())
        with pytest.raises(budget_guard.BudgetDenied):
            budget_guard.reserve("douyin", "admin", self._cand())

    def test_spend_ceilings_still_enforced_with_unlimited_calls(self, china_env):
        from app.services.china_platforms import budget_guard
        china_env.setenv("CHINA_ACCESS_DOUYIN_MANAGED_DAILY_SPEND_CEILING_USD", "0.015")
        budget_guard.reserve("douyin", "admin", self._cand(7100))
        budget_guard.reserve("douyin", "admin", self._cand(7100))
        assert budget_guard.precheck("douyin", "admin", [self._cand(7100)]).level == "platform_spend"
        with pytest.raises(budget_guard.BudgetDenied) as ei:
            budget_guard.reserve("douyin", "admin", self._cand(7100))
        assert ei.value.level == "platform_spend"

    def test_provider_monthly_ceiling_still_enforced(self, china_env):
        from app.services.china_platforms import budget_guard
        china_env.setenv("CHINA_ACCESS_APIFY_MONTHLY_SPEND_CEILING_USD", "0.01")
        budget_guard.reserve("douyin", "admin", self._cand(7100))
        with pytest.raises(budget_guard.BudgetDenied) as ei:
            budget_guard.reserve("douyin", "admin", self._cand(7100))
        assert ei.value.level == "provider_month"

    def test_negative_spend_ceiling_still_means_nothing_may_be_spent(self, china_env):
        from app.services.china_platforms import budget_guard
        china_env.setenv("CHINA_ACCESS_DOUYIN_MANAGED_DAILY_SPEND_CEILING_USD", "-1")
        with pytest.raises(budget_guard.BudgetDenied) as ei:
            budget_guard.reserve("douyin", "admin", self._cand(7100))
        assert ei.value.level == "platform_spend"

    def test_a_positive_call_cap_can_be_brought_back(self, china_env):
        from app.services.china_platforms import budget_guard
        china_env.setenv("CHINA_ACCESS_DOUYIN_MANAGED_DAILY_CALL_LIMIT", "1")
        budget_guard.reserve("douyin", "admin", self._cand())
        with pytest.raises(budget_guard.BudgetDenied) as ei:
            budget_guard.reserve("douyin", "admin", self._cand())
        assert ei.value.level == "platform_calls"

    def test_admin_detection_is_shared(self, monkeypatch, admin_session):
        from app.services.china_platforms import integration
        req = MagicMock()
        req.headers = {"X-Admin-Token": "good-session"}
        assert integration._is_admin_session(req) is True
        req.headers = {"X-Admin-Token": "bad"}
        assert integration._is_admin_session(req) is False
        assert quotas.is_admin_request(None) is False


# Owner 2026-10-06: the allowance is per platform for EVERY platform, so the
# China platforms must not share the single "other" bucket.
@pytest.mark.parametrize("url,slug", [
    ("https://v.kuaishou.com/dEGaIW", "kuaishou"),
    ("https://www.kuaishou.com/short-video/3x2wdee2f2ud7ac", "kuaishou"),
    ("https://www.xiaohongshu.com/explore/6a1fd7d1000000003700f22f", "xiaohongshu"),
    ("http://xhslink.com/a/AbC", "xiaohongshu"),
    ("https://b23.tv/AbC", "bilibili"),
    ("https://www.bilibili.com/video/BV1ECeJ65EZS", "bilibili"),
    ("https://www.iq.com/play/x-episode-1-abc", "iqiyi"),
    ("https://v.youku.com/v_show/id_x.html", "youku"),
    ("https://www.mgtv.com/b/1/2.html", "mgtv"),
    ("https://example.com/?next=iq.com", "other"),
    ("https://notiq.com/x", "other"),
])
def test_china_platforms_have_their_own_bucket(url, slug):
    from app.core.platform_key import platform_key, platform_label
    assert platform_key(url) == slug
    assert platform_label(slug) != "các trang khác" or slug == "other"
