"""
POST /formats/probe — measured resolution/codec for TikWM streams.

No network: Redis is a dict, ffprobe is a fake subprocess, httpx uses a
MockTransport, getaddrinfo is stubbed. The one TLS test talks to a server on
127.0.0.1 inside the test process.
"""
import asyncio
import json
import pathlib
import re
import socket
import ssl
import time

import httpx
import pytest

from app.core import ssrf_guard
from app.services import format_probe as fp

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ffprobe_tikwm_hdplay.json"
# The path segment is the CDN expiry (hex epoch). It used to be the literal
# 6abfa75b captured live, which expired at 2026-10-02 12:45 UTC and turned two
# tests red from then on. Derive it from "now + 6h" so the fixtures never age.
_EXP = int(time.time()) + 6 * 3600
_EXP_HEX = format(_EXP, "08x")
HD = f"https://v19-notes.tiktokcdn-us.com/abc/{_EXP_HEX}/video/tos/x/?a=0&mime_type=video_mp4"
PLAY = f"https://v16m.tiktokcdn-us.com/def/{_EXP_HEX}/video/tos/y/?a=1233"


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
    # Fixed clock, so a fixed URL: the live-captured path form, 6abfa75b == now + ~6h.
    hd_fixed = "https://v19-notes.tiktokcdn-us.com/abc/6abfa75b/video/tos/x/?a=0&mime_type=video_mp4"
    assert fp.url_expiry(hd_fixed, now) == 0x6abfa75b
    # 0x6abfa75b is 6h00m08s out, so the 6 h cap wins; a 2 h URL caps at 2 h.
    assert fp.ttl_for(hd_fixed, 6 * 3600, now) == 6 * 3600
    two_h = hd_fixed.replace("6abfa75b", format(now + 7200, "x"))
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


# ── network layer: resolve-once, pin, bounded fetch ─────────────────

def _box(typ: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + typ + payload


FTYP = _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2")


def _mp4(moov_payload=4000, mdat_payload=500_000, moov_first=True) -> bytes:
    moov = _box(b"moov", b"\x11" * moov_payload)
    mdat = _box(b"mdat", b"\x22" * mdat_payload)
    return FTYP + (moov + mdat if moov_first else mdat + moov)


class DNS:
    """Stub for socket.getaddrinfo. `answers[host]` is a list of answer lists,
    consumed one per call (the last repeats) — lets a test model rebinding."""

    def __init__(self, answers):
        self.answers = {h: list(v) for h, v in answers.items()}
        self.calls = []

    def __call__(self, host, port, *a, **k):
        self.calls.append(host)
        seq = self.answers.get(host)
        if not seq:
            raise socket.gaierror("no such host")
        ips = seq.pop(0) if len(seq) > 1 else seq[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]


@pytest.fixture
def dns(monkeypatch):
    def install(answers):
        d = DNS(answers)
        monkeypatch.setattr(fp.socket, "getaddrinfo", d)
        return d
    return install


class Chunks(httpx.AsyncByteStream):
    """Async body that yields `chunk`-sized slices and counts what was pulled."""

    def __init__(self, data=None, chunk=65536, infinite=False):
        self.data, self.chunk, self.infinite = data, chunk, infinite
        self.pulled = 0

    async def __aiter__(self):
        if self.infinite:
            if self.data:
                self.pulled += len(self.data)
                yield self.data
            while True:
                self.pulled += self.chunk
                yield b"\x00" * self.chunk
        for i in range(0, len(self.data), self.chunk):
            part = self.data[i:i + self.chunk]
            self.pulled += len(part)
            yield part


def _serve_range(blob, req, chunk=65536):
    m = re.match(r"bytes=(\d+)-(\d+)", req.headers["range"])
    lo, hi = int(m.group(1)), min(int(m.group(2)), len(blob) - 1)
    return httpx.Response(
        206, headers={"content-range": f"bytes {lo}-{hi}/{len(blob)}"},
        stream=Chunks(blob[lo:hi + 1], chunk))


def _mock_httpx(monkeypatch, handler):
    real = httpx.AsyncClient
    seen = []

    def wrapped(req):
        seen.append(req)
        return handler(req)
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(wrapped), **kw),
    )
    return seen


