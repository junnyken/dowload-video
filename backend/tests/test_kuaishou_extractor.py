"""
Kuaishou scaffold (OFF by default, UNVERIFIED) — no network in any test.

Every page below is SYNTHETIC: hand-built from the PUBLIC descriptions cited
in app/services/kuaishou_extractor.py (KS-Downloader, lux, sharextract #24,
an aliyun write-up). None of it was captured from kuaishou.com — the site
has never answered us (geo-block from outside mainland China, 2026-10-05).
They test OUR parser's logic and fail-closed behaviour, not that the site
really looks like this. Replace with redacted real captures from
scripts/kuaishou_capture.py once a Chinese network path exists.

Hosts/IPs: 1.1.1.1 / 8.8.8.8 are real public addresses on purpose —
ipaddress treats the TEST-NET ranges (203.0.113.x, …) as private.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.services import kuaishou_extractor as ks
from app.services.kuaishou_extractor import KuaishouError

PUBLIC_IP = "1.1.1.1"
VID = "3x2wdee2f2ud7ac"  # the id from the owner's (unreachable) test link


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.delenv(ks.FLAG_ENV, raising=False)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv(ks.FLAG_ENV, "true")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test may reach the real resolver or a real HTTP client."""
    def _no_dns(*a, **k):
        raise AssertionError("real DNS used in a test")

    def _no_http(*a, **k):
        raise AssertionError("real HTTP client used in a test")
    monkeypatch.setattr(ks, "_default_resolve", _no_dns)
    monkeypatch.setattr(ks, "_default_client_factory", _no_http)
    monkeypatch.delenv(ks.PROXY_ENV, raising=False)
    monkeypatch.setattr(ks, "_pool_cookies", lambda: {})


def public_resolver(host, port):
    return [PUBLIC_IP]


# ── SYNTHETIC fixtures ────────────────────────────────────────────────────

def SYNTHETIC_apollo_page(photo_id=VID, *, photo_url="https://cdn-media.example-ks.net/v/abc.mp4?sign=x",
                          extra_nodes=None, drop_photo_url=False):
    """SYNTHETIC — shape per KS-Downloader HTMLExtractor (web) / sharextract #24."""
    node = {
        "id": photo_id,
        "caption": "SYNTHETIC caption 合成",
        "coverUrl": "https://cdn-img.example-ks.net/c/abc.jpg",
        "duration": 15200,
        "photoUrl": photo_url,
    }
    if drop_photo_url:
        node.pop("photoUrl")
    client = {
        f"VisionVideoDetailPhoto:{photo_id}": node,
        "VisionVideoDetailAuthor:SYNTH_AUTHOR": {"id": "SYNTH_AUTHOR", "name": "SYNTHETIC author"},
        "$ROOT_QUERY.visionVideoDetail": {"photo": {"type": "id", "id": f"VisionVideoDetailPhoto:{photo_id}"}},
    }
    client.update(extra_nodes or {})
    state = json.dumps({"defaultClient": client}, ensure_ascii=False).replace("/", "\\u002F")
    return ("<html><head><title>SYNTHETIC page</title></head><body><script>"
            f"window.__APOLLO_STATE__={state};(function(){{var s;}}());</script></body></html>")


def SYNTHETIC_init_state_page(photo):
    """SYNTHETIC — shape per KS-Downloader HTMLExtractor (app) + aliyun write-up."""
    state = {"SYNTH_HASH_KEY": {"result": 1, "photo": photo, "serialInfo": {}}}
    return ("<html><body><script>window.INIT_STATE = "
            + json.dumps(state, ensure_ascii=False) + "</script></body></html>")


SYNTHETIC_VIDEO_PHOTO = {
    "photoId": VID,
    "photoType": "VIDEO",
    "caption": "SYNTHETIC share caption",
    "userName": "SYNTHETIC user",
    "duration": 9000,
    "coverUrls": [{"cdn": "x", "url": "https://cdn-img.example-ks.net/c/1.jpg"}],
    "mainMvUrls": [{"cdn": "a", "url": "https://cdn-media.example-ks.net/v/1.mp4"},
                   {"cdn": "b", "url": "https://cdn-media2.example-ks.net/v/1.mp4"}],
}

SYNTHETIC_ATLAS_PHOTO = {
    "photoId": "3xatlas0001",
    "photoType": "VERTICAL_ATLAS",
    "caption": "SYNTHETIC album",
    "coverUrls": [{"url": "https://cdn-img.example-ks.net/c/2.jpg"}],
    "ext_params": {"atlas": {"cdn": ["cdn-img.example-ks.net"],
                             "list": ["/ufile/atlas/1.jpg", "/ufile/atlas/2.jpg"]}},
}


# ── mock HTTP plumbing ────────────────────────────────────────────────────

class Recorder:
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.factory_kwargs: list[dict] = []

    def factory(self, *, proxy, timeout):
        self.factory_kwargs.append({"proxy": proxy, "timeout": timeout})

        def _h(request):
            self.requests.append(request)
            return self.handler(request)
        return httpx.Client(transport=httpx.MockTransport(_h), follow_redirects=False)


def html_response(body, status=200, headers=None):
    return httpx.Response(status, headers={"content-type": "text/html; charset=utf-8", **(headers or {})},
                          content=body.encode("utf-8"))


def logical_host(request: httpx.Request) -> str:
    return request.headers.get("host", request.url.host)


# ═══════════════════════════════════════════════════════════════════
# 1. Flag OFF — behaviour exactly as before
# ═══════════════════════════════════════════════════════════════════

