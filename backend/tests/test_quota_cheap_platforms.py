"""
Cheap platforms (TikTok/Douyin/Threads by default) get their own, larger daily
counter, and using it never consumes the standard one. Expensive platforms
(YouTube, Instagram, Facebook, Spotify…) keep the old limits.
"""

import pytest

from app.core import quotas


class _FakeRedis:
    """Just enough Redis for counters: get / incr / expire / pipeline."""

    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, *a, **kw):
        self.store[k] = v

    def incr(self, k, n=1):
        self.store[k] = int(self.store.get(k) or 0) + n
        return self.store[k]

    def decr(self, k, n=1):
        self.store[k] = int(self.store.get(k) or 0) - n
        return self.store[k]

    def expire(self, *a, **k):
        return True

    def pipeline(self):
        rc = self

        class _P:
            def __init__(self):
                self.ops = []

            def incr(self, k):
                self.ops.append(("incr", k))
                return self

            def expire(self, *a):
                return self

            def execute(self):
                return [rc.incr(k) for _, k in self.ops]
        return _P()


@pytest.fixture
def rc(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr("app.core.redis_client.get_redis", lambda: r)
    return r


IP = "198.51.100.7"


class TestDefaults:

    def test_defaults(self):
        assert quotas.GUEST_CHEAP_DAILY == 30
        assert quotas.FREE_CHEAP_DAILY == 100
        assert quotas.CHEAP_PLATFORMS == {"tiktok", "douyin", "threads"}

    @pytest.mark.parametrize("p", ["youtube", "instagram", "facebook", "spotify", "other", None])
    def test_expensive_platforms_are_not_cheap(self, p):
        assert not quotas.is_cheap_platform(p)


class TestGuestCounters:

    def test_guest_can_do_more_than_5_tiktoks_up_to_30(self, rc):
        for i in range(30):
            q = quotas.check_anon_quota(IP, "tiktok")
            assert q["allowed"], f"TikTok download #{i + 1} was blocked"
            quotas.increment_anon_usage(IP, "tiktok")
        q = quotas.check_anon_quota(IP, "tiktok")
        assert not q["allowed"]
        assert q["daily_limit"] == 30 and q["quota_bucket"] == "cheap"
        assert "TikTok" in q["message"]

    def test_guest_6th_youtube_is_still_blocked(self, rc):
        for _ in range(5):
            assert quotas.check_anon_quota(IP, "youtube")["allowed"]
            quotas.increment_anon_usage(IP, "youtube")
        q = quotas.check_anon_quota(IP, "youtube")
        assert not q["allowed"]
        assert q["daily_limit"] == 5 and q["quota_bucket"] == "standard"

    def test_counters_are_independent(self, rc):
        # 30 TikToks leave the standard bucket untouched…
        for _ in range(30):
            quotas.increment_anon_usage(IP, "tiktok")
        std = quotas.check_anon_quota(IP, "instagram")
        assert std["allowed"] and std["downloads_today"] == 0
        # …and exhausting the standard bucket leaves TikTok available.
        for _ in range(5):
            quotas.increment_anon_usage(IP, "facebook")
        assert not quotas.check_anon_quota(IP, "facebook")["allowed"]
        assert not quotas.check_anon_quota(IP, "tiktok")["allowed"]  # 30 used
        rc.store.clear()
        for _ in range(5):
            quotas.increment_anon_usage(IP, "youtube")
        assert quotas.check_anon_quota(IP, "douyin")["allowed"]
        assert quotas.check_anon_quota(IP, "threads")["downloads_today"] == 0

    def test_no_platform_means_standard(self, rc):
        """Bulk and other callers that pass no platform keep the old behaviour."""
        for _ in range(5):
            quotas.increment_anon_usage(IP)
        assert not quotas.check_anon_quota(IP)["allowed"]


class TestSignedInCounters:

    def test_free_user_cheap_limit_is_100(self, rc, monkeypatch):
        monkeypatch.setattr(quotas, "_get_tier", lambda uid: "free")
        for _ in range(100):
            quotas.increment_user_cheap_usage("u1")
        q = quotas.check_user_cheap_quota("u1", "tiktok")
        assert not q["allowed"] and q["daily_limit"] == 100 and q["quota_bucket"] == "cheap"

    def test_paid_tier_never_gets_less_than_free(self, monkeypatch):
        assert quotas._cheap_limit_for_tier("free") == 100
        assert quotas._cheap_limit_for_tier("pro") >= 100
        assert quotas._cheap_limit_for_tier("enterprise") == -1


# ── Through /fetch-link ─────────────────────────────────────────────────

@pytest.fixture
def route(monkeypatch, tmp_path, rc):
    import app.api.routes as routes
    from app.core import disk_guardrail, ssrf_guard
    monkeypatch.setattr(disk_guardrail, "_DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(disk_guardrail, "check_can_accept_job", lambda *a, **k: (True, ""))
    monkeypatch.setattr(routes, "_preflight_disk_check", lambda: None)
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))
    # The 30/minute per-IP route limit is not what is under test here and
    # would trip before the 30-download daily allowance can be exercised.
    # (routes holds its own reference — another test file may have reloaded
    # app.main, leaving two Limiter instances; switch off every one in play.)
    import app.main as main_mod
    for lim in {id(x): x for x in (main_mod.limiter, routes.limiter,
                                     getattr(main_mod.app.state, "limiter", None)) if x}.values():
        monkeypatch.setattr(lim, "enabled", False)

    async def _ok(url, *a, **k):
        return {"title": "t", "direct_mp4_url": "https://cdn.example.com/v.mp4", "original_url": url}
    monkeypatch.setattr(routes, "extract_video_info", _ok)
    return routes