def _fake_ffprobe(monkeypatch):
    fed = []

    async def _probe(data):
        fed.append(data)
        return FIXTURE.read_bytes()
    monkeypatch.setattr(fp, "_run_ffprobe", _probe)
    return fed


def test_ip_rule_is_shared_with_ssrf_guard():
    for bad in ("10.1.2.3", "127.0.0.1", "169.254.169.254", "100.64.0.1", "::1", "fd00::1", "nope"):
        assert not ssrf_guard.ip_is_public(bad)
    assert ssrf_guard.ip_is_public("93.184.216.34")


def test_allowlisted_host_resolving_to_private_ip_is_blocked(redis, dns, monkeypatch):
    dns({"v19-notes.tiktokcdn-us.com": [["10.1.2.3"]]})
    _issue(redis, HD)
    reqs = _mock_httpx(monkeypatch, lambda req: httpx.Response(500))
    fed = _fake_ffprobe(monkeypatch)
    out = asyncio.run(fp.probe_one(HD))
    assert out["error"] == "blocked_address"
    assert reqs == [] and fed == []


def test_any_private_record_blocks_even_if_first_is_public(redis, dns, monkeypatch):
    dns({"v19-notes.tiktokcdn-us.com": [["93.184.216.34", "127.0.0.1"]]})
    _issue(redis, HD)
    reqs = _mock_httpx(monkeypatch, lambda req: httpx.Response(500))
    assert asyncio.run(fp.probe_one(HD))["error"] == "blocked_address"
    assert reqs == []


def test_redirect_to_other_host_is_refused(redis, dns, monkeypatch):
    d = dns({"v19-notes.tiktokcdn-us.com": [["93.184.216.34"]]})
    _issue(redis, HD)
    _mock_httpx(monkeypatch, lambda req: httpx.Response(
        302, headers={"location": "https://evil.example/steal"}))
    fed = _fake_ffprobe(monkeypatch)
    out = asyncio.run(fp.probe_one(HD))
    assert out["error"] == "redirect_host_not_allowed"
    assert fed == []
    assert "evil.example" not in d.calls          # never even resolved


def test_redirect_to_http_is_refused(redis, dns, monkeypatch):
    dns({"v19-notes.tiktokcdn-us.com": [["93.184.216.34"]]})
    _issue(redis, HD)
    _mock_httpx(monkeypatch, lambda req: httpx.Response(
        302, headers={"location": "http://v16m.tiktokcdn-us.com/x"}))
    out = asyncio.run(fp.probe_one(HD))
    assert out["error"] == "redirect_scheme_not_allowed"


def test_redirect_chain_is_capped(redis, dns, monkeypatch):
    dns({"v19-notes.tiktokcdn-us.com": [["93.184.216.34"]]})
    _issue(redis, HD)
    reqs = _mock_httpx(monkeypatch, lambda req: httpx.Response(302, headers={"location": HD}))
    assert asyncio.run(fp.probe_one(HD))["error"] == "too_many_redirects"
    assert len(reqs) == fp.MAX_REDIRECTS + 1