KS_URLS = [
    f"https://www.kuaishou.com/short-video/{VID}",
    "https://www.kuaishou.com/f/X7aBcDeF",
    "https://v.kuaishou.com/AbCd12",
    f"https://www.kuaishou.com/video/{VID}",
    "https://v.m.chenzhongtech.com/fw/photo/3xvsedz34m82ppi?userId=SYNTH",
    "https://m.gifshow.com/fw/photo/3xvsedz34m82ppi",
    "https://www.kuaishou.com/fw/photo/3xvsedz34m82ppi",
]


class TestFlagOff:

    def test_flag_default_is_off(self, flag_off):
        assert ks.kuaishou_enabled() is False
        for u in KS_URLS:
            assert ks.should_handle(u) is False

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "", "  ", "enabled", "2"])
    def test_non_truthy_values_are_off(self, monkeypatch, val):
        monkeypatch.setenv(ks.FLAG_ENV, val)
        assert ks.kuaishou_enabled() is False

    def test_registry_does_not_list_or_detect(self, flag_off):
        from app.services.extractor_registry import REGISTRY
        names = [p["platform"] for p in REGISTRY.all_platforms()]
        assert "Kuaishou" not in names
        for u in KS_URLS:
            ext = REGISTRY.detect_safe(u)
            assert ext is None or ext.platform_name != "Kuaishou"

    def test_registration_is_skipped(self, flag_off):
        from app.services.extractor_registry import ExtractorRegistry
        reg = ExtractorRegistry()
        assert ks.register_if_enabled(reg) is False
        assert len(reg) == 0

    def test_classifier_and_capabilities_do_not_know_it(self, flag_off):
        from app.core.source_classifier import classify
        from app.services.container_registry import get_all_platforms, get_full_matrix
        for u in KS_URLS:
            assert classify(u).platform == "unknown"
        assert "kuaishou" not in get_all_platforms()
        assert "kuaishou" not in get_full_matrix()

    def test_normalizer_short_link_flag_unchanged(self, flag_off):
        from app.core.url_normalizer import normalize
        assert normalize("https://v.kuaishou.com/AbCd12").is_short_link is False

    def test_public_endpoints_do_not_advertise(self, flag_off, app):
        r = app.get("/api/v1/platforms")
        assert r.status_code == 200
        assert "kuaishou" not in r.text.lower()
        r = app.get("/api/v1/platforms/capabilities")
        assert r.status_code == 200
        assert "kuaishou" not in r.text.lower()
        # /resolve-input is served by app.api.container (registered first).
        r = app.post("/api/v1/resolve-input", json={"url": KS_URLS[0]})
        assert r.status_code == 200, r.text
        assert r.json()["platform"] == "unknown"
        assert "kuaishou" not in r.text.lower().replace(KS_URLS[0].lower(), "")

    def test_no_probe_target(self):
        from app.core import platform_probe
        assert "kuaishou" not in platform_probe.PROBE_PLATFORMS
        assert "kuaishou" not in platform_probe._DEFAULT_TARGETS

    def test_downloader_takes_generic_path(self, flag_off, monkeypatch):
        """OFF: the Kuaishou extractor is never called; yt-dlp (generic) gets the URL."""
        from app.services import downloader

        def _must_not_run(*a, **k):
            raise AssertionError("Kuaishou extractor called while flag is OFF")
        monkeypatch.setattr(ks, "extract_kuaishou_download_info", _must_not_run)

        class _Reached(Exception):
            pass

        reached = []

        class _FakeYDL:
            def __init__(self, *a, **k):
                reached.append(1)
                raise _Reached()
        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", _FakeYDL)
        monkeypatch.setattr(downloader, "is_cobalt_available", lambda *a, **k: False, raising=False)
        with pytest.raises(Exception) as ei:
            downloader._extract_video_info_impl(KS_URLS[0], "video_fast")
        assert reached, "the generic yt-dlp path was not reached"
        assert not isinstance(ei.value, KuaishouError)
        assert "kuaishou_" not in str(ei.value)


# ═══════════════════════════════════════════════════════════════════
# 2. Flag ON — URL recognition
# ═══════════════════════════════════════════════════════════════════

