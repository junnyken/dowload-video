"""
2026-10-06: a 33 MB / 151 s Douyin video resolved by Apify took 72-125 s on
/fetch-link because the server copy crawled at ~0.2 MB/s from the CDN, then
retried through the per-GB CN residential proxy, then handed the CDN URL
over anyway. For managed (Apify) results the server copy now stops after a
time budget and never goes through the CN proxy; native results keep the
old behaviour.
"""
import itertools

import httpx
import pytest

from app.services import downloader
from app.services.china_platforms import integration, provider_router
from tests._china_fakes import DY_URL, SIGNED, clean_env, flags_on, media_result, rc  # noqa: F401
from tests.test_china_access_integration import legacy_douyin  # noqa: F401


class _Recorder:
    def __init__(self):
        self.clients = []   # proxy kwarg of every httpx.Client created


@pytest.fixture
def slow_cdn(monkeypatch):
    """httpx.Client whose stream yields 200 chunks; time advances 1 s per
    chunk, so a 10 s budget stops it long before the end (without the budget
    the download completes and the test fails on local_file_path)."""
    rec = _Recorder()
    clock = itertools.count(0.0, 1.0)
    monkeypatch.setattr("time.monotonic", lambda: next(clock))

    class Resp:
        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size=65536):
            for _ in range(200):
                yield b"x" * 1024

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Client:
        def __init__(self, *a, **k):
            rec.clients.append(k.get("proxy"))

        def stream(self, *a, **k):
            return Resp()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("app.core.proxy_manager.IPROYAL_PROXY_CN", "http://cn-proxy.invalid:1")
    monkeypatch.setenv("CHINA_ACCESS_MANAGED_SERVER_DOWNLOAD_BUDGET_SEC", "10")
    return rec


def test_managed_result_hands_off_after_budget_without_cn_proxy(
        flags_on, monkeypatch, legacy_douyin, slow_cdn):  # noqa: F811
    async def fake_resolve(self, req, ctx=None, **kw):
        return media_result("apify_douyin", "managed", req.url)

    monkeypatch.setattr(provider_router.ProviderRouter, "resolve", fake_resolve)
    out = downloader._extract_video_info_impl(DY_URL, "video")
    assert out["provider"] == "apify_douyin"
    assert out["direct_mp4_url"] == SIGNED            # handed to the client
    assert not out.get("local_file_path")
    assert slow_cdn.clients == [None]                 # one direct attempt, no CN proxy


def test_native_result_still_tries_cn_proxy(monkeypatch, slow_cdn):
    """Flags off / native path: unchanged — direct first, then CN proxy.
    (The native stream would never end here, so stop it via a failing raise.)"""
    monkeypatch.setattr(integration, "resolve_douyin_via_access_layer", lambda *a, **k: None)
    monkeypatch.setattr(downloader, "extract_douyin_video_sync",
                        lambda *a, **k: {"title": "t", "direct_mp4_url": SIGNED, "provider": "tikwm"})

    class Boom(httpx.HTTPError):
        pass

    class FailingClient:
        def __init__(self, *a, **k):
            slow_cdn.clients.append(k.get("proxy"))

        def stream(self, *a, **k):
            raise Boom("403")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "Client", FailingClient)
    out = downloader._extract_video_info_impl(DY_URL, "video")
    assert out["direct_mp4_url"] == SIGNED
    assert slow_cdn.clients == [None, "http://cn-proxy.invalid:1"]