def test_rebinding_resolve_once_per_hop_and_connect_to_that_ip(redis, dns, monkeypatch):
    wm = "https://api16-normal-useast5.tiktokv.us/aweme/v1/play/?video_id=v1"
    final = "https://v45-lite.tiktokcdn-us.com/z/video/?a=1"
    _issue(redis, wm)
    # Every later lookup of either name would answer with a private address.
    d = dns({
        "api16-normal-useast5.tiktokv.us": [["23.44.175.72"], ["127.0.0.1"]],
        "v45-lite.tiktokcdn-us.com": [["192.64.15.2"], ["169.254.169.254"]],
    })
    blob = _mp4()

    def handler(req):
        if req.headers["host"] == "api16-normal-useast5.tiktokv.us":
            return httpx.Response(302, headers={"location": final})
        return _serve_range(blob, req)
    reqs = _mock_httpx(monkeypatch, handler)
    fed = _fake_ffprobe(monkeypatch)

    out = asyncio.run(fp.probe_one(wm))
    assert out["height"] == 960 and out["vcodec"] == "hevc"
    assert d.calls == ["api16-normal-useast5.tiktokv.us", "v45-lite.tiktokcdn-us.com"]
    assert [(r.url.host, r.headers["host"], r.extensions["sni_hostname"]) for r in reqs] == [
        ("23.44.175.72", "api16-normal-useast5.tiktokv.us", "api16-normal-useast5.tiktokv.us"),
        ("192.64.15.2", "v45-lite.tiktokcdn-us.com", "v45-lite.tiktokcdn-us.com"),
    ]
    assert reqs[1].url.path == "/z/video/" and reqs[1].url.query == b"a=1"
    assert reqs[1].headers["range"] == f"bytes=0-{fp.PREFIX_BYTES - 1}"
    # ffprobe got the bytes; reading stopped once moov was complete.
    assert fed[0].startswith(FTYP)
    assert len(fed[0]) < 100_000
    # bitrate comes from the Content-Range total, as with ffprobe-on-URL.
    assert out["bitrate_kbps"] == round(len(blob) * 8 / 14.17 / 1000)


def test_connect_failure_falls_back_to_next_validated_ip_without_reresolving(redis, dns, monkeypatch):
    d = dns({"v19-notes.tiktokcdn-us.com": [["2.17.106.100", "2.17.106.113"]]})
    _issue(redis, HD)
    blob = _mp4()

    def handler(req):
        if req.url.host == "2.17.106.100":
            raise httpx.ConnectTimeout("boom", request=req)
        return _serve_range(blob, req)
    reqs = _mock_httpx(monkeypatch, handler)
    _fake_ffprobe(monkeypatch)
    assert asyncio.run(fp.probe_one(HD))["width"] == 540
    assert d.calls == ["v19-notes.tiktokcdn-us.com"]
    assert [r.url.host for r in reqs] == ["2.17.106.100", "2.17.106.113"]


def test_byte_cap_is_enforced_mid_stream(monkeypatch):
    # Server ignores Range and streams forever; we must stop at the budget.
    body = Chunks(infinite=True, chunk=65536)
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200, stream=body))
    budget = fp._Budget(300_000)

    async def go():
        async with fp._new_client() as c:
            return await fp._range_get(c, HD, "v19-notes.tiktokcdn-us.com", 443,
                                       "93.184.216.34", 0, 10**9, budget)
    kind, data, _total = asyncio.run(go())
    assert kind == "data" and len(data) == 300_000
    assert budget.read == 300_000 and budget.left == 0
    assert body.pulled <= 300_000 + 65536            # stopped at the chunk that crossed


def test_prefix_without_moov_stops_at_prefix_cap(redis, dns, monkeypatch):
    # A huge 'free' box before anything else: never find moov, never exceed PREFIX_BYTES.
    dns({"v19-notes.tiktokcdn-us.com": [["93.184.216.34"]]})
    _issue(redis, HD)
    body = Chunks(FTYP + (50 * 2**20).to_bytes(4, "big") + b"free", chunk=65536, infinite=True)
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200, stream=body))
    fed = _fake_ffprobe(monkeypatch)
    out = asyncio.run(fp.probe_one(HD))
    assert out["error"] == "moov_not_in_prefix"
    assert fed == []
    assert body.pulled <= fp.PREFIX_BYTES + 65536