class TestUrlRecognition:

    @pytest.mark.parametrize("url,kind,code", [
        (f"https://www.kuaishou.com/short-video/{VID}", "short_video", VID),
        (f"https://kuaishou.com/short-video/{VID}?authorId=x&streamSource=find", "short_video", VID),
        (f"http://www.kuaishou.cn/short-video/{VID}", "short_video", VID),
        (f"https://www.kuaishou.com/video/{VID}", "video", VID),
        ("https://www.kuaishou.com/f/X7aBcDeF", "share_f", "X7aBcDeF"),
        ("https://v.kuaishou.com/AbCd12", "short_link", "AbCd12"),
        ("https://v.kuaishou.cn/AbCd12/", "short_link", "AbCd12"),
        ("https://v.m.chenzhongtech.com/fw/photo/3xvsedz34m82ppi?userId=1", "fw_photo", "3xvsedz34m82ppi"),
        ("https://m.gifshow.com/fw/photo/3xvsedz34m82ppi", "fw_photo", "3xvsedz34m82ppi"),
        ("https://www.kuaishou.com/fw/photo/3xvsedz34m82ppi", "fw_photo", "3xvsedz34m82ppi"),
        ("  https://WWW.KUAISHOU.COM/short-video/3xABCdef  ", "short_video", "3xABCdef"),
    ])
    def test_recognised(self, flag_on, url, kind, code):
        link = ks.parse_kuaishou_url(url)
        assert link is not None and (link.kind, link.code) == (kind, code)
        assert link.url.startswith("https://")
        assert ks.should_handle(url)

    @pytest.mark.parametrize("url", [
        "https://kuaishou.com.evil.com/short-video/3xabcdef",
        "https://evilkuaishou.com/short-video/3xabcdef",
        "https://www.kuaishou.com.cn.evil.net/f/abcdef",
        "https://kuaishou.com@evil.com/short-video/3xabcdef",
        "https://user:pw@www.kuaishou.com/short-video/3xabcdef",
        "https://www.kuaishou.com:8443/short-video/3xabcdef",
        "ftp://www.kuaishou.com/short-video/3xabcdef",
        "javascript://www.kuaishou.com/short-video/3xabcdef",
        "https://www.kuaishou.com/",
        "https://www.kuaishou.com/profile/3xuser",
        "https://www.kuaishou.com/short-video/",
        "https://www.kuaishou.com/short-video/../../etc",
        "https://v.kuaishou.com/a/b/c",
        "https://notkuaishou.cn/short-video/3xabcdef",
        "https://gifshow.com.evil.io/fw/photo/3xabcdef",
        "https://www.chenzhongtech.com/short-video/3xabcdef",   # short-video only on kuaishou.*
        "https://www.tiktok.com/@x/video/1",
        "",
        None,
        "not a url",
    ])
    def test_rejected(self, flag_on, url):
        assert ks.parse_kuaishou_url(url) is None
        assert ks.should_handle(url) is False

    def test_classifier_on(self, flag_on):
        from app.core.source_classifier import classify
        c = classify(f"https://www.kuaishou.com/short-video/{VID}")
        assert (c.platform, c.source_type, c.normalized_id) == ("kuaishou", "single_video", VID)
        assert classify("https://v.kuaishou.com/AbCd12").source_type == "single_post"
        assert classify("https://kuaishou.com.evil.com/short-video/3xabcdef").platform != "kuaishou"

    def test_capability_on_is_experimental(self, flag_on):
        from app.services.container_registry import get_capability
        cap = get_capability("kuaishou", "single_video")
        assert cap.support_level.value == "experimental"
        assert cap.requirements.proxy_required

    def test_registration_on(self, flag_on, monkeypatch):
        from app.services.extractor_registry import ExtractorRegistry
        reg = ExtractorRegistry()
        assert ks.register_if_enabled(reg) is True
        assert ks.register_if_enabled(reg) is True and len(reg) == 1   # idempotent
        assert reg.detect(KS_URLS[0]).platform_name == "Kuaishou"
        assert [p["platform"] for p in reg.all_platforms()] == ["Kuaishou"]
        # Flag switched off after registration → hidden again.
        monkeypatch.delenv(ks.FLAG_ENV)
        assert reg.all_platforms() == []
        assert reg.detect_safe(KS_URLS[0]) is None

    def test_downloader_routes_here(self, flag_on, monkeypatch):
        from app.services import downloader
        seen = {}

        def _fake(url, quality="video"):
            seen["url"] = url
            return {"title": "t", "direct_mp4_url": "https://x/y.mp4"}
        monkeypatch.setattr(ks, "extract_kuaishou_download_info", _fake)
        out = downloader._extract_video_info_impl(KS_URLS[0], "video_fast")
        assert seen["url"] == KS_URLS[0] and out["title"] == "t"

    def test_normalizer_short_link_on(self, flag_on):
        from app.core.url_normalizer import normalize
        assert normalize("https://v.kuaishou.com/AbCd12").is_short_link is True


# ═══════════════════════════════════════════════════════════════════
# 3. Network: SSRF, redirects, pinning, timeouts, proxy secrecy
# ═══════════════════════════════════════════════════════════════════

