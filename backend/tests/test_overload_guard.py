"""
Task #6397 — overload guard: a busy server answers 503 server_busy at once.

Holds: busy → 503 + Retry-After + Vietnamese message + retry_after in the body ·
under the limit the request reaches the endpoint untouched · the in-flight
counter is released on exceptions, HTTP errors and cancelled/timed-out work ·
extraction threads are counted while alive (also orphaned after a timeout) and
released when they end · stale threads are not counted · switch off → never
busy · admin session bypass · probe/health/admin paths never guarded · admin
page shows and sets the limit. fakeredis only, no network.
"""
from __future__ import annotations

import asyncio
import threading
import time

import fakeredis
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import admin_switches as api_mod
from app.api.admin import verify_admin
from app.core import admin_switches as sw
from app.core import overload_guard as og
from app.main import app as fastapi_app

client = TestClient(fastapi_app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("OVERLOAD_GUARD_ENABLED", "OVERLOAD_MAX_INFLIGHT", "OVERLOAD_RETRY_AFTER_SEC",
              "OVERLOAD_THREAD_STALE_SEC", "MAX_CONCURRENT_DOWNLOADS"):
        monkeypatch.delenv(k, raising=False)
    og._reset_for_tests()
    sw.reset_cache()
    yield
    og._reset_for_tests()
    sw.reset_cache()


@pytest.fixture(autouse=True)
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)
    return r


def _fill(n: int) -> None:
    for _ in range(n):
        og.force_enter()


# A tiny app with the same middleware and routes at guarded paths, so failure
# modes can be driven without touching the real extraction code.
mini = FastAPI()
mini.add_middleware(og.OverloadGuardMiddleware)
seen = {"inflight": None}


@mini.post("/api/v1/fetch-link")
async def _ok():
    seen["inflight"] = og.inflight()
    return {"ok": True}


@mini.post("/api/v1/trim")
async def _boom():
    raise RuntimeError("boom")


@mini.post("/api/v1/to-gif")
async def _http_err():
    raise HTTPException(status_code=504, detail="extraction_timeout")


@mini.post("/api/v1/fetch-threads")
async def _timeout():
    await asyncio.wait_for(asyncio.sleep(5), timeout=0.01)


@mini.get("/health")
async def _health():
    return {"ok": True}


mini_client = TestClient(mini, raise_server_exceptions=False)


# ── busy → 503 ──────────────────────────────────────────────────────────────