def test_moov_after_mdat_gives_moov_not_in_prefix(redis, dns, monkeypatch):
    dns({"v19-notes.tiktokcdn-us.com": [["93.184.216.34"]]})
    _issue(redis, HD)
    blob = _mp4(mdat_payload=3_000_000, moov_first=False)
    bodies = []

    def handler(req):
        r = _serve_range(blob, req)
        bodies.append(r.stream)
        return r
    reqs = _mock_httpx(monkeypatch, handler)
    fed = _fake_ffprobe(monkeypatch)
    out = asyncio.run(fp.probe_one(HD))
    assert out["error"] == "moov_not_in_prefix"
    assert fed == [] and len(reqs) == 1
    assert bodies[0].pulled <= 65536             # saw mdat header, stopped


def test_moov_straddling_prefix_gets_one_bounded_followup(redis, dns, monkeypatch):
    monkeypatch.setattr(fp, "PREFIX_BYTES", 10_000)
    dns({"v19-notes.tiktokcdn-us.com": [["93.184.216.34"]]})
    _issue(redis, HD)
    blob = _mp4(moov_payload=30_000)
    moov_end = len(FTYP) + 8 + 30_000
    reqs = _mock_httpx(monkeypatch, lambda req: _serve_range(blob, req, chunk=4096))
    fed = _fake_ffprobe(monkeypatch)
    assert asyncio.run(fp.probe_one(HD))["width"] == 540
    assert [r.headers["range"] for r in reqs] == ["bytes=0-9999", f"bytes=10000-{moov_end - 1}"]
    assert fed[0] == blob[:moov_end]


def test_moov_larger_than_total_cap_is_refused_without_followup(redis, dns, monkeypatch):
    monkeypatch.setattr(fp, "PREFIX_BYTES", 10_000)
    monkeypatch.setattr(fp, "MAX_TOTAL_BYTES", 20_000)
    dns({"v19-notes.tiktokcdn-us.com": [["93.184.216.34"]]})
    _issue(redis, HD)
    blob = _mp4(moov_payload=30_000)
    reqs = _mock_httpx(monkeypatch, lambda req: _serve_range(blob, req))
    assert asyncio.run(fp.probe_one(HD))["error"] == "moov_not_in_prefix"
    assert len(reqs) == 1


def test_scan_moov_verdicts():
    blob = _mp4()
    v, end = fp.scan_moov(blob)
    assert v == "ok" and end == len(FTYP) + 8 + 4000
    assert fp.scan_moov(blob[:len(FTYP) + 100])[0] == "partial"
    assert fp.scan_moov(blob[:len(FTYP) + 4])[0] == "need_more"
    assert fp.scan_moov(_mp4(moov_first=False))[0] == "after_mdat"
    assert fp.scan_moov(b"\x00\x00\x00\x02junk")[0] == "bad"


def test_client_never_trusts_env_and_verifies_tls():
    c = fp._new_client()
    try:
        assert c.trust_env is False
        assert c.follow_redirects is False
    finally:
        asyncio.run(c.aclose())
    assert fp._tls_verify() is True


# ── TLS: connecting to the IP still verifies the cert against the hostname ──

def _make_ca_and_leaf(tmp_path, leaf_name):
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "probe-test-ca")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(1)
          .not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, leaf_name)]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(2)
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(leaf_name)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    ca_pem = tmp_path / "ca.pem"
    ca_pem.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_pem = tmp_path / "leaf.pem"
    cert_pem.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_pem = tmp_path / "leaf.key"
    key_pem.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                          serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return ca_pem, cert_pem, key_pem