class TestNetwork:

    def test_private_ip_rejected_before_any_request(self):
        rec = Recorder(lambda r: html_response("never"))
        with pytest.raises(KuaishouError) as ei:
            ks.fetch_page(KS_URLS[0], resolver=lambda h, p: ["10.0.0.5"], client_factory=rec.factory)
        assert ei.value.error_code == "kuaishou_unsafe_address"
        assert rec.requests == []

    @pytest.mark.parametrize("addrs", [["127.0.0.1"], ["169.254.169.254"], [PUBLIC_IP, "192.168.1.1"],
                                       ["100.64.0.1"], ["::1"]])
    def test_any_non_public_answer_rejected(self, addrs):
        with pytest.raises(KuaishouError) as ei:
            ks.fetch_page(KS_URLS[0], resolver=lambda h, p: addrs,
                          client_factory=Recorder(lambda r: html_response("x")).factory)
        assert ei.value.error_code == "kuaishou_unsafe_address"

    @pytest.mark.parametrize("location,code", [
        ("https://evil.example.com/short-video/3xabcdef", "kuaishou_redirect_rejected"),
        ("http://www.kuaishou.com/short-video/3xabcdef", "kuaishou_redirect_rejected"),
        ("https://127.0.0.1/latest/meta-data", "kuaishou_redirect_rejected"),
        ("https://kuaishou.com.evil.com/x", "kuaishou_redirect_rejected"),
        ("https://www.kuaishou.com:8080/x", "kuaishou_redirect_rejected"),
        ("ftp://www.kuaishou.com/x", "kuaishou_redirect_rejected"),
    ])
    def test_bad_redirect_rejected_without_following(self, location, code):
        rec = Recorder(lambda r: httpx.Response(302, headers={"location": location}))
        with pytest.raises(KuaishouError) as ei:
            ks.fetch_page("https://v.kuaishou.com/AbCd12", resolver=public_resolver,
                          client_factory=rec.factory)
        assert ei.value.error_code == code
        assert len(rec.requests) == 1

    def test_redirect_to_private_resolving_kuaishou_host_rejected(self):
        def resolver(host, port):
            return ["10.1.2.3"] if host == "internal.kuaishou.com" else [PUBLIC_IP]
        rec = Recorder(lambda r: httpx.Response(302, headers={"location": "https://internal.kuaishou.com/x"}))
        with pytest.raises(KuaishouError) as ei:
            ks.fetch_page("https://v.kuaishou.com/AbCd12", resolver=resolver, client_factory=rec.factory)
        assert ei.value.error_code == "kuaishou_unsafe_address"
        assert len(rec.requests) == 1

    def test_max_three_redirects(self):
        n = {"i": 0}

        def handler(r):
            n["i"] += 1
            return httpx.Response(302, headers={"location": f"https://www.kuaishou.com/f/hop{n['i']}x"})
        rec = Recorder(handler)
        with pytest.raises(KuaishouError) as ei:
            ks.fetch_page("https://v.kuaishou.com/AbCd12", resolver=public_resolver, client_factory=rec.factory)
        assert ei.value.error_code == "kuaishou_too_many_redirects"
        assert len(rec.requests) == ks.MAX_REDIRECTS + 1

    def test_three_redirects_then_page_ok(self):
        def handler(r):
            path = r.url.path
            if path == "/AbCd12":
                return httpx.Response(302, headers={"location": "https://www.kuaishou.com/f/one111"})
            if path == "/f/one111":
                return httpx.Response(301, headers={"location": "/f/two222"})
            if path == "/f/two222":
                return httpx.Response(302, headers={"location": f"https://www.kuaishou.com/short-video/{VID}"})
            return html_response("ok")
        rec = Recorder(handler)
        res = ks.fetch_page("https://v.kuaishou.com/AbCd12", resolver=public_resolver, client_factory=rec.factory)
        assert res.final_url == f"https://www.kuaishou.com/short-video/{VID}"
        assert res.hop_hosts == ["v.kuaishou.com", "www.kuaishou.com", "www.kuaishou.com", "www.kuaishou.com"]

    def test_connection_pinned_to_validated_ip_without_proxy(self):
        rec = Recorder(lambda r: html_response("ok"))
        ks.fetch_page(KS_URLS[0], resolver=public_resolver, client_factory=rec.factory)
        req = rec.requests[0]
        assert req.url.host == PUBLIC_IP
        assert req.headers["host"] == "www.kuaishou.com"
        assert req.extensions.get("sni_hostname") == "www.kuaishou.com"
        assert rec.factory_kwargs[0]["proxy"] is None

    def test_timeouts_are_bounded(self):
        rec = Recorder(lambda r: html_response("ok"))
        ks.fetch_page(KS_URLS[0], resolver=public_resolver, client_factory=rec.factory)
        t = rec.factory_kwargs[0]["timeout"]
        assert t.connect <= 8.0 and t.read <= 25.0

    @pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"),
                                     httpx.PoolTimeout("slow")])
    def test_timeout_is_geo_blocked(self, exc):
        def handler(r):
            raise exc
        with pytest.raises(KuaishouError) as ei:
            ks.fetch_page(KS_URLS[0], resolver=public_resolver, client_factory=Recorder(handler).factory)
        assert ei.value.error_code == "kuaishou_geo_blocked"

    @pytest.mark.parametrize("status,code", [(403, "kuaishou_geo_blocked"), (451, "kuaishou_geo_blocked"),
                                             (429, "kuaishou_challenge_page"), (404, "kuaishou_not_found"),
                                             (410, "kuaishou_not_found"), (500, "kuaishou_upstream_unreachable"),
                                             (204, "kuaishou_upstream_unreachable")])
    def test_status_mapping(self, status, code):
        rec = Recorder(lambda r: html_response("blocked", status=status))
        with pytest.raises(KuaishouError) as ei:
            ks.fetch_page(KS_URLS[0], resolver=public_resolver, client_factory=rec.factory)
        assert ei.value.error_code == code

    def test_body_cap(self, monkeypatch):
        monkeypatch.setattr(ks, "MAX_BODY_BYTES", 1000)
        rec = Recorder(lambda r: html_response("x" * 5000))
        with pytest.raises(KuaishouError) as ei:
            ks.fetch_page(KS_URLS[0], resolver=public_resolver, client_factory=rec.factory)
        assert ei.value.error_code == "kuaishou_response_too_large"

    def test_unresolvable_without_proxy(self):
        def resolver(h, p):
            raise OSError("nxdomain")
        with pytest.raises(KuaishouError) as ei:
            ks.fetch_page(KS_URLS[0], resolver=resolver, client_factory=Recorder(lambda r: html_response("x")).factory)
        assert ei.value.error_code == "kuaishou_upstream_unreachable"


SECRET_PROXY = "http://synthuser:S3cretProxyPass@cn-proxy.synthetic.invalid:31280"