def _post(app, url):
    return app.post("/api/v1/fetch-link", json={"url": url, "quality": "video"},
                    headers={"X-Forwarded-For": IP})


def test_route_guest_gets_30_tiktoks_and_cheap_bucket_named_in_error(app, route):
    for i in range(30):
        r = _post(app, f"https://www.tiktok.com/@x/video/{7000000000000000000 + i}")
        assert r.status_code == 200, f"#{i + 1}: {r.status_code} {r.text[:200]}"
    r = _post(app, "https://www.tiktok.com/@x/video/7100000000000000000")
    assert r.status_code == 429, r.text[:300]
    assert "detail" in r.json(), r.text[:300]
    det = r.json()["detail"]
    assert det["error_code"] == "quota_exceeded_daily"
    assert det["quota_bucket"] == "cheap" and det["daily_limit"] == 30
    assert det["platform"] == "tiktok"


def test_route_guest_6th_youtube_blocked_by_standard_quota(app, route, rc):
    # 5 standard downloads already used today (YouTube's own 1/day guest cap
    # would otherwise stop the 2nd one first, hiding what this test checks).
    rc.store[quotas._anon_quota_key(IP)] = 5
    r = _post(app, "https://www.youtube.com/watch?v=jNQXAC9IVRw")
    assert r.status_code == 429
    det = r.json()["detail"]
    assert det["quota_bucket"] == "standard" and det["daily_limit"] == 5


def test_route_cheap_downloads_do_not_consume_standard(app, route, rc):
    for i in range(10):
        assert _post(app, f"https://www.tiktok.com/@x/video/{i + 1}").status_code == 200
    assert int(rc.store.get(quotas._anon_quota_key(IP)) or 0) == 0
    assert int(rc.store[quotas._anon_quota_key(IP, "cheap")]) == 10
    # A standard-platform download still has its full allowance.
    r = _post(app, "https://www.instagram.com/p/abc/")
    assert r.status_code == 200, r.text[:200]
    assert int(rc.store[quotas._anon_quota_key(IP)]) == 1