def test_pinned_ip_still_verifies_certificate_hostname(tmp_path, monkeypatch):
    good = "good.tiktokcdn-us.com"
    ca_pem, cert_pem, key_pem = _make_ca_and_leaf(tmp_path, good)
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert_pem, key_pem)
    sni_seen, host_seen = [], []
    server_ctx.sni_callback = lambda sock, name, ctx: sni_seen.append(name)
    monkeypatch.setattr(fp, "_tls_verify", lambda: ssl.create_default_context(cafile=str(ca_pem)))

    async def handle(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            host_seen.append(re.search(rb"(?im)^host:\s*(\S+)", head).group(1).decode())
            writer.write(b"HTTP/1.1 206 Partial Content\r\nContent-Range: bytes 0-3/4\r\n"
                         b"Content-Length: 4\r\nConnection: close\r\n\r\nabcd")
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    async def go(name):
        server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_ctx)
        port = server.sockets[0].getsockname()[1]
        try:
            async with fp._new_client() as c:
                return await fp._range_get(c, f"https://{name}:{port}/v", name, port,
                                           "127.0.0.1", 0, 3, fp._Budget(100))
        finally:
            server.close()
            await server.wait_closed()

    # Right name: TCP goes to 127.0.0.1, SNI + cert check + Host use the name.
    kind, data, total = asyncio.run(go(good))
    assert (kind, data, total) == ("data", b"abcd", 4)
    assert sni_seen == [good] and host_seen[0].startswith(good + ":")

    # Same IP, same cert, different name: the handshake must fail on hostname.
    with pytest.raises(httpx.ConnectError, match="(?i)certificate verify failed|hostname"):
        asyncio.run(go("evil.tiktokcdn-us.com"))
    assert len(host_seen) == 1        # no HTTP request was ever sent


# ── ffprobe never touches the network ───────────────────────────────

def test_ffprobe_argv_has_no_url_and_pipe_only():
    args = fp._ffprobe_args()
    assert args[args.index("-protocol_whitelist") + 1] == "pipe"
    assert args[args.index("-f") + 1] == "mp4"
    assert args[args.index("-i") + 1] == "pipe:0"
    assert "-show_streams" in args and "-show_format" in args
    assert not any("://" in a or "http" in a or "tcp" in a or "tls" in a for a in args)


def test_run_ffprobe_feeds_bytes_on_stdin(monkeypatch):
    seen = {}

    class Proc:
        returncode = 0

        async def communicate(self, input=None):
            seen["input"] = input
            return FIXTURE.read_bytes(), b""

    async def _spawn(*args, **kw):
        seen["args"], seen["kw"] = args, kw
        return Proc()
    monkeypatch.setattr(fp.asyncio, "create_subprocess_exec", _spawn)
    out = asyncio.run(fp._run_ffprobe(b"MP4BYTES"))
    assert json.loads(out)["streams"]
    assert seen["input"] == b"MP4BYTES"
    assert seen["kw"]["stdin"] == asyncio.subprocess.PIPE
    assert list(seen["args"]) == fp._ffprobe_args()


def test_parse_prefers_http_total_size_for_bitrate():
    r = fp.parse_ffprobe_json(FIXTURE.read_text(), size_bytes=3103774)
    assert r["bitrate_kbps"] == 1752 or r["bitrate_kbps"] == 1753


# ── cache ─────────────────────────────────────────────────────────────

def test_success_is_cached_and_cache_hit_skips_ffprobe(redis, monkeypatch):
    _issue(redis, HD)

    async def _fetch(u):
        return b"MP4", 3103774, 3
    monkeypatch.setattr(fp, "_fetch_prefix", _fetch)
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

    async def _fetch(u):
        return b"MP4", 3103774, 3

    async def _probe(u):
        return FIXTURE.read_bytes()
    monkeypatch.setattr(fp, "_fetch_prefix", _fetch)
    monkeypatch.setattr(fp, "_run_ffprobe", _probe)
    out = asyncio.run(fp.probe_one(HD))
    assert out["width"] == 540


# ── timeout ───────────────────────────────────────────────────────────

def test_hung_ffprobe_times_out_and_is_killed(redis, monkeypatch):
    _issue(redis, HD)
    monkeypatch.setattr(fp, "PROC_TIMEOUT_S", 0.2)

    async def _fetch(u):
        return b"MP4", 3103774, 3
    monkeypatch.setattr(fp, "_fetch_prefix", _fetch)

    class HungProc:
        returncode = None
        killed = False

        async def communicate(self, input=None):
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

    async def _fetch(u):
        return b"MP4", 3103774, 3
    monkeypatch.setattr(fp, "_fetch_prefix", _fetch)

    class FailProc:
        returncode = 1

        async def communicate(self, input=None):
            return b"", b"moov atom not found"

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