class TestProxySecrecy:

    @pytest.fixture
    def with_proxy(self, monkeypatch):
        monkeypatch.setenv(ks.PROXY_ENV, SECRET_PROXY)

    def _assert_clean(self, text):
        assert "S3cretProxyPass" not in text
        assert "synthuser" not in text
        assert "cn-proxy.synthetic.invalid" not in text

    @pytest.mark.parametrize("exc_factory,code", [
        (lambda: httpx.ProxyError(f"proxy {SECRET_PROXY} refused CONNECT"), "kuaishou_proxy_error"),
        (lambda: httpx.ConnectError(f"cannot reach {SECRET_PROXY}"), "kuaishou_proxy_error"),
        (lambda: httpx.ReadTimeout(f"timeout via {SECRET_PROXY}"), "kuaishou_geo_blocked"),
        (lambda: httpx.RemoteProtocolError(f"bad reply from {SECRET_PROXY}"), "kuaishou_upstream_unreachable"),
    ])
    def test_proxy_never_in_exception_or_logs(self, with_proxy, caplog, monkeypatch, exc_factory, code):
        def handler(r):
            raise exc_factory()
        rec = Recorder(handler)
        monkeypatch.setattr(ks, "_default_client_factory", rec.factory)
        monkeypatch.setattr(ks, "_default_resolve", public_resolver)
        caplog.set_level(logging.DEBUG)
        with pytest.raises(KuaishouError) as ei:
            ks.extract_kuaishou_download_info(KS_URLS[0])
        e = ei.value
        assert e.error_code == code
        self._assert_clean(str(e))
        self._assert_clean(e.detail)
        self._assert_clean(repr(e))
        self._assert_clean(caplog.text)
        assert e.__cause__ is None and e.__suppress_context__ is True

    def test_proxy_is_used_and_not_pinned(self, with_proxy, caplog):
        caplog.set_level(logging.DEBUG)
        rec = Recorder(lambda r: html_response(SYNTHETIC_apollo_page()))
        res = ks.fetch_page(KS_URLS[0], resolver=lambda h, p: [], client_factory=rec.factory)
        assert rec.factory_kwargs[0]["proxy"] == SECRET_PROXY
        assert rec.requests[0].url.host == "www.kuaishou.com"
        assert res.via_proxy
        self._assert_clean(caplog.text)

    def test_proxy_still_refuses_private_answer(self, with_proxy):
        with pytest.raises(KuaishouError) as ei:
            ks.fetch_page(KS_URLS[0], resolver=lambda h, p: ["10.0.0.1"],
                          client_factory=Recorder(lambda r: html_response("x")).factory)
        assert ei.value.error_code == "kuaishou_unsafe_address"

    def test_redact_helper(self, with_proxy):
        self._assert_clean(ks.redact(f"x {SECRET_PROXY} y"))
        assert "S3cret" not in ks.redact("socks5://a:S3cretOther@h:1")


# ═══════════════════════════════════════════════════════════════════
# 4. Parser strategies against SYNTHETIC fixtures
# ═══════════════════════════════════════════════════════════════════

class TestApolloStrategy:

    def test_ok(self):
        o = ks.strategy_apollo_state(SYNTHETIC_apollo_page(), VID)
        assert o.ok, o.reason
        p = o.post
        assert p.title == "SYNTHETIC caption 合成"
        assert p.video_urls == ["https://cdn-media.example-ks.net/v/abc.mp4?sign=x"]   # / decoded
        assert p.thumbnail == "https://cdn-img.example-ks.net/c/abc.jpg"
        assert p.duration_ms == 15200 and p.author == "SYNTHETIC author"

    def test_id_not_present(self):
        o = ks.strategy_apollo_state(SYNTHETIC_apollo_page(photo_id="3xOTHER0001"), VID)
        assert not o.ok and f"no VisionVideoDetailPhoto node for id {VID}" in o.reason

    def test_two_nodes_without_id_is_ambiguous(self):
        extra = {"VisionVideoDetailPhoto:3xOTHER0001": {"photoUrl": "https://cdn-media.example-ks.net/o.mp4"}}
        o = ks.strategy_apollo_state(SYNTHETIC_apollo_page(extra_nodes=extra), None)
        assert not o.ok and "2 VisionVideoDetailPhoto nodes" in o.reason

    def test_missing_photo_url(self):
        o = ks.strategy_apollo_state(SYNTHETIC_apollo_page(drop_photo_url=True), VID)
        assert not o.ok and "photoUrl missing" in o.reason

    def test_invalid_json(self):
        o = ks.strategy_apollo_state("<script>window.__APOLLO_STATE__={broken: undefined};</script>", VID)
        assert not o.ok and "not valid JSON" in o.reason

    def test_no_marker(self):
        o = ks.strategy_apollo_state("<html></html>", VID)
        assert not o.ok and "not found" in o.reason


