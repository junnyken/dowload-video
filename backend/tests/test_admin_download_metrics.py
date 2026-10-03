"""
F1 — admin download metrics must see every /fetch-link attempt.

Production 2026-10-02: Admin Analytics "Total jobs 0 (7 days)", Overview
"Lượt tải hôm nay 0" and every platform "last success: never" on a day with
plenty of real downloads. /admin/analytics and /admin/stats counted rows in
download_jobs, which /fetch-link writes only for a signed-in user's success.
Meanwhile the anomaly detector, reading the Redis counters vidgrab:stats:{date},
correctly reported TikTok failing. The admin views now read those counters
(app.core.download_outcomes), so these tests drive the real route and the real
admin endpoints against an in-memory Redis.
"""

from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from app.core import download_outcomes as do

TIKTOK_404 = (
    "Không thể trích xuất thông tin video. Vui lòng kiểm tra: link đúng không, video có "
    "Public không, hoặc video có bị xoá/riêng tư không. (Lý do kỹ thuật: [TikTok] "
    "7000000000000000000: Your IP address is blocked from accessing this post)"
)


@pytest.fixture
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)  # patch the singleton, not get_redis (modules bind get_redis at import)
    return r


def _down_redis():
    """A client whose every command raises ConnectionError (Redis down)."""
    server = fakeredis.FakeServer()
    server.connected = False
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture
def sync_outcomes(monkeypatch):
    """Run the background write inline so a test can read it back at once."""
    calls = []

    def _inline(platform, success, error_code=None):
        calls.append((platform, success, error_code))
        do.record(platform, success, error_code)

    monkeypatch.setattr(do, "record_async", _inline)
    return calls


class _FakeTable:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def insert(self, row):
        self.db.inserts.append((self.name, row))
        return self

    def select(self, *a, **k):
        return self

    def __getattr__(self, _name):          # eq/gte/lt/order/limit/neq/in_ …
        return lambda *a, **k: self

    def upsert(self, *a, **k):
        return self

    def execute(self):
        class R:
            data = []
            count = 0
        return R()


class _FakeSupabase:
    def __init__(self):
        self.inserts = []

    def table(self, name):
        return _FakeTable(self, name)


@pytest.fixture
def route(monkeypatch, tmp_path, rc):
    import app.api.routes as routes
    from app.core import disk_guardrail, ssrf_guard
    monkeypatch.setattr(disk_guardrail, "_DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(disk_guardrail, "check_can_accept_job", lambda *a, **k: (True, ""))
    monkeypatch.setattr(routes, "_preflight_disk_check", lambda: None)
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))
    import app.main as main_mod
    for lim in {id(x): x for x in (main_mod.limiter, routes.limiter,
                                     getattr(main_mod.app.state, "limiter", None)) if x}.values():
        monkeypatch.setattr(lim, "enabled", False)
    sb = _FakeSupabase()
    monkeypatch.setattr(routes, "get_supabase_client", lambda: sb)
    routes._test_sb = sb
    return routes


def _ok_extractor(routes, monkeypatch):
    async def _ok(url, *a, **k):
        return {"title": "t", "direct_mp4_url": "https://cdn.example.com/v.mp4", "original_url": url}
    monkeypatch.setattr(routes, "extract_video_info", _ok)


def _fail_extractor(routes, monkeypatch, msg=TIKTOK_404):
    async def _bad(url, *a, **k):
        raise ValueError(msg)
    monkeypatch.setattr(routes, "extract_video_info", _bad)


def _today():
    """The UTC date the day hash is keyed on (follows a pinned do._now)."""
    return do._now().strftime("%Y-%m-%d")


# Admin views use the Vietnam-time day; pin "now" so these tests do not depend
# on the hour they run at (05:30Z = 12:30 in Vietnam: same date in both).
FIXED_NOW = datetime(2026, 10, 3, 5, 30, tzinfo=timezone.utc)


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr(do, "_now", lambda: FIXED_NOW)
    return FIXED_NOW


def _writer_running_since(rc, dt):
    """An hourly bucket at `dt`: the hourly writer was live from then on."""
    rc.hset(do.HOUR_KEY_TPL.format(hour=do.hour_str(dt)), "tiktok:ok", 0)


def _post(app, url, ip="198.51.100.7"):
    return app.post("/api/v1/fetch-link", json={"url": url, "quality": "video"},
                    headers={"X-Forwarded-For": ip})


# ── Recording ──────────────────────────────────────────────────────────────

