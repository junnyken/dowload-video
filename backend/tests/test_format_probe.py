"""
POST /formats/probe — measured resolution/codec for TikWM streams.

No network: Redis is a dict, ffprobe is a fake subprocess, httpx uses a
MockTransport and DNS is stubbed.
"""
import asyncio
import json
import pathlib
import time

import httpx
import pytest

from app.core import ssrf_guard
from app.services import format_probe as fp

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ffprobe_tikwm_hdplay.json"
HD = "https://v19-notes.tiktokcdn-us.com/abc/6abfa75b/video/tos/x/?a=0&mime_type=video_mp4"
PLAY = "https://v16m.tiktokcdn-us.com/def/6abfa75b/video/tos/y/?a=1233"


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    def get(self, k):
        return self.store.get(k)

    def setex(self, k, ttl, v):
        self.store[k] = v
        self.ttls[k] = ttl

    def exists(self, k):
        return int(k in self.store)


class DeadRedis:
    def __getattr__(self, name):
        def _boom(*a, **k):
            raise ConnectionError("redis down")
        return _boom


@pytest.fixture
def redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(fp, "_get_redis", lambda: r)
    monkeypatch.setattr(fp, "_sem", None)
    return r


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda host: ("93.184.216.34",))


def _issue(redis, *urls):
    for u in urls:
        redis.setex(fp._ISSUED_PREFIX + fp.url_key(u), 60, "1")


# ── parsing ───────────────────────────────────────────────────────────

def test_parse_real_ffprobe_fixture():
    r = fp.parse_ffprobe_json(FIXTURE.read_text())
    assert r == {
        "width": 540, "height": 960, "vcodec": "hevc", "acodec": "aac",
        "bitrate_kbps": 1753, "duration": 14.17,
    }


def test_parse_rejects_audio_only_and_garbage():
    audio_only = {"streams": [{"codec_type": "audio", "codec_name": "mp3"}], "format": {}}
    with pytest.raises(fp.ProbeError, match="no_video_stream"):
        fp.parse_ffprobe_json(json.dumps(audio_only))
    with pytest.raises(fp.ProbeError, match="bad_probe_output"):
        fp.parse_ffprobe_json(b"not json")


# ── expiry-bounded TTL ────────────────────────────────────────────────

def test_ttl_never_outlives_signed_url():
    now = 1_790_923_507
    # Path form seen live: 6abfa75b == now + ~6h
    assert fp.url_expiry(HD, now) == 0x6abfa75b
    # 0x6abfa75b is 6h00m08s out, so the 6 h cap wins; a 2 h URL caps at 2 h.
    assert fp.ttl_for(HD, 6 * 3600, now) == 6 * 3600
    two_h = HD.replace("6abfa75b", format(now + 7200, "x"))
    assert fp.ttl_for(two_h, 6 * 3600, now) == 7200
    short = f"https://v16m.tiktokcdn-us.com/x/?x-expires={now + 120}"
    assert fp.ttl_for(short, 6 * 3600, now) == 120
    assert fp.ttl_for("https://v16m.tiktokcdn-us.com/x/", 6 * 3600, now) == 6 * 3600
    expired = f"https://v16m.tiktokcdn-us.com/x/?expires={now - 5}"
    assert fp.ttl_for(expired, 6 * 3600, now) == 0


def test_register_issued_urls_skips_non_https(redis):
    fp.register_issued_urls([HD, "http://v16m.tiktokcdn-us.com/a", ""])
    assert fp._was_issued(HD)
    assert not fp._was_issued("http://v16m.tiktokcdn-us.com/a")


# ── allowlist / SSRF ─────────────────────────────────────────────────

def test_check_url_rejections(redis):
    _issue(redis, HD, "http://v16m.tiktokcdn-us.com/a", "https://10.0.0.1/a",
           "https://evil.example/a", "https://tiktokcdn-us.com.evil.example/a")
    assert fp.check_url("http://v16m.tiktokcdn-us.com/a") == "scheme_not_allowed"
    assert fp.check_url("https://10.0.0.1/a") == "host_not_allowed"
    assert fp.check_url("https://evil.example/a") == "host_not_allowed"
    assert fp.check_url("https://tiktokcdn-us.com.evil.example/a") == "host_not_allowed"
    assert fp.check_url(PLAY) == "not_issued"      # right host, never issued
    assert fp.check_url(123) == "invalid_url"
    assert fp.check_url(HD) is None


def test_issued_check_fails_closed_without_redis(monkeypatch):
    monkeypatch.setattr(fp, "_get_redis", lambda: DeadRedis())
    assert fp.check_url(HD) == "not_issued"


def test_allowlisted_host_resolving_to_private_ip_is_blocked(redis, monkeypatch):
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda host: ("10.1.2.3",))
    _issue(redis, HD)
    called = []
    monkeypatch.setattr(fp, "_run_ffprobe", lambda u: called.append(u))
    out = asyncio.run(fp.probe_one(HD))
    assert out["error"] == "blocked_address"
    assert called == []