class TestInitStateStrategy:

    def test_video(self):
        o = ks.strategy_init_state(SYNTHETIC_init_state_page(SYNTHETIC_VIDEO_PHOTO), VID)
        assert o.ok, o.reason
        assert o.post.post_type == "video"
        assert o.post.video_urls[0] == "https://cdn-media.example-ks.net/v/1.mp4"
        assert o.post.thumbnail == "https://cdn-img.example-ks.net/c/1.jpg"
        assert o.post.title == "SYNTHETIC share caption" and o.post.duration_ms == 9000

    def test_atlas(self):
        o = ks.strategy_init_state(SYNTHETIC_init_state_page(SYNTHETIC_ATLAS_PHOTO), None)
        assert o.ok, o.reason
        assert o.post.post_type == "images"
        assert o.post.image_urls == ["https://cdn-img.example-ks.net/ufile/atlas/1.jpg",
                                     "https://cdn-img.example-ks.net/ufile/atlas/2.jpg"]

    @pytest.mark.parametrize("cdn", ["evil.com/x", "a@evil.com", "", "   "])
    def test_atlas_bad_cdn(self, cdn):
        photo = json.loads(json.dumps(SYNTHETIC_ATLAS_PHOTO))
        photo["ext_params"]["atlas"]["cdn"] = [cdn]
        o = ks.strategy_init_state(SYNTHETIC_init_state_page(photo), None)
        assert not o.ok and "atlas.cdn" in o.reason

    @pytest.mark.parametrize("path", ["ufile/1.jpg", "//evil.com/1.jpg", 5])
    def test_atlas_bad_path(self, path):
        photo = json.loads(json.dumps(SYNTHETIC_ATLAS_PHOTO))
        photo["ext_params"]["atlas"]["list"] = [path]
        o = ks.strategy_init_state(SYNTHETIC_init_state_page(photo), None)
        assert not o.ok and "atlas.list" in o.reason

    def test_unknown_type_is_unsupported(self):
        photo = dict(SYNTHETIC_VIDEO_PHOTO, photoType="LIVE_STREAM")
        o = ks.strategy_init_state(SYNTHETIC_init_state_page(photo), VID)
        assert not o.ok and o.unsupported_type and "LIVE_STREAM" in o.reason

    def test_video_type_without_urls(self):
        photo = dict(SYNTHETIC_VIDEO_PHOTO, mainMvUrls=[])
        o = ks.strategy_init_state(SYNTHETIC_init_state_page(photo), VID)
        assert not o.ok and "mainMvUrls missing" in o.reason

    def test_two_photos_ambiguous_without_matching_id(self):
        state = {"a": {"photo": dict(SYNTHETIC_VIDEO_PHOTO, photoId="3xAAAA")},
                 "b": {"photo": dict(SYNTHETIC_VIDEO_PHOTO, photoId="3xBBBB")}}
        html = "<script>window.INIT_STATE = " + json.dumps(state) + "</script>"
        o = ks.strategy_init_state(html, "3xCCCC")
        assert not o.ok and "cannot tell" in o.reason
        o2 = ks.strategy_init_state(html, "3xBBBB")
        assert o2.ok and o2.post.photo_id == "3xBBBB"


class TestPhotoUrlRegexStrategy:

    def test_single(self):
        html = '<title>SYNTHETIC &amp; title</title><script>{"photoUrl":"https:\\u002F\\u002Fcdn-media.example-ks.net\\u002Fv.mp4"}</script>'
        o = ks.strategy_photo_url_regex(html, None)
        assert o.ok and o.post.video_urls == ["https://cdn-media.example-ks.net/v.mp4"]
        assert o.post.title == "SYNTHETIC & title"

    def test_two_distinct_fail(self):
        html = '"photoUrl":"https://a.example-ks.net/1.mp4" "photoUrl":"https://a.example-ks.net/2.mp4"'
        o = ks.strategy_photo_url_regex(html, None)
        assert not o.ok and "2 distinct" in o.reason

    def test_none(self):
        assert not ks.strategy_photo_url_regex("<html/>", None).ok


class TestFailClosed:

    @pytest.mark.parametrize("html", ["", "<html></html>", "\x00\xff garbage",
                                      "window.__APOLLO_STATE__=", "window.INIT_STATE = {",
                                      "window.INIT_STATE = []"])
    def test_every_strategy_fails_precisely(self, html):
        outs = ks.run_strategies(html, VID)
        assert [o.name for o in outs] == ["apollo_state", "init_state", "photo_url_regex"]
        for o in outs:
            assert o.ok is False and o.post is None and o.reason

    def test_crashing_strategy_is_reported_not_raised(self, monkeypatch):
        def boom(html, pid):
            raise RuntimeError("bug")
        monkeypatch.setattr(ks, "STRATEGIES", (boom,))
        outs = ks.run_strategies("x", None)
        assert outs[0].ok is False and "crashed" in outs[0].reason

    def test_order_prefers_apollo(self):
        html = SYNTHETIC_apollo_page() + SYNTHETIC_init_state_page(SYNTHETIC_VIDEO_PHOTO)
        outs = ks.run_strategies(html, VID)
        assert outs[0].ok and outs[1].ok
        assert ks._pick(outs).strategy == "apollo_state"


# ═══════════════════════════════════════════════════════════════════
# 5. Media URL validation
# ═══════════════════════════════════════════════════════════════════

class TestMediaValidation:

    def test_ok(self):
        assert ks.validate_media_url("https://cdn-media.example-ks.net/v.mp4?x=1",
                                     resolver=public_resolver).startswith("https://")

    @pytest.mark.parametrize("url,resolver", [
        ("http://cdn-media.example-ks.net/v.mp4", public_resolver),
        ("ftp://cdn-media.example-ks.net/v.mp4", public_resolver),
        ("https://u:p@cdn-media.example-ks.net/v.mp4", public_resolver),
        ("https://cdn-media.example-ks.net:8443/v.mp4", public_resolver),
        ("https://127.0.0.1/v.mp4", public_resolver),
        ("https://[::1]/v.mp4", public_resolver),
        ("https://8.8.8.8/v.mp4", public_resolver),
        ("https://localhost/v.mp4", public_resolver),
        ("https://redis/v.mp4", public_resolver),
        ("https://cdn-media.example-ks.net/v.mp4", lambda h, p: ["10.0.0.9"]),
        ("https://cdn-media.example-ks.net/v.mp4", lambda h, p: [PUBLIC_IP, "172.16.0.1"]),
        ("https://cdn-media.example-ks.net/v.mp4", lambda h, p: []),
        ("", public_resolver),
        (None, public_resolver),
    ])
    def test_rejected(self, url, resolver):
        with pytest.raises(KuaishouError) as ei:
            ks.validate_media_url(url, resolver=resolver)
        assert ei.value.error_code == "kuaishou_media_url_rejected"


