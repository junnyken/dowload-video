"""
Task #6397 — scripts/loadtest_light.py: stats are right, production is refused
without the flag and capped at 10, limits are clamped, and a short run against
an in-process ASGI app (no network) counts statuses per endpoint.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "loadtest_light.py"
_spec = importlib.util.spec_from_file_location("loadtest_light", _PATH)
lt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lt)


def test_percentile_nearest_rank():
    assert lt.percentile([], 50) is None
    assert lt.percentile([7], 95) == 7
    xs = list(range(1, 101))
    assert lt.percentile(xs, 50) == 50
    assert lt.percentile(xs, 95) == 95
    assert lt.percentile([30, 10, 20], 50) == 20
    assert lt.percentile([1, 2, 3, 4], 100) == 4


def test_stats_summary_counts_errors():
    s = lt.Stats()
    for ms in (10, 20, 30):
        s.add("/health", 200, ms)
    s.add("/health", 503, 5)
    s.add("/health", None, 15000)
    s.add("/api/v1/client/version", 304, 3)
    out = s.summary()
    h = out["/health"]
    assert h["requests"] == 5 and h["ok"] == 3 and h["errors"] == 2
    assert h["status"] == {"200": 3, "503": 1, "error": 1}
    assert h["p50_ms"] == 20 and h["p95_ms"] == 15000
    assert out["/api/v1/client/version"]["errors"] == 0
    text = s.render(2.0)
    assert "total 6 requests, 2 errors, 3.0 req/s" in text


@pytest.mark.parametrize("host,local", [
    ("localhost", True), ("127.0.0.1", True), ("127.5.5.5", True), ("::1", True),
    ("api.localhost", True), ("box.local", True),
    ("vibe1.tinhgon.xyz", False), ("dowload-video.mk.dev.matbao.ai", False),
    ("10.0.0.5", False), ("", False), (None, False),
])
def test_is_local_host(host, local):
    assert lt.is_local_host(host) is local


def test_production_refused_without_flag_and_capped_with_it():
    with pytest.raises(lt.Refused):
        lt.resolve_settings("https://vibe1.tinhgon.xyz", 5, 10, False)
    s = lt.resolve_settings("https://vibe1.tinhgon.xyz/", 20, 10, True)
    assert s["production"] is True and s["concurrency"] == 10 and s["base"] == "https://vibe1.tinhgon.xyz"
    assert lt.resolve_settings("https://x.example", 3, 10, True)["concurrency"] == 3


def test_local_limits_clamped():
    s = lt.resolve_settings("http://127.0.0.1:8000", 500, 999, False)
    assert s["production"] is False and s["concurrency"] == 20 and s["duration"] == 60
    s = lt.resolve_settings("http://localhost:8000", 0, 0, False)
    assert s["concurrency"] == 1 and s["duration"] == 1
    with pytest.raises(lt.Refused):
        lt.resolve_settings("ftp://localhost", 5, 10, False)


def test_main_refuses_production_without_running(capsys):
    assert lt.main(["--base", "https://vibe1.tinhgon.xyz"]) == 2
    assert "PRODUCTION" in capsys.readouterr().err


def test_run_against_in_process_app():
    app = FastAPI()
    calls = []

    @app.get("/health")
    async def health():
        calls.append("h")
        return {"ok": True}

    @app.get("/api/v1/platform-status")
    async def ps():
        from fastapi import HTTPException
        raise HTTPException(503, "busy")

    stats = asyncio.run(lt.run("http://localhost", 3, 0.3,
                               paths=("/health", "/api/v1/platform-status", "/api/v1/client/version"),
                               transport=httpx.ASGITransport(app=app)))
    out = stats.summary()
    assert out["/health"]["errors"] == 0 and out["/health"]["requests"] >= 1
    assert out["/api/v1/platform-status"]["status"].get("503", 0) >= 1
    assert out["/api/v1/client/version"]["status"].get("404", 0) >= 1
    assert calls