def _mock_httpx(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


def test_redirect_to_other_host_is_refused(redis, public_dns, monkeypatch):
    _issue(redis, HD)
    _mock_httpx(monkeypatch, lambda req: httpx.Response(
        302, headers={"location": "https://evil.example/steal"}))
    called = []

    async def _never(u):
        called.append(u)
        return b""
    monkeypatch.setattr(fp, "_run_ffprobe", _never)
    out = asyncio.run(fp.probe_one(HD))
    assert out["error"] == "redirect_host_not_allowed"
    assert called == []


def test_redirect_to_http_is_refused(redis, public_dns, monkeypatch):
    _issue(redis, HD)
    _mock_httpx(monkeypatch, lambda req: httpx.Response(
        302, headers={"location": "http://v16m.tiktokcdn-us.com/x"}))
    out = asyncio.run(fp.probe_one(HD))
    assert out["error"] == "redirect_scheme_not_allowed"


def test_ffprobe_gets_final_url_after_allowed_redirect(redis, public_dns, monkeypatch):
    wm = "https://api16-normal-useast5.tiktokv.us/aweme/v1/play/?video_id=v1"
    final = "https://v45-lite.tiktokcdn-us.com/z/video/?a=1"
    _issue(redis, wm)

    def handler(req):
        if req.url.host == "api16-normal-useast5.tiktokv.us":
            return httpx.Response(302, headers={"location": final})
        assert req.headers["range"] == "bytes=0-0"
        return httpx.Response(206, content=b"\x00")
    _mock_httpx(monkeypatch, handler)
    seen = []

    async def _probe(u):
        seen.append(u)
        return FIXTURE.read_bytes()
    monkeypatch.setattr(fp, "_run_ffprobe", _probe)
    out = asyncio.run(fp.probe_one(wm))
    assert seen == [final]
    assert out["height"] == 960 and out["vcodec"] == "hevc"


def test_ffprobe_argv_is_locked_down():
    args = fp._ffprobe_args(HD)
    assert args[args.index("-protocol_whitelist") + 1] == "https,tls,tcp"
    assert args[args.index("-rw_timeout") + 1] == "6000000"
    assert args[args.index("-f") + 1] == "mp4"
    assert args[-1] == HD


# ── cache ─────────────────────────────────────────────────────────────

def test_success_is_cached_and_cache_hit_skips_ffprobe(redis, monkeypatch):
    _issue(redis, HD)

    async def _resolve(u):
        return u
    monkeypatch.setattr(fp, "_resolve_final_url", _resolve)
    runs = []

    async def _probe(u):
        runs.append(u)
        return FIXTURE.read_bytes()
    monkeypatch.setattr(fp, "_run_ffprobe", _probe)

    first = asyncio.run(fp.probe_one(HD))
    assert first["width"] == 540 and "cached" not in first
    key = fp._RESULT_PREFIX + fp.url_key(HD)
    assert key in redis.store
    assert redis.ttls[key] <= 6 * 3600

    second = asyncio.run(fp.probe_one(HD))
    assert second["cached"] is True and second["height"] == 960
    assert len(runs) == 1


def test_redis_down_still_computes_when_issued_check_passes(monkeypatch):
    # Cache layer is fail-soft: a cache read/write error must not block a probe.
    monkeypatch.setattr(fp, "_sem", None)
    monkeypatch.setattr(fp, "check_url", lambda u: None)
    monkeypatch.setattr(fp, "_get_redis", lambda: DeadRedis())

    async def _resolve(u):
        return u

    async def _probe(u):
        return FIXTURE.read_bytes()
    monkeypatch.setattr(fp, "_resolve_final_url", _resolve)
    monkeypatch.setattr(fp, "_run_ffprobe", _probe)
    out = asyncio.run(fp.probe_one(HD))
    assert out["width"] == 540


# ── timeout ───────────────────────────────────────────────────────────

def test_hung_ffprobe_times_out_and_is_killed(redis, monkeypatch):
    _issue(redis, HD)
    monkeypatch.setattr(fp, "PROC_TIMEOUT_S", 0.2)

    async def _resolve(u):
        return u
    monkeypatch.setattr(fp, "_resolve_final_url", _resolve)

    class HungProc:
        returncode = None
        killed = False

        async def communicate(self):
            await asyncio.sleep(60)

        def kill(self):
            HungProc.killed = True

        async def wait(self):
            return -9

    async def _spawn(*a, **k):
        return HungProc()
    monkeypatch.setattr(fp.asyncio, "create_subprocess_exec", _spawn)

    t0 = time.monotonic()
    out = asyncio.run(fp.probe_one(HD))
    assert out["error"] == "timeout"
    assert HungProc.killed
    assert time.monotonic() - t0 < 5


def test_nonzero_exit_is_an_error_not_a_guess(redis, monkeypatch):
    _issue(redis, HD)

    async def _resolve(u):
        return u
    monkeypatch.setattr(fp, "_resolve_final_url", _resolve)

    class FailProc:
        returncode = 1

        async def communicate(self):
            return b"", b"403 Forbidden"

    async def _spawn(*a, **k):
        return FailProc()
    monkeypatch.setattr(fp.asyncio, "create_subprocess_exec", _spawn)
    out = asyncio.run(fp.probe_one(HD))
    assert out == {"url": HD, "error": "probe_failed"}


# ── endpoint ──────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_endpoint_caps_at_four_urls(client, redis):
    r = client.post("/api/v1/formats/probe", json={"urls": [HD] * 5})
    assert r.status_code == 400


def test_endpoint_rejects_empty(client, redis):
    assert client.post("/api/v1/formats/probe", json={"urls": []}).status_code == 400


def test_endpoint_returns_per_url_errors_for_unissued(client, redis):
    r = client.post("/api/v1/formats/probe",
                    json={"urls": [HD, "http://x.tiktokcdn-us.com/a", "https://127.0.0.1/a"]})
    assert r.status_code == 200
    errs = [x["error"] for x in r.json()["results"]]
    assert errs == ["not_issued", "scheme_not_allowed", "host_not_allowed"]