# ═══════════════════════════════════════════════════════════════════
# 6. End to end (mocked transport) + error classification
# ═══════════════════════════════════════════════════════════════════

class TestEndToEnd:

    def test_short_link_to_video(self):
        def handler(r):
            if logical_host(r) == "v.kuaishou.com":
                return httpx.Response(302, headers={"location": f"https://www.kuaishou.com/short-video/{VID}"})
            return html_response(SYNTHETIC_apollo_page())
        rec = Recorder(handler)
        rep = ks.ExtractionReport()
        post = ks.extract_post("https://v.kuaishou.com/AbCd12", resolver=public_resolver,
                               client_factory=rec.factory, report=rep)
        assert post.strategy == "apollo_state" and post.photo_id == VID
        info = ks.to_download_info(post, "https://v.kuaishou.com/AbCd12")
        assert info["direct_mp4_url"] == "https://cdn-media.example-ks.net/v/abc.mp4?sign=x"
        assert info["duration"] == 15 and info["platform"] == "kuaishou"
        assert info["available_formats"][0]["type"] == "video"

    def test_atlas_download_info(self):
        rec = Recorder(lambda r: html_response(SYNTHETIC_init_state_page(SYNTHETIC_ATLAS_PHOTO)))
        post = ks.extract_post("https://m.gifshow.com/fw/photo/3xatlas0001", resolver=public_resolver,
                               client_factory=rec.factory)
        info = ks.to_download_info(post, "u")
        assert info["media_count"] == 2 and [f["type"] for f in info["available_formats"]] == ["image", "image"]

    def test_site_cookie_replayed_once_never_invented(self):
        calls = []

        def handler(r):
            calls.append(r.headers.get("cookie"))
            if len(calls) == 1:
                return html_response("<html>no state</html>",
                                     headers={"set-cookie": "did=web_SYNTHETIC_from_site; Path=/; HttpOnly"})
            return html_response(SYNTHETIC_apollo_page())
        rep = ks.ExtractionReport()
        post = ks.extract_post(KS_URLS[0], resolver=public_resolver, client_factory=Recorder(handler).factory,
                               report=rep)
        assert post.video_urls and rep.retried_with_site_cookies
        assert calls == [None, "did=web_SYNTHETIC_from_site"]

    def test_no_cookie_set_means_no_retry(self):
        calls = []

        def handler(r):
            calls.append(r.headers.get("cookie"))
            return html_response("<html>no state</html>")
        with pytest.raises(KuaishouError) as ei:
            ks.extract_post(KS_URLS[0], resolver=public_resolver, client_factory=Recorder(handler).factory)
        assert ei.value.error_code == "kuaishou_parse_failed"
        assert calls == [None]
        # The internal detail names each strategy's precise reason.
        for name in ("apollo_state", "init_state", "photo_url_regex"):
            assert name in ei.value.detail

    def test_challenge_final_url(self):
        def handler(r):
            if r.url.path.startswith("/short-video"):
                return httpx.Response(302, headers={"location": "https://captcha.kuaishou.com/verify?x=1"})
            return html_response("<html>slide to verify</html>")
        with pytest.raises(KuaishouError) as ei:
            ks.extract_post(KS_URLS[0], resolver=public_resolver, client_factory=Recorder(handler).factory)
        assert ei.value.error_code == "kuaishou_challenge_page"

    def test_unsupported_post_type(self):
        photo = dict(SYNTHETIC_VIDEO_PHOTO, photoType="LIVE_STREAM")
        rec = Recorder(lambda r: html_response(SYNTHETIC_init_state_page(photo)))
        with pytest.raises(KuaishouError) as ei:
            ks.extract_post(KS_URLS[4], resolver=public_resolver, client_factory=rec.factory)
        assert ei.value.error_code == "kuaishou_unsupported_post_type"

    def test_media_resolving_private_is_rejected(self):
        def resolver(host, port):
            return ["10.0.0.7"] if host.startswith("cdn-media") else [PUBLIC_IP]
        rec = Recorder(lambda r: html_response(SYNTHETIC_apollo_page()))
        with pytest.raises(KuaishouError) as ei:
            ks.extract_post(KS_URLS[0], resolver=resolver, client_factory=rec.factory)
        assert ei.value.error_code == "kuaishou_media_url_rejected"

    def test_bad_thumbnail_dropped_not_fatal(self):
        def resolver(host, port):
            return ["10.0.0.7"] if host.startswith("cdn-img") else [PUBLIC_IP]
        rec = Recorder(lambda r: html_response(SYNTHETIC_apollo_page()))
        post = ks.extract_post(KS_URLS[0], resolver=resolver, client_factory=rec.factory)
        assert post.thumbnail == "" and post.video_urls

    def test_invalid_input_url(self):
        with pytest.raises(KuaishouError) as ei:
            ks.extract_post("https://kuaishou.com.evil.com/short-video/3xabcdef")
        assert ei.value.error_code == "kuaishou_invalid_url"

    def test_registry_extractor_returns_error_result(self, flag_on, monkeypatch):
        def _raise(*a, **k):
            raise KuaishouError("kuaishou_geo_blocked", "t")
        monkeypatch.setattr(ks, "extract_post", _raise)
        r = ks.KuaishouExtractor().extract_single(KS_URLS[0])
        assert r.error_code == "kuaishou_geo_blocked" and "Trung Quốc" in r.error_message


# Words /fetch-link rewrites a message on (routes.py) or classifies by keyword.
_TRIGGER_WORDS = ("unavailable", "sign in", "bot", "private", "drm", "widevine", "encrypted",
                  "protection", "clearkey", "copyright", "dmca", "[generic]", "riêng tư")