def test_success_is_recorded_once(app, route, rc, sync_outcomes, monkeypatch):
    _ok_extractor(route, monkeypatch)
    r = _post(app, "https://www.tiktok.com/@x/video/1")
    assert r.status_code == 200, r.text[:300]
    assert sync_outcomes == [("tiktok", True, None)]
    assert rc.hgetall(f"vidgrab:stats:{_today()}") == {"tiktok:ok": "1"}
    assert rc.get("p27a:ts:tiktok:success")
    assert not rc.exists(f"vidgrab:errcodes:{_today()}")


def test_classified_failure_is_recorded_once_with_error_code(app, route, rc, sync_outcomes, monkeypatch):
    _fail_extractor(route, monkeypatch)
    r = _post(app, "https://www.tiktok.com/@x/video/7000000000000000000")
    assert r.status_code == 404
    assert r.json()["error_code"] == "video_unavailable"
    assert sync_outcomes == [("tiktok", False, "video_unavailable")]
    assert rc.hgetall(f"vidgrab:stats:{_today()}") == {"tiktok:err": "1"}
    assert rc.hgetall(f"vidgrab:errcodes:{_today()}") == {"tiktok|video_unavailable": "1"}


def test_fetch_link_still_succeeds_when_recording_throws(app, route, monkeypatch):
    _ok_extractor(route, monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("redis exploded")
    monkeypatch.setattr(do, "record_async", _boom)
    monkeypatch.setattr(do, "record", _boom)
    r = _post(app, "https://www.tiktok.com/@x/video/2")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["success"] is True


def test_fetch_link_failure_status_unchanged_when_recording_throws(app, route, monkeypatch):
    _fail_extractor(route, monkeypatch)
    monkeypatch.setattr(do, "record_async", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = _post(app, "https://www.tiktok.com/@x/video/7000000000000000000")
    assert r.status_code == 404
    assert r.json()["error_code"] == "video_unavailable"


def test_record_async_never_blocks_on_a_hung_redis(monkeypatch):
    """The real background path: the caller returns before Redis answers."""
    import threading
    import time
    gate = threading.Event()
    seen = []

    def _slow(platform, success, error_code=None, *, now=None):
        gate.wait(5)
        seen.append((platform, success, error_code))
    monkeypatch.setattr(do, "record", _slow)
    t0 = time.monotonic()
    do.record_async("tiktok", True)
    assert time.monotonic() - t0 < 0.5
    gate.set()
    do.flush()
    assert seen == [("tiktok", True, None)]


def test_record_never_raises_without_redis(monkeypatch):
    monkeypatch.setattr("app.core.redis_client._client", _down_redis())
    do.record("tiktok", False, "video_unavailable")      # must not raise


# ── Quota / worker safety ──────────────────────────────────────────────────

def test_signed_in_success_increments_quota_exactly_once_and_queues_nothing(
        app, route, rc, sync_outcomes, monkeypatch):
    from app.core.auth_middleware import get_optional_user
    import app.main as main_mod
    _ok_extractor(route, monkeypatch)
    calls = {"usage": 0, "cheap": 0}
    monkeypatch.setattr(route, "check_user_quota", lambda uid: {"allowed": True})
    monkeypatch.setattr(route, "check_user_cheap_quota", lambda uid, p: {"allowed": True})
    monkeypatch.setattr(route, "increment_usage",
                        lambda uid, count_daily=True: calls.__setitem__("usage", calls["usage"] + 1))
    monkeypatch.setattr(route, "increment_user_cheap_usage",
                        lambda uid: calls.__setitem__("cheap", calls["cheap"] + 1))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.core.metering.record_download", _noop)
    queued = []
    monkeypatch.setattr(route.process_video_task, "apply_async",
                        lambda *a, **k: queued.append((a, k)))
    main_mod.app.dependency_overrides[get_optional_user] = lambda: {"id": "u-1", "tier": "free"}
    try:
        r = _post(app, "https://www.instagram.com/p/abc/")
    finally:
        main_mod.app.dependency_overrides.pop(get_optional_user, None)
    assert r.status_code == 200, r.text[:300]
    assert calls == {"usage": 1, "cheap": 0}
    assert sync_outcomes == [("instagram", True, None)]
    # Recording adds no download_jobs row: the only insert is the existing
    # personal-history row, already terminal, so no worker can pick it up.
    job_rows = [row for name, row in route._test_sb.inserts if name == "download_jobs"]
    assert len(job_rows) == 1 and job_rows[0]["status"] == "success"
    assert queued == []


def test_guest_failure_writes_no_job_row_and_no_quota(app, route, rc, sync_outcomes, monkeypatch):
    from app.core import quotas
    _fail_extractor(route, monkeypatch)
    r = _post(app, "https://www.tiktok.com/@x/video/7000000000000000000", ip="198.51.100.9")
    assert r.status_code == 404
    assert route._test_sb.inserts == []
    assert int(rc.get(quotas._anon_quota_key("198.51.100.9", "cheap")) or 0) == 0


def test_4k_guard_after_success_is_not_counted_twice(app, route, rc, sync_outcomes, monkeypatch):
    """A 403 raised after the success was counted must not add a failure too."""
    from app.core.auth_middleware import get_optional_user
    import app.main as main_mod

    async def _4k(url, *a, **k):
        return {"title": "t", "direct_mp4_url": "x", "downloaded_height": 2160}
    monkeypatch.setattr(route, "extract_video_info", _4k)
    monkeypatch.setattr(route, "check_user_quota", lambda uid: {"allowed": True})
    monkeypatch.setattr(route, "check_youtube_tier", lambda *a, **k: {"allowed": True})
    monkeypatch.setattr(route, "check_quality_permission",
                        lambda *a, **k: {"allowed": False, "message": "no", "tier": "free"})
    from app.core import youtube_gate, yt_quota
    monkeypatch.setattr(youtube_gate, "preflight", lambda *a, **k: {"effective_height": None})
    monkeypatch.setattr(yt_quota, "reserve", lambda *a, **k: (True, 1, 5))
    monkeypatch.setattr(yt_quota, "global_reserve", lambda *a, **k: (True, 1, 100))
    main_mod.app.dependency_overrides[get_optional_user] = lambda: {"id": "u-2", "tier": "free"}
    try:
        r = _post(app, "https://www.youtube.com/watch?v=jNQXAC9IVRw")
    finally:
        main_mod.app.dependency_overrides.pop(get_optional_user, None)
    assert r.status_code == 403, r.text[:300]
    assert sync_outcomes == [("youtube", True, None)]


# ── Admin endpoints read the counters ──────────────────────────────────────



def _admin_dependencies(app_obj):
    """Every verify_admin callable the mounted admin routes actually depend on.

    test_admin_security reloads app.api.admin, after which the module's
    `verify_admin` is a new object while the routes still hold the original —
    so override what the routes hold, not the module attribute."""
    found = set()
    for route in app_obj.routes:
        dep = getattr(route, "dependant", None)
        stack = list(getattr(dep, "dependencies", []) or [])
        while stack:
            d = stack.pop()
            if getattr(d.call, "__name__", "") == "verify_admin":
                found.add(d.call)
            stack.extend(d.dependencies or [])
    return found

@pytest.fixture
def admin(monkeypatch):
    import app.main as main_mod
    from app.api import admin as admin_mod
    deps = _admin_dependencies(main_mod.app)
    for d in deps:
        main_mod.app.dependency_overrides[d] = lambda: True
    monkeypatch.setattr(admin_mod, "get_supabase_client", lambda: _FakeSupabase())
    yield admin_mod
    for d in deps:
        main_mod.app.dependency_overrides.pop(d, None)


def _seed(now=None):
    now = now or datetime.now(timezone.utc)
    for _ in range(3):
        do.record("tiktok", True, now=now)
    do.record("tiktok", False, "video_unavailable", now=now)
    do.record("instagram", False, "temporary_blocked", now=now)


def test_admin_analytics_counts_fetch_link_outcomes(app, rc, admin, fixed_now):
    _writer_running_since(rc, fixed_now - timedelta(days=3))
    _seed()
    body = app.get("/api/v1/admin/analytics?days=1").json()
    assert body["summary"]["total_jobs"] == 5
    assert body["summary"]["total_success"] == 3
    assert body["summary"]["total_failed"] == 2
    assert body["summary"]["success_rate"] == 60.0
    assert body["daily_stats"][-1]["date"] == "2026-10-03"   # days=1 is TODAY (VN)
    assert body["daily_stats"][-1]["source"] == "hourly"
    assert body["daily_stats"][-1]["approximate"] is False
    assert body["timezone"] == "Asia/Ho_Chi_Minh"
    tt = next(p for p in body["platform_stats"] if p["key"] == "tiktok")
    assert tt["count"] == 4 and tt["platform"] == "TikTok"
    assert body["top_errors"][0]["error_code"] in ("video_unavailable", "temporary_blocked")


def test_admin_analytics_route_to_counters_end_to_end(app, route, rc, admin, sync_outcomes, monkeypatch, fixed_now):
    _ok_extractor(route, monkeypatch)
    assert _post(app, "https://www.tiktok.com/@x/video/1").status_code == 200
    _fail_extractor(route, monkeypatch)
    assert _post(app, "https://www.tiktok.com/@x/video/7000000000000000000").status_code == 404
    s = app.get("/api/v1/admin/analytics?days=7").json()["summary"]
    assert (s["total_jobs"], s["total_success"], s["total_failed"]) == (2, 1, 1)


def test_admin_analytics_success_rate_null_with_no_attempts(app, rc, admin, fixed_now):
    rc.hset(f"vidgrab:stats:{_today()}", "placeholder", "0")   # day exists, no attempts
    body = app.get("/api/v1/admin/analytics?days=1").json()
    assert body["summary"]["total_jobs"] == 0
    assert body["summary"]["success_rate"] is None
    assert body["daily_stats"][-1]["success_rate"] is None


def test_admin_stats_today_and_24h(app, rc, admin, fixed_now):
    _writer_running_since(rc, fixed_now - timedelta(days=3))
    _seed()
    body = app.get("/api/v1/admin/stats").json()
    assert body["total_downloads_today"] == 3
    assert body["downloads_today"] == {"attempts": 5, "success": 3, "failed": 2, "success_rate": 60.0}
    assert body["failed_24h"] == 2
    assert body["downloads_24h"]["attempts"] == 5
    plats = {p["platform"]: p for p in body["platform_breakdown_today"]}
    assert plats["instagram"]["success_rate"] == 0.0
    assert plats["tiktok"]["total"] == 4
    codes = {e["error_code"]: e["count"] for e in body["top_errors_today"]}
    assert codes == {"video_unavailable": 1, "temporary_blocked": 1}
    assert body["timezone"] == "Asia/Ho_Chi_Minh"
    assert body["today"]["date"] == "2026-10-03" and body["today"]["approximate"] is False
    assert body["today"]["window_start"] == "2026-10-02T17:00:00+00:00"
    assert body["today"]["window_end"] == "2026-10-03T17:00:00+00:00"


def test_admin_stats_nulls_when_counters_unreachable(app, admin, monkeypatch):
    monkeypatch.setattr("app.core.redis_client._client", _down_redis())
    body = app.get("/api/v1/admin/stats").json()
    assert body["total_downloads_today"] is None
    assert body["downloads_today"]["success_rate"] is None
    assert body["failed_24h"] is None


def test_platform_stats_success_rate_null_when_zero(app, rc, admin, fixed_now):
    rc.hset(f"vidgrab:stats:{_today()}", mapping={"tiktok:ok": "0", "tiktok:err": "0"})
    totals = app.get("/api/v1/admin/platform-stats?days=1").json()["totals"]
    assert totals[0]["success_rate"] is None


def test_platforms_health_last_success_from_counters(app, rc, admin, monkeypatch):
    from app.core import platform_circuit
    monkeypatch.setattr(platform_circuit, "get_state", lambda p: "closed")
    do.record("tiktok", True)
    do.record("tiktok", False, "video_unavailable")
    rows = {p["platform"]: p for p in app.get("/api/v1/admin/platforms/health").json()["platforms"]}
    assert rows["tiktok"]["lastSuccessAt"] != "never"
    assert rows["tiktok"]["lastSuccessAtIso"]
    assert rows["tiktok"]["totalJobs1h"] == 2
    assert rows["tiktok"]["failRate1h"] == 50
    assert rows["reddit"]["failRate1h"] is None          # no attempts → null
    assert rows["reddit"]["lastSuccessAt"] == "never"


def test_older_days_fall_back_to_download_jobs_without_double_count(app, rc, admin, monkeypatch, fixed_now):
    """A day with a counter hash reads only Redis; a day without one reads only
    download_jobs — the same attempt can never be counted from both. (No
    hourly coverage before this hour, so both days are UTC-day approximations.)"""
    today = _today()
    yday = (fixed_now - timedelta(days=1)).strftime("%Y-%m-%d")
    do.record("tiktok", True)

    class _Rows(_FakeSupabase):
        def table(self, name):
            t = _FakeTable(self, name)

            def _exec():
                class R:
                    data = [
                        {"status": "success", "platform": "tiktok", "created_at": f"{yday}T10:00:00+00:00"},
                        {"status": "failed", "platform": "tiktok", "created_at": f"{yday}T11:00:00+00:00"},
                        {"status": "pending", "platform": "tiktok", "created_at": f"{yday}T12:00:00+00:00"},
                        # a row on a day Redis covers must be ignored
                        {"status": "success", "platform": "tiktok", "created_at": f"{today}T01:00:00+00:00"},
                    ]
                return R()
            t.execute = _exec
            return t
    monkeypatch.setattr(admin, "get_supabase_client", lambda: _Rows())
    body = app.get("/api/v1/admin/analytics?days=2").json()
    by_day = {d["date"]: d for d in body["daily_stats"]}
    assert by_day[today]["source"] == "utc_day" and by_day[today]["total"] == 1
    assert by_day[today]["approximate"] is True and by_day[yday]["approximate"] is True
    assert by_day[yday]["source"] == "jobs_table"
    assert (by_day[yday]["success"], by_day[yday]["failed"]) == (1, 1)
    assert body["summary"]["total_jobs"] == 3
