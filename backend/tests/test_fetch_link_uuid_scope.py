"""
fetch_link must not shadow the module-level `uuid`.

A local `import uuid` inside the ADMIT_DELAYED branch made `uuid` a local
variable for the whole function, so every other `uuid.uuid4()` in fetch_link
raised UnboundLocalError unless that branch had run first:

  * scheduled downloads (scheduled_at) always answered 400 "Lỗi lên lịch";
  * the signed-in user's history row in download_jobs was never written
    (swallowed by `except Exception: pass`) — one reason the admin dashboard,
    which counted download_jobs, read 0;
  * the metering event was never recorded.
"""

import inspect

import fakeredis
import pytest


def _fetch_link_code():
    import app.api.routes as routes
    fn = inspect.unwrap(routes.fetch_link)
    return fn.__code__


def test_uuid_is_not_a_local_name_in_fetch_link():
    assert "uuid" not in _fetch_link_code().co_varnames


class _Tbl:
    def __init__(self, sink, name):
        self.sink, self.name = sink, name

    def insert(self, row):
        self.sink.append((self.name, row))
        return self

    def __getattr__(self, _n):
        return lambda *a, **k: self

    def execute(self):
        class R:
            data = []
        return R()


@pytest.fixture
def route(monkeypatch, tmp_path):
    import app.api.routes as routes
    from app.core import disk_guardrail, ssrf_guard
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)  # patch the singleton, not get_redis (modules bind get_redis at import)
    monkeypatch.setattr(disk_guardrail, "_DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(disk_guardrail, "check_can_accept_job", lambda *a, **k: (True, ""))
    monkeypatch.setattr(routes, "_preflight_disk_check", lambda: None)
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))
    import app.main as main_mod
    for lim in {id(x): x for x in (main_mod.limiter, routes.limiter,
                                     getattr(main_mod.app.state, "limiter", None)) if x}.values():
        monkeypatch.setattr(lim, "enabled", False)
    sink = []

    class SB:
        def table(self, name):
            return _Tbl(sink, name)
    monkeypatch.setattr(routes, "get_supabase_client", lambda: SB())
    routes._uuid_test_sink = sink
    return routes


def test_scheduled_download_is_accepted(app, route, monkeypatch):
    queued = []
    monkeypatch.setattr(route.process_video_task, "apply_async",
                        lambda *a, **k: queued.append(k))
    r = app.post("/api/v1/fetch-link", json={
        "url": "https://www.tiktok.com/@x/video/1", "quality": "video",
        "scheduled_at": "2099-01-01T00:00:00Z"})
    assert r.status_code == 202, r.text[:300]
    rows = [row for name, row in route._uuid_test_sink if name == "download_jobs"]
    assert len(rows) == 1 and rows[0]["status"] == "pending" and rows[0]["source"] == "scheduled"
    assert len(queued) == 1


def test_signed_in_success_writes_history_row(app, route, monkeypatch):
    from app.core.auth_middleware import get_optional_user
    import app.main as main_mod

    async def _ok(url, *a, **k):
        return {"title": "t", "direct_mp4_url": "https://cdn.example.com/v.mp4", "original_url": url}
    monkeypatch.setattr(route, "extract_video_info", _ok)
    monkeypatch.setattr(route, "check_user_quota", lambda uid: {"allowed": True})
    monkeypatch.setattr(route, "increment_usage", lambda *a, **k: None)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.core.metering.record_download", _noop)
    main_mod.app.dependency_overrides[get_optional_user] = lambda: {"id": "u-1", "tier": "free"}
    try:
        r = app.post("/api/v1/fetch-link", json={"url": "https://www.instagram.com/p/abc/",
                                                 "quality": "video"})
    finally:
        main_mod.app.dependency_overrides.pop(get_optional_user, None)
    assert r.status_code == 200, r.text[:300]
    rows = [row for name, row in route._uuid_test_sink if name == "download_jobs"]
    assert len(rows) == 1
    assert rows[0]["status"] == "success" and rows[0]["user_id"] == "u-1"
    assert rows[0]["batch_id"].startswith("single_")
