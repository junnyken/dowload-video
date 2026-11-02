"""Focused SSRF regressions. Synthetic URLs/DNS and mocked I/O only.

Run with test_link_resolver_host_awareness.py in a network-isolated sandbox.
No real DB, broker, extractor, DNS, or HTTP requests are used by these tests.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.core import ssrf_guard
from app.utils import link_resolver as resolver


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    """Patch the guard's resolver, including its cache, not socket callers."""
    def resolve(host):
        return ("10.0.0.8",) if host == "private.example" else ("8.8.8.8",)
    monkeypatch.setattr(ssrf_guard, "_resolve", resolve)


@pytest.fixture
def partner_io(monkeypatch):
    from app.api import partner
    from app.tasks import partner_tasks, video_tasks
    from app.core import local_download

    db = MagicMock()
    db.table.return_value.insert.return_value.execute.return_value.data = []
    quota = MagicMock()
    dispatch = MagicMock()
    bridge = MagicMock()
    monkeypatch.setattr(partner, "get_service_client", lambda: db)
    monkeypatch.setattr(partner, "_check_job_quota", quota)
    monkeypatch.setattr(partner, "_increment_usage", MagicMock())
    monkeypatch.setattr(video_tasks.process_video_task, "apply_async", dispatch)
    monkeypatch.setattr(partner_tasks.sync_partner_job_task, "apply_async", bridge)
    monkeypatch.setattr(local_download, "public_api_base", lambda request: "https://api.example")
    tenant = SimpleNamespace(
        has_scope=lambda scope: True, tenant_id="synthetic-tenant",
        api_key_id="synthetic-key", plan=partner.TenantPlan.starter,
    )
    return SimpleNamespace(api=partner, db=db, quota=quota, dispatch=dispatch,
                           bridge=bridge, tenant=tenant)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/video", "http://10.0.0.1/video",
    "http://169.254.169.254/video", "http://[::1]/video",
    "https://private.example/video",
    "share text https://private.example/video end",
])
def test_partner_rejects_before_writes_or_dispatch(partner_io, url):
    io = partner_io
    with pytest.raises(HTTPException) as err:
        asyncio.run(io.api.submit_job(io.api.JobSubmitRequest(url=url),
                                     MagicMock(), io.tenant))
    assert err.value.status_code == 422
    assert err.value.detail == {"code": "invalid_url", "message": "URL is not allowed"}
    io.db.table.assert_not_called()
    io.quota.assert_not_called()
    io.dispatch.assert_not_called()
    io.bridge.assert_not_called()


@pytest.mark.parametrize("raw", [
    "https://public.example/video",
    "share text https://public.example/video end",
])
def test_partner_public_host_dispatches_extracted_url(partner_io, raw):
    io = partner_io
    expected = "https://public.example/video"
    result = asyncio.run(io.api.submit_job(io.api.JobSubmitRequest(url=raw),
                                          MagicMock(), io.tenant))
    assert result.url == expected
    assert result.status == "queued"
    io.quota.assert_called_once_with(io.tenant)
    inserts = io.db.table.return_value.insert.call_args_list
    assert len(inserts) == 2
    assert inserts[0].args[0]["url"] == expected
    assert inserts[1].args[0]["original_url"] == expected
    io.dispatch.assert_called_once()
    assert io.dispatch.call_args.kwargs["args"] == [inserts[1].args[0]["id"], expected]
    io.bridge.assert_called_once()


@pytest.fixture
def task_io(monkeypatch):
    from app.tasks import video_tasks as task
    from app.core import job_lease, metrics, platform_lanes

    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "status": "pending", "retry_count": 0,
    }
    updates = MagicMock()
    extract = MagicMock()
    monkeypatch.setattr(task, "get_supabase_client", lambda: db)
    monkeypatch.setattr(task, "_sb_update", updates)
    monkeypatch.setattr(task, "extract_video_info_sync", extract)
    for name in ("_register_pending_task", "_deregister_pending_task", "_platform_sleep"):
        monkeypatch.setattr(task, name, MagicMock())
    monkeypatch.setattr(task, "_bind_china_worker_context", lambda requester: None)
    monkeypatch.setattr(task, "_reset_china_worker_context", MagicMock())
    monkeypatch.setattr(task, "get_cached_result", lambda url: None)
    monkeypatch.setattr(job_lease, "JobLease", MagicMock())
    for name in ("try_acquire_platform", "try_acquire_user"):
        monkeypatch.setattr(platform_lanes, name, lambda *args: True)
    for name in ("release_platform", "release_user"):
        monkeypatch.setattr(platform_lanes, name, MagicMock())
    monkeypatch.setattr(metrics, "emit_job_event", MagicMock())
    return SimpleNamespace(task=task, db=db, updates=updates, extract=extract)


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "https://private.example/x"])
def test_task_private_url_fails_without_extraction(task_io, url):
    io = task_io
    assert io.task.process_video_task.run("synthetic-job", url) is None
    io.extract.assert_not_called()
    io.updates.assert_called_once()
    db, data, job_id = io.updates.call_args.args
    assert db is io.db and job_id == "synthetic-job"
    assert data["status"] == data["job_stage"] == "failed"
    assert data["auto_retry_status"] == "not_applicable"
    assert data["error_message"] == "URL is not allowed"
    assert url not in data["error_message"]