def test_busy_answers_503_with_retry_after_and_vietnamese_message(monkeypatch):
    monkeypatch.setenv("OVERLOAD_MAX_INFLIGHT", "3")
    monkeypatch.setenv("OVERLOAD_RETRY_AFTER_SEC", "45")
    _fill(3)
    r = client.post("/api/v1/fetch-link", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert r.status_code == 503
    assert r.headers["retry-after"] == "45"
    body = r.json()
    assert body["error_code"] == "server_busy"
    assert body["retry_after"] == 45
    assert body["message"] == "Hệ thống đang bận, vui lòng thử lại sau khoảng 45 giây."
    assert body["detail"] == body["message"]
    assert og.inflight() == 3            # the refused request was not counted
    assert og.status()["rejected_this_process"] == 1
    assert og.rejected_today() == 1


def test_busy_response_is_readable_cross_origin(monkeypatch):
    monkeypatch.setenv("OVERLOAD_MAX_INFLIGHT", "2")
    _fill(2)
    r = client.post("/api/v1/fetch-link", json={"url": "x"},
                    headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 503
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "retry-after" in r.headers.get("access-control-expose-headers", "").lower()


def test_busy_by_live_extraction_threads(monkeypatch):
    monkeypatch.setenv("OVERLOAD_MAX_INFLIGHT", "2")
    with og.extraction_thread(), og.extraction_thread():
        assert og.live_threads() == 2
        r = mini_client.post("/api/v1/fetch-link")
        assert r.status_code == 503
    assert og.live_threads() == 0
    assert mini_client.post("/api/v1/fetch-link").status_code == 200


def test_other_heavy_paths_guarded(monkeypatch):
    monkeypatch.setenv("OVERLOAD_MAX_INFLIGHT", "2")
    _fill(2)
    for path in ("/api/v1/fetch-threads", "/api/v1/fetch-spotify", "/api/v1/trim",
                 "/api/v1/to-gif", "/api/v1/process/subtitle", "/api/v1/formats/probe"):
        r = client.post(path, json={})
        assert r.status_code == 503, path
        assert r.json()["error_code"] == "server_busy"


# ── normal path untouched ───────────────────────────────────────────────────

def test_under_limit_reaches_endpoint_and_releases(monkeypatch, tmp_path):
    monkeypatch.setattr("app.core.disk_guardrail._DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr("app.core.disk_guardrail.check_can_accept_job", lambda *a, **k: (True, ""))
    r = client.post("/api/v1/fetch-link", json={"url": ""})
    assert r.status_code == 400          # the endpoint's own validation ran
    assert og.inflight() == 0
    r = mini_client.post("/api/v1/fetch-link")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert seen["inflight"] == 1         # counted while inside
    assert og.inflight() == 0


def test_default_limit_is_twice_max_concurrent_downloads(monkeypatch):
    assert og.default_limit() == 20
    monkeypatch.setenv("MAX_CONCURRENT_DOWNLOADS", "6")
    assert og.default_limit() == 12
    monkeypatch.setenv("OVERLOAD_MAX_INFLIGHT", "7")
    assert og.default_limit() == 7
    _fill(19)
    monkeypatch.delenv("OVERLOAD_MAX_INFLIGHT")
    monkeypatch.delenv("MAX_CONCURRENT_DOWNLOADS")
    assert mini_client.post("/api/v1/fetch-link").status_code == 200   # 19 < 20


# ── no leak ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,code", [("/api/v1/trim", 500), ("/api/v1/to-gif", 504),
                                       ("/api/v1/fetch-threads", 500)])
def test_counter_released_on_errors(path, code):
    for _ in range(5):
        r = mini_client.post(path)
        assert r.status_code == code
    assert og.inflight() == 0


def test_extraction_thread_released_on_exception():
    with pytest.raises(ValueError):
        with og.extraction_thread():
            assert og.live_threads() == 1
            raise ValueError("x")
    assert og.live_threads() == 0


def test_run_with_timeout_counts_orphan_until_it_ends():
    from app.services.downloader import _run_with_timeout
    release = threading.Event()

    def slow():
        release.wait(5)
        return "late"

    with pytest.raises(TimeoutError):
        _run_with_timeout(slow, timeout=0.05)
    assert og.live_threads() == 1        # orphan still running, still counted
    release.set()
    for _ in range(100):
        if og.live_threads() == 0:
            break
        time.sleep(0.02)
    assert og.live_threads() == 0

    def bad():
        raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        _run_with_timeout(bad, timeout=2)
    assert _run_with_timeout(lambda: 5, timeout=2) == 5
    assert og.live_threads() == 0


def test_stale_threads_not_counted(monkeypatch):
    monkeypatch.setenv("OVERLOAD_THREAD_STALE_SEC", "60")
    monkeypatch.setenv("OVERLOAD_MAX_INFLIGHT", "2")
    with og._lock:
        og._threads[901] = time.monotonic() - 3600
        og._threads[902] = time.monotonic() - 3600
    assert og.live_threads() == 0
    assert mini_client.post("/api/v1/fetch-link").status_code == 200


# ── switch off / bypass ─────────────────────────────────────────────────────

def test_env_off_never_busy(monkeypatch):
    monkeypatch.setenv("OVERLOAD_GUARD_ENABLED", "false")
    monkeypatch.setenv("OVERLOAD_MAX_INFLIGHT", "2")
    _fill(50)
    assert mini_client.post("/api/v1/fetch-link").status_code == 200
    assert og.inflight() == 50


def test_admin_switch_off_never_busy(rc, monkeypatch):
    monkeypatch.setenv("OVERLOAD_MAX_INFLIGHT", "2")
    _fill(2)
    assert mini_client.post("/api/v1/fetch-link").status_code == 503
    rc.hset(sw.OVERRIDE_KEY, sw.OVERLOAD_GUARD, "off")
    sw.reset_cache()
    assert mini_client.post("/api/v1/fetch-link").status_code == 200


def test_admin_session_bypasses(monkeypatch):
    monkeypatch.setenv("OVERLOAD_MAX_INFLIGHT", "2")
    _fill(2)
    monkeypatch.setattr("app.core.quotas.is_admin_request", lambda req: True)
    assert mini_client.post("/api/v1/fetch-link").status_code == 503   # no header → not checked
    r = mini_client.post("/api/v1/fetch-link", headers={"X-Admin-Token": "sess"})
    assert r.status_code == 200
    assert og.inflight() == 2
    monkeypatch.setattr("app.core.quotas.is_admin_request", lambda req: False)
    assert mini_client.post("/api/v1/fetch-link", headers={"X-Admin-Token": "bad"}).status_code == 503


def test_probe_and_admin_paths_never_guarded(monkeypatch):
    monkeypatch.setenv("OVERLOAD_MAX_INFLIGHT", "2")
    _fill(10)
    assert mini_client.get("/health").status_code == 200
    for method, path in (("GET", "/health"), ("GET", "/api/v1/ping"),
                         ("GET", "/api/v1/platform-status"), ("GET", "/api/v1/client/version"),
                         ("GET", "/api/v1/admin/switches"), ("OPTIONS", "/api/v1/fetch-link"),
                         ("GET", "/api/v1/fetch-link"), ("POST", "/api/v1/bulk-download"),
                         ("POST", "/api/v1/admin/login")):
        assert not og.is_guarded(method, path), (method, path)


def test_guard_fails_open_when_redis_down(monkeypatch):
    server = fakeredis.FakeServer()
    server.connected = False
    monkeypatch.setattr("app.core.redis_client._client",
                        fakeredis.FakeRedis(server=server, decode_responses=True))
    sw.reset_cache()
    og.reset_cache()
    assert og.limit() == 20 and og.enabled() is True
    assert mini_client.post("/api/v1/fetch-link").status_code == 200
    monkeypatch.setenv("OVERLOAD_MAX_INFLIGHT", "2")
    _fill(2)
    r = mini_client.post("/api/v1/fetch-link")
    assert r.status_code == 503            # still protects; counting is in memory
    assert og.rejected_today() is None


# ── admin page ──────────────────────────────────────────────────────────────

@pytest.fixture
def admin():
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    yield
    fastapi_app.dependency_overrides.pop(verify_admin, None)


@pytest.fixture
def audit(monkeypatch):
    calls = []
    monkeypatch.setattr(api_mod, "log_admin_action", lambda req, action, **kw: calls.append((action, kw)))
    return calls


def test_admin_view_and_limit_roundtrip(admin, audit, rc):
    s = {x["key"]: x for x in client.get("/api/v1/admin/switches").json()["switches"]}
    g = s["overload_guard"]
    assert g["state"] == "on" and g["source"] == "default"
    assert g["threshold"]["value"] == 20 and g["threshold"]["source"] == "default"

    r = client.post("/api/v1/admin/switches/overload_guard/limit", json={"limit": 6})
    assert r.status_code == 200
    assert r.json()["switch"]["threshold"] == {**r.json()["switch"]["threshold"], "value": 6, "source": "admin"}
    assert og.limit() == 6
    assert audit[-1][0] == "admin.switch.overload_limit"

    assert client.post("/api/v1/admin/switches/overload_guard/limit", json={"limit": 1}).status_code == 400
    assert client.post("/api/v1/admin/switches/overload_guard/limit", json={"limit": None}).status_code == 200
    assert og.limit() == 20

    r = client.post("/api/v1/admin/switches/overload_guard", json={"state": "off"})
    assert r.status_code == 200 and r.json()["switch"]["state"] == "off"
    assert og.enabled() is False


def test_admin_limit_takes_effect(rc, monkeypatch):
    og.set_admin_limit(3)
    _fill(3)
    assert mini_client.post("/api/v1/fetch-link").status_code == 503
    og.set_admin_limit(None)
    assert mini_client.post("/api/v1/fetch-link").status_code == 200


def test_limit_requires_admin():
    fastapi_app.dependency_overrides.pop(verify_admin, None)
    r = client.post("/api/v1/admin/switches/overload_guard/limit", json={"limit": 5})
    assert r.status_code in (401, 403)