class TestErrorClassification:

    def test_every_code_has_status_and_catalogue_entry(self):
        from app.core.error_codes import ERROR_META
        from app.core.extraction_errors import _EXTRACTOR_CODE_STATUS
        for code in ks.ERROR_MESSAGES:
            assert code in _EXTRACTOR_CODE_STATUS, code
            assert code in ERROR_META, code
            assert ERROR_META[code]["user_message"] == ks.ERROR_MESSAGES[code], code

    @pytest.mark.parametrize("code", list(ks.ERROR_MESSAGES))
    def test_message_survives_route_rewrite(self, code):
        text = str(KuaishouError(code, "detail")).lower()
        for w in _TRIGGER_WORDS:
            assert w not in text, (code, w)

    def test_geo_blocked_is_503_by_exc_and_by_text(self):
        from app.core.extraction_errors import classify_extraction_error
        e = KuaishouError("kuaishou_geo_blocked", "ReadTimeout")
        assert classify_extraction_error(str(e), e) == (503, "kuaishou_geo_blocked")
        # Celery keeps only the string.
        assert classify_extraction_error(str(e)) == (503, "kuaishou_geo_blocked")

    def test_geo_message_wording(self):
        m = ks.ERROR_MESSAGES["kuaishou_geo_blocked"]
        assert "Trung Quốc" in m and "proxy" in m and "Link của bạn không có lỗi" in m

    def test_geo_blocked_via_fetch_link_route(self, flag_on, app, monkeypatch, tmp_path):
        import app.api.routes as routes
        from app.core import disk_guardrail, ssrf_guard
        monkeypatch.setattr(disk_guardrail, "_DOWNLOAD_DIR", str(tmp_path))
        monkeypatch.setattr(disk_guardrail, "check_can_accept_job", lambda *a, **k: (True, ""))
        monkeypatch.setattr(routes, "_preflight_disk_check", lambda: None)
        monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))

        async def _raise(*a, **k):
            raise KuaishouError("kuaishou_geo_blocked", "ReadTimeout")
        monkeypatch.setattr(routes, "extract_video_info", _raise)
        r = app.post("/api/v1/fetch-link", json={"url": KS_URLS[0], "quality": "video"})
        assert r.status_code == 503, r.text
        body = r.json()
        assert body["error_code"] == "kuaishou_geo_blocked"
        assert body["detail"].startswith(ks.ERROR_MESSAGES["kuaishou_geo_blocked"])

    def test_cookie_pool_cooldown(self):
        from app.core.cookie_pool import _COOLDOWN
        assert _COOLDOWN["kuaishou"] == 20


# ═══════════════════════════════════════════════════════════════════
# 7. Capture tool (offline, SYNTHETIC page)
# ═══════════════════════════════════════════════════════════════════

def _load_capture_module():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "kuaishou_capture.py"
    spec = importlib.util.spec_from_file_location("kuaishou_capture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCaptureTool:

    def test_redaction(self):
        cap = _load_capture_module()
        raw = ('{"did":"web_abc123","userId":12345,"userName":"Real Person","photoId":"3xKEEP",'
               '"VisionVideoDetailAuthor:3xAUTHOR":{"id":"3xAUTHOR"},'
               '"photoUrl":"https:\\u002F\\u002Fcdn.example-ks.net\\u002Fv.mp4?sign=SECRET1\\u0026x-ks-ptid=SECRET2",'
               '"coverUrl":"https://cdn.example-ks.net/c.jpg?tk=SECRET3&amp;b=SECRET4"}')
        out = cap.redact_capture(raw)
        for secret in ("web_abc123", "12345", "Real Person", "3xAUTHOR", "SECRET1", "SECRET2",
                       "SECRET3", "SECRET4"):
            assert secret not in out, secret
        assert "3xKEEP" in out and "cdn.example-ks.net" in out

    def test_offline_run_reports_pass_and_saves_redacted(self, monkeypatch, tmp_path, capsys):
        cap = _load_capture_module()
        rec = Recorder(lambda r: html_response(SYNTHETIC_apollo_page()))
        monkeypatch.setattr(ks, "_default_client_factory", rec.factory)
        monkeypatch.setattr(ks, "_default_resolve", public_resolver)
        monkeypatch.setenv(ks.PROXY_ENV, SECRET_PROXY)
        out_file = tmp_path / "cap.html"
        rc = cap.main([KS_URLS[0], "--out", str(out_file)])
        printed = capsys.readouterr().out
        assert rc == 0
        assert "[PASS] Strategy apollo_state" in printed
        assert "cdn-media.example-ks.net" in printed
        assert "sign=x" not in printed                       # never the signed URL
        assert "S3cretProxyPass" not in printed and "synthuser" not in printed
        saved = out_file.read_text(encoding="utf-8")
        assert "REDACTED Kuaishou capture" in saved and "SYNTHETIC author" not in saved
        # The redacted capture still parses (usable as a fixture).
        assert ks.strategy_apollo_state(saved, VID).ok

    def test_offline_geo_block_reports_fail(self, monkeypatch, tmp_path, capsys):
        cap = _load_capture_module()

        def handler(r):
            raise httpx.ReadTimeout("tarpit")
        monkeypatch.setattr(ks, "_default_client_factory", Recorder(handler).factory)
        monkeypatch.setattr(ks, "_default_resolve", public_resolver)
        rc = cap.main([KS_URLS[0], "--no-save"])
        printed = capsys.readouterr().out
        assert rc == 1
        assert "[FAIL] Fetch page: kuaishou_geo_blocked" in printed