def test_task_public_url_reaches_extractor(task_io):
    # Stop at the extraction boundary: do not exercise file writes/notifications.
    class ExtractionReached(BaseException):
        pass
    io = task_io
    io.extract.side_effect = ExtractionReached
    url = "https://public.example/video"
    with pytest.raises(ExtractionReached):
        io.task.process_video_task.run("synthetic-job", url)
    io.extract.assert_called_once_with(url, "video", False, False)
    io.updates.assert_not_called()


@pytest.mark.parametrize("url, expected", [
    ("https://vt.tiktok.com/x", True),
    ("https://bit.ly/x", True),
    ("https://sub.t.co/x", True),  # allowed: subdomains of each listed host
    ("https://BIT.LY./x", True),
    ("https://example.com/path/bit.ly", False),
    ("https://evil.com/?u=youtu.be", False),
    ("https://nott.co/x", False),
    ("https://bit.ly.evil.com/x", False),
    ("https://bit.ly@evil.com/x", False),
    ("http://[invalid", False),
])
def test_short_link_host_matching(url, expected):
    assert resolver._is_short_url(url) is expected


def fake_http(monkeypatch, responses):
    """Only explicit URLs are accepted. Every attempt to make a request is recorded."""
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            logical = str(httpx.URL(url).copy_with(host=kwargs["extensions"]["sni_hostname"]))
            assert str(httpx.URL(url).host) == "8.8.8.8"
            logical_url = httpx.URL(logical)
            expected_host = logical_url.host
            if logical_url.port is not None and logical_url.port != (443 if logical_url.scheme == "https" else 80):
                expected_host += f":{logical_url.port}"
            assert kwargs["headers"]["Host"] == expected_host
            assert ":None" not in kwargs["headers"]["Host"]
            calls.append(logical)
            status, location, text = responses[logical]
            return httpx.Response(status, headers={"Location": location} if location else {},
                                  text=text, request=httpx.Request("GET", url))

    monkeypatch.setattr(resolver.httpx, "AsyncClient", Client)
    return calls


@pytest.mark.parametrize("helper", ["_try_resolve_302", "_try_resolve_follow"])
@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "https://private.example/x"])
def test_private_first_hop_makes_no_request(monkeypatch, helper, url):
    calls = fake_http(monkeypatch, {})
    assert asyncio.run(getattr(resolver, helper)(url, "synthetic-UA")) is None
    assert calls == []


@pytest.mark.parametrize("helper", ["_try_resolve_302", "_try_resolve_follow"])
@pytest.mark.parametrize("target", ["http://169.254.169.254/x", "https://private.example/x"])
def test_private_redirect_is_rejected(monkeypatch, helper, target):
    start = "https://bit.ly/x"
    calls = fake_http(monkeypatch, {start: (302, target, "")})
    assert asyncio.run(getattr(resolver, helper)(start, "synthetic-UA")) is None
    assert calls == [start]


def test_302_public_relative_location(monkeypatch):
    start = "https://bit.ly/x"
    calls = fake_http(monkeypatch, {start: (302, "/expanded", "")})
    assert asyncio.run(resolver._try_resolve_302(start, "synthetic-UA")) == "https://bit.ly/expanded"
    assert calls == [start]


def test_public_redirect_chain_resolves(monkeypatch):
    start, middle, end = "https://bit.ly/x", "https://public.example/mid", "https://public.example/end"
    calls = fake_http(monkeypatch, {
        start: (302, middle, ""), middle: (307, "/end", ""), end: (200, "", "done"),
    })
    assert asyncio.run(resolver._try_resolve_follow(start, "synthetic-UA")) == end
    assert calls == [start, middle, end]


def test_private_meta_refresh_is_rejected(monkeypatch):
    start = "https://bit.ly/x"
    calls = fake_http(monkeypatch, {start: (200, "", '<meta content="0;url=http://127.0.0.1/x">')})
    assert asyncio.run(resolver._try_resolve_302(start, "synthetic-UA")) is None
    assert calls == [start]


def test_redirect_limit_stops_cycles(monkeypatch):
    start = "https://bit.ly/x"
    calls = fake_http(monkeypatch, {start: (302, start, "")})
    assert asyncio.run(resolver._try_resolve_follow(start, "synthetic-UA")) is None
    assert len(calls) == resolver._FOLLOW_MAX_REDIRECTS + 1


def test_resolver_falls_back_to_original_for_private_target(monkeypatch):
    start = "https://bit.ly/x"
    calls = fake_http(monkeypatch, {start: (302, "http://127.0.0.1/x", "")})
    assert asyncio.run(resolver.resolve_short_url_async(start)) == start
    assert calls == [start, start, start]


def test_pinned_get_omits_default_https_port():
    class Client:
        async def get(self, url, **kwargs):
            self.kwargs = kwargs
            return httpx.Response(200)
    client = Client()
    asyncio.run(resolver._pinned_get(client, "https://bit.ly/x", {}))
    assert client.kwargs["headers"]["Host"] == "bit.ly"
    assert client.kwargs["extensions"]["sni_hostname"] == "bit.ly"
