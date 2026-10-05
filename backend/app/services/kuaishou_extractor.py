"""
Kuaishou (快手) extractor — SCAFFOLD, OFF by default, UNVERIFIED
================================================================

STATUS: nothing in this module has ever been run against the live site.
Kuaishou did not answer (read timeout) from the workspace or from the
production server, both outside mainland China (checked 2026-10-05), and no
Chinese network path exists yet. Every parsing strategy below is built from
public write-ups / open-source code that we READ, and is UNVERIFIED until a
real page has been captured with `scripts/kuaishou_capture.py` through a
Chinese proxy. See docs/KUAISHOU.md.

Feature flag:
    KUAISHOU_ENABLED   (default off; 1/true/yes/on enables)
    OFF → this module does nothing: the extractor is not registered, the
          downloader never routes here, the classifier and capability matrix
          do not know the platform. Kuaishou links keep failing in the
          generic yt-dlp path exactly as before (unsupported_url).
    ON  → /fetch-link routes Kuaishou links here.

Network:
    KUAISHOU_PROXY_CN  optional proxy URL, passed to httpx as-is
                       (http://[user:pass@]host:port, https://..., or socks5://
                       when socksio is installed — same as XIAOHONGSHU_PROXY_CN).
                       The value is NEVER logged or put in an exception text.
    Timeouts: connect 8 s, at most 25 s per request, and one shared budget for
    the whole extraction (all hops + the cookie retry) so it ends before the
    30 s /fetch-link metadata timeout.
    A timeout or a 403/451 block page → `kuaishou_geo_blocked` (HTTP 503).

Redirects are followed by hand (max 3 hops). Every hop must be https, on the
Kuaishou host allowlist, and resolve only to public addresses (ssrf_guard
rules); without a proxy the connection is pinned to the validated IP (the
format_probe approach), so there is no second DNS lookup to rebind.

Anti-bot: no `did` / device id is generated here. The only public description
of the device cookie (lux, below) simply replays whatever cookies the site
itself set on a first response, which is what `_extract` does once. The web
GraphQL endpoint (www.kuaishou.com/graphql, `visionVideoDetail`) is NOT used:
public write-ups show it needs a site-issued `did` plus private parameters,
and we will not invent signatures.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import re
import socket
import time
from dataclasses import dataclass, field
from html import unescape as _html_unescape
from typing import Any, Callable, Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

PLATFORM_NAME = "Kuaishou"
PLATFORM_SLUG = "kuaishou"

FLAG_ENV = "KUAISHOU_ENABLED"
PROXY_ENV = "KUAISHOU_PROXY_CN"

CONNECT_TIMEOUT_S = 8.0
REQUEST_TIMEOUT_S = 25.0           # hard cap for one request (one hop)
EXTRACTION_BUDGET_S = 27.0         # all hops + retry; < the 30 s fetch-link metadata cap
MAX_REDIRECTS = 3
MAX_BODY_BYTES = 4 * 1024 * 1024   # a share page is ~160 KB per public write-ups
MAX_CONNECT_TRIES = 2              # validated IPs tried per hop

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def kuaishou_enabled() -> bool:
    """Read at call time, so the flag needs no restart to be honoured in tests."""
    return (os.getenv(FLAG_ENV, "") or "").strip().lower() in _TRUTHY


# ═══════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════
# HTTP status for each code lives in app.core.extraction_errors
# (_EXTRACTOR_CODE_STATUS); the catalogue entry in app.core.error_codes.
# tests/test_kuaishou_extractor.py checks the three stay in sync.
#
# Wording rule: none of these messages may contain the words the /fetch-link
# handler rewrites on ("unavailable", "sign in", "bot", "private", "drm",
# "copyright", ...), or the user would get a YouTube/DRM message instead.

ERROR_MESSAGES: dict[str, str] = {
    "kuaishou_geo_blocked": (
        "Kuaishou chỉ trả lời các truy cập từ bên trong Trung Quốc. Máy chủ cần "
        "một proxy đặt tại Trung Quốc để tải được nội dung này. Link của bạn "
        "không có lỗi."
    ),
    "kuaishou_proxy_error": (
        "Máy chủ không kết nối được tới Kuaishou qua proxy Trung Quốc đã cấu hình. "
        "Quản trị viên cần kiểm tra proxy. Link của bạn không có lỗi."
    ),
    "kuaishou_upstream_unreachable": (
        "Máy chủ không kết nối được tới Kuaishou lúc này. Vui lòng thử lại sau. "
        "Link của bạn không có lỗi."
    ),
    "kuaishou_challenge_page": (
        "Kuaishou đang yêu cầu máy chủ xác minh (mã captcha) nên chưa lấy được "
        "video. Vui lòng thử lại sau. Link của bạn không có lỗi."
    ),
    "kuaishou_invalid_url": (
        "Link Kuaishou này không đúng dạng hỗ trợ. Hãy dán link một video công khai, "
        "ví dụ kuaishou.com/short-video/... hoặc link chia sẻ v.kuaishou.com/..."
    ),
    "kuaishou_redirect_rejected": (
        "Link chia sẻ này không dẫn tới một trang video Kuaishou nên hệ thống "
        "không mở tiếp."
    ),
    "kuaishou_too_many_redirects": (
        "Kuaishou chuyển hướng quá nhiều lần nên hệ thống dừng lại. Vui lòng thử lại sau."
    ),
    "kuaishou_unsafe_address": (
        "Địa chỉ Kuaishou trả về trỏ tới một mạng không công khai nên máy chủ từ chối mở."
    ),
    "kuaishou_not_found": (
        "Không tìm thấy video Kuaishou này. Video có thể đã bị xoá hoặc "
        "không còn công khai."
    ),
    "kuaishou_parse_failed": (
        "Đã mở được trang Kuaishou nhưng không đọc được dữ liệu video. Kuaishou có "
        "thể đã đổi cấu trúc trang; tính năng tải Kuaishou đang thử nghiệm."
    ),
    "kuaishou_unsupported_post_type": (
        "Bài đăng Kuaishou này không phải video hay album ảnh mà hệ thống đọc được."
    ),
    "kuaishou_media_url_rejected": (
        "Địa chỉ tệp mà Kuaishou trả về không vượt qua kiểm tra an toàn nên đã bị từ chối."
    ),
    "kuaishou_response_too_large": (
        "Trang Kuaishou trả về lớn bất thường nên hệ thống dừng đọc. Vui lòng thử lại sau."
    ),
}


class KuaishouError(ValueError):
    """
    A classified Kuaishou failure.

    str(e) is the Vietnamese user message plus the "(Lý do kỹ thuật: <code>)"
    suffix the downloader already uses, so the code survives anything that
    only keeps the text (Celery, logs) and extraction_errors can classify it.
    `detail` is internal, already redacted, and never shown to the user.
    """

    def __init__(self, code: str, detail: str = ""):
        if code not in ERROR_MESSAGES:
            code = "kuaishou_parse_failed"
        self.error_code = code
        self.user_message = ERROR_MESSAGES[code]
        self.detail = redact(detail)
        super().__init__(f"{self.user_message} (Lý do kỹ thuật: {code})")


# ── Secret redaction ──────────────────────────────────────────────────────

_CRED_IN_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")


def _proxy_url() -> str:
    return (os.getenv(PROXY_ENV, "") or "").strip()


def redact(text: Any) -> str:
    """Remove the configured proxy URL (whole, and its credentials) from text."""
    s = str(text or "")
    proxy = _proxy_url()
    if proxy:
        s = s.replace(proxy, "<proxy>")
        try:
            p = urlparse(proxy)
            for part in (p.password, p.username, p.netloc, p.hostname):
                if part and len(part) >= 3:
                    s = s.replace(part, "<redacted>")
        except Exception:
            pass
    return _CRED_IN_URL.sub(r"\1<redacted>@", s)


# ═══════════════════════════════════════════════════════════════════
# URL recognition
# ═══════════════════════════════════════════════════════════════════
# Forms and where they are documented (all read 2026-10-05):
#   kuaishou.(com|cn)/short-video/<id>   KS-Downloader source/link/examiner.py
#   kuaishou.(com|cn)/f/<code>           same file (F_SHORT_URL), redirect link
#   v.kuaishou.(com|cn)/<code>           same file (V_SHORT_URL), redirect link
#   kuaishou.(com|cn)/fw/photo/<id>      same file (C_DETAIL_URL)
#   *.chenzhongtech.(com|cn)/fw/photo/<id>  same file (REDIRECT_DETAIL_URL)
#   m.gifshow.com/fw/photo/<id>          seen as a published share URL in
#       github.com/Colinchiu007/mulpub/pull/2573
#   kuaishou.com/video/<id>              requested by the owner; NOT found in
#       any public source we read — kept, UNVERIFIED.
# https://github.com/JoeanAmier/KS-Downloader/blob/master/source/link/examiner.py

PAGE_HOST_SUFFIXES = (
    "kuaishou.com", "kuaishou.cn",
    "chenzhongtech.com", "chenzhongtech.cn",
    "gifshow.com",
)
_KS_BASES = ("kuaishou.com", "kuaishou.cn")
_SHORT_HOSTS = ("v.kuaishou.com", "v.kuaishou.cn")

_ID = r"([A-Za-z0-9_-]{4,64})"
_RE_SHORT_VIDEO = re.compile(rf"^/short-video/{_ID}/?$")
_RE_VIDEO = re.compile(rf"^/video/{_ID}/?$")
_RE_SHARE_F = re.compile(rf"^/f/{_ID}/?$")
_RE_FW_PHOTO = re.compile(rf"^/fw/photo/{_ID}/?$")
_RE_SHORT_CODE = re.compile(rf"^/{_ID}/?$")


@dataclass(frozen=True)
class KuaishouLink:
    kind: str          # short_video | video | fw_photo | share_f | short_link
    code: str          # photo id, or the share/short code
    url: str           # https form of the input

    @property
    def needs_redirect(self) -> bool:
        return self.kind in ("share_f", "short_link")

    @property
    def photo_id(self) -> Optional[str]:
        return None if self.needs_redirect else self.code


def _clean_host(host: Optional[str]) -> str:
    return (host or "").strip().rstrip(".").lower()


def host_allowed(host: Optional[str]) -> bool:
    h = _clean_host(host)
    return bool(h) and any(h == s or h.endswith("." + s) for s in PAGE_HOST_SUFFIXES)


def _under(host: str, bases: Iterable[str]) -> bool:
    return any(host == b or host.endswith("." + b) for b in bases)


def parse_kuaishou_url(url: Any) -> Optional[KuaishouLink]:
    """Pure recognition, no network, independent of the feature flag."""
    if not isinstance(url, str):
        return None
    raw = url.strip()
    if not raw or len(raw) > 2048:
        return None
    try:
        p = urlparse(raw)
        port = p.port
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or p.username or p.password:
        return None
    if port not in (None, 80, 443):
        return None
    host = _clean_host(p.hostname)
    if not host_allowed(host):
        return None
    path = p.path or "/"
    https_url = urlunparse(("https", host, path, "", p.query, ""))

    if host in _SHORT_HOSTS:
        m = _RE_SHORT_CODE.match(path)
        return KuaishouLink("short_link", m.group(1), https_url) if m else None
    m = _RE_FW_PHOTO.match(path)
    if m:
        return KuaishouLink("fw_photo", m.group(1), https_url)
    if _under(host, _KS_BASES):
        for kind, rx in (("short_video", _RE_SHORT_VIDEO), ("video", _RE_VIDEO),
                         ("share_f", _RE_SHARE_F)):
            m = rx.match(path)
            if m:
                return KuaishouLink(kind, m.group(1), https_url)
    return None


def is_kuaishou_url(url: Any) -> bool:
    return parse_kuaishou_url(url) is not None


def should_handle(url: Any) -> bool:
    """The one gate every integration point uses: flag ON and a recognised URL."""
    return kuaishou_enabled() and is_kuaishou_url(url)


# ═══════════════════════════════════════════════════════════════════
# Network layer
# ═══════════════════════════════════════════════════════════════════

Resolver = Callable[[str, int], list]
ClientFactory = Callable[..., httpx.Client]

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# Share pages (fw/photo, INIT_STATE) are the mobile representation.
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_REDIRECTS = (301, 302, 303, 307, 308)


@dataclass
class FetchResult:
    final_url: str
    status: int
    text: str
    hop_hosts: list = field(default_factory=list)
    set_cookies: dict = field(default_factory=dict)
    via_proxy: bool = False
    bytes_read: int = 0


def _default_resolve(host: str, port: int) -> list:
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    return list(dict.fromkeys(info[4][0] for info in infos))


def _default_client_factory(*, proxy: Optional[str], timeout: httpx.Timeout) -> httpx.Client:
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": False,   # every hop is re-validated by hand
        "trust_env": False,          # only KUAISHOU_PROXY_CN may re-route us
        "verify": True,
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _public_ips(host: str, port: int, resolver: Resolver, *, required: bool) -> list:
    """
    Resolve once and require EVERY address to be public (ssrf_guard rules).
    `required=False` (proxy mode): if OUR resolver cannot resolve the name the
    proxy will, so an empty answer is allowed; a private answer never is.
    """
    from app.core.ssrf_guard import BLOCKED_HOSTNAMES, ip_is_public
    if any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTNAMES):
        raise KuaishouError("kuaishou_unsafe_address", f"blocked hostname {host}")
    if _is_ip_literal(host):
        # Allowlisted names are never IP literals.
        raise KuaishouError("kuaishou_unsafe_address", "IP literal host")
    try:
        addrs = [str(a) for a in (resolver(host, port) or [])]
    except (OSError, UnicodeError):
        addrs = []
    if not addrs:
        if required:
            raise KuaishouError("kuaishou_upstream_unreachable", f"DNS failed for {host}")
        return []
    if not all(ip_is_public(a) for a in addrs):
        raise KuaishouError("kuaishou_unsafe_address", f"{host} resolves to a non-public address")
    return addrs


def _check_hop(url: str, *, first: bool) -> tuple[str, int]:
    try:
        p = urlparse(url)
        port = p.port or 443
    except ValueError:
        raise KuaishouError("kuaishou_invalid_url" if first else "kuaishou_redirect_rejected",
                            "unparseable URL")
    code = "kuaishou_invalid_url" if first else "kuaishou_redirect_rejected"
    if p.scheme != "https":
        raise KuaishouError(code, f"scheme {p.scheme!r} is not https")
    if p.username or p.password:
        raise KuaishouError(code, "credentials in URL")
    host = _clean_host(p.hostname)
    if not host_allowed(host):
        raise KuaishouError(code, f"host {host!r} is not a Kuaishou host")
    if port != 443:
        raise KuaishouError(code, f"port {port} not allowed")
    return host, port


def _parse_set_cookies(headers: httpx.Headers) -> dict:
    out: dict[str, str] = {}
    for raw in headers.get_list("set-cookie"):
        first = raw.split(";", 1)[0]
        if "=" in first:
            k, v = first.split("=", 1)
            k = k.strip()
            if k and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", k):
                out[k] = v.strip()
    return out


def _cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def fetch_page(
    url: str,
    *,
    cookies: Optional[dict] = None,
    deadline: Optional[float] = None,
    resolver: Optional[Resolver] = None,
    client_factory: Optional[ClientFactory] = None,
    mobile: bool = False,
) -> FetchResult:
    """
    GET a Kuaishou page following at most MAX_REDIRECTS redirects by hand.
    Raises KuaishouError for every failure; never follows a redirect it has
    not validated. Never logs cookies or the proxy URL.
    """
    resolver = resolver or _default_resolve
    client_factory = client_factory or _default_client_factory
    proxy = _proxy_url() or None
    deadline = deadline or (time.monotonic() + EXTRACTION_BUDGET_S)
    jar: dict[str, str] = dict(cookies or {})
    hop_hosts: list[str] = []
    current = url

    for hop in range(MAX_REDIRECTS + 1):
        host, port = _check_hop(current, first=(hop == 0))
        hop_hosts.append(host)
        ips = _public_ips(host, port, resolver, required=not proxy)

        headers = {
            "User-Agent": _MOBILE_UA if mobile else _DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if jar:
            headers["Cookie"] = _cookie_header(jar)

        # Through a proxy the proxy resolves and connects; without one we pin
        # the connection to an address we validated (no second DNS lookup).
        targets = [None] if proxy else ips[:MAX_CONNECT_TRIES]
        last_connect_exc: Optional[BaseException] = None
        outcome = None
        for ip in targets:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KuaishouError("kuaishou_geo_blocked", "extraction budget spent")
            per_req = min(REQUEST_TIMEOUT_S, remaining)
            req_deadline = time.monotonic() + per_req
            timeout = httpx.Timeout(per_req, connect=min(CONNECT_TIMEOUT_S, per_req))
            req_headers = dict(headers)
            extensions: dict[str, Any] = {}
            target_url: Any = current
            if ip is not None:
                target_url = httpx.URL(current).copy_with(host=ip)
                req_headers["Host"] = host
                extensions["sni_hostname"] = host
            try:
                with client_factory(proxy=proxy, timeout=timeout) as client:
                    with client.stream("GET", target_url, headers=req_headers,
                                       extensions=extensions) as resp:
                        jar.update(_parse_set_cookies(resp.headers))
                        if resp.status_code in _REDIRECTS:
                            loc = resp.headers.get("location")
                            if not loc:
                                raise KuaishouError("kuaishou_parse_failed",
                                                    f"redirect {resp.status_code} without Location")
                            outcome = ("redirect", urljoin(current, loc.strip()))
                        else:
                            buf = bytearray()
                            for chunk in resp.iter_bytes():
                                buf += chunk
                                if len(buf) > MAX_BODY_BYTES:
                                    raise KuaishouError("kuaishou_response_too_large",
                                                        f"body over {MAX_BODY_BYTES} bytes")
                                if time.monotonic() > req_deadline:
                                    raise KuaishouError("kuaishou_geo_blocked",
                                                        "body read exceeded the request deadline")
                            enc = resp.encoding or "utf-8"
                            try:
                                text = bytes(buf).decode(enc, errors="replace")
                            except LookupError:
                                text = bytes(buf).decode("utf-8", errors="replace")
                            outcome = ("page", resp.status_code, text, len(buf))
                break
            except KuaishouError:
                raise
            except httpx.TimeoutException as e:
                raise KuaishouError("kuaishou_geo_blocked",
                                    f"{type(e).__name__} contacting {host}") from None
            except httpx.ProxyError as e:
                raise KuaishouError("kuaishou_proxy_error", f"{type(e).__name__}") from None
            except httpx.ConnectError as e:
                if proxy:
                    raise KuaishouError("kuaishou_proxy_error", f"{type(e).__name__}") from None
                last_connect_exc = e
                continue
            except ImportError:
                # socks5:// proxy without socksio installed.
                raise KuaishouError("kuaishou_proxy_error",
                                    "proxy scheme needs an extra package (socksio)") from None
            except (httpx.HTTPError, OSError, ValueError) as e:
                raise KuaishouError("kuaishou_upstream_unreachable",
                                    f"{type(e).__name__}") from None
        if outcome is None:
            name = type(last_connect_exc).__name__ if last_connect_exc else "no address"
            raise KuaishouError("kuaishou_upstream_unreachable", f"connect failed: {name}")

        if outcome[0] == "redirect":
            current = outcome[1]
            continue

        _, status, text, nbytes = outcome
        if status in (403, 451):
            raise KuaishouError("kuaishou_geo_blocked", f"HTTP {status} from {host}")
        if status == 429:
            raise KuaishouError("kuaishou_challenge_page", "HTTP 429")
        if status in (404, 410):
            raise KuaishouError("kuaishou_not_found", f"HTTP {status}")
        if status != 200:
            raise KuaishouError("kuaishou_upstream_unreachable", f"HTTP {status}")
        logger.info("[Kuaishou] fetched %s (%d bytes, %d hop(s), proxy=%s)",
                    host, nbytes, len(hop_hosts), "yes" if proxy else "no")
        return FetchResult(final_url=current, status=status, text=text,
                           hop_hosts=hop_hosts, set_cookies=jar,
                           via_proxy=bool(proxy), bytes_read=nbytes)

    raise KuaishouError("kuaishou_too_many_redirects", f"more than {MAX_REDIRECTS} redirects")


# ═══════════════════════════════════════════════════════════════════
# Parsing strategies (ALL UNVERIFIED against the live site)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class KuaishouPost:
    post_type: str                     # "video" | "images"
    strategy: str
    photo_id: str = ""
    title: str = ""
    author: str = ""
    thumbnail: str = ""
    duration_ms: int = 0
    video_urls: list = field(default_factory=list)   # mirrors of ONE stream, best first
    image_urls: list = field(default_factory=list)


@dataclass
class StrategyOutcome:
    name: str
    ok: bool
    reason: str = ""
    post: Optional[KuaishouPost] = None
    unsupported_type: bool = False


def _fail(name: str, reason: str, *, unsupported: bool = False) -> StrategyOutcome:
    return StrategyOutcome(name=name, ok=False, reason=reason, unsupported_type=unsupported)


_DECODER = json.JSONDecoder()


def _embedded_json(html: str, marker: re.Pattern) -> tuple[Optional[Any], str]:
    """Decode the JSON value right after `marker`. Returns (obj, reason_if_none)."""
    m = marker.search(html or "")
    if not m:
        return None, f"marker {marker.pattern!r} not found"
    i = m.end()
    while i < len(html) and html[i] in " \t\r\n":
        i += 1
    try:
        obj, _end = _DECODER.raw_decode(html, i)
    except json.JSONDecodeError as e:
        return None, f"JSON after marker is not valid JSON ({e.msg} at {e.pos})"
    return obj, ""


def _str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _int(v: Any) -> int:
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)) and v >= 0:
        return int(v)
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return 0


# Strategy 1 ─ PC page, window.__APOLLO_STATE__
# Sources (read 2026-10-05):
#   https://github.com/JoeanAmier/KS-Downloader/blob/master/source/extract/extractor.py
#     (HTMLExtractor: WEB_KEYWORD "window.__APOLLO_STATE__=", "defaultClient",
#      "VisionVideoDetailPhoto:{id}" → caption, coverUrl, duration, photoUrl;
#      author from the "VisionVideoDetailAuthor:" node → name)
#   https://github.com/wuaishare/sharextract/issues/24 (same node names; warns
#     that the adaptive `manifest` holds separate audio-only / video-only
#     tracks — so manifest representations are deliberately NOT offered as
#     qualities here: one could be silent. Revisit with a real capture.)
_APOLLO_MARKER = re.compile(r"window\.__APOLLO_STATE__\s*=")


def strategy_apollo_state(html: str, photo_id: Optional[str]) -> StrategyOutcome:
    name = "apollo_state"
    state, why = _embedded_json(html, _APOLLO_MARKER)
    if state is None:
        return _fail(name, why)
    if not isinstance(state, dict):
        return _fail(name, "APOLLO_STATE is not an object")
    client = state.get("defaultClient", state)
    if not isinstance(client, dict):
        return _fail(name, "defaultClient is not an object")
    prefix = "VisionVideoDetailPhoto:"
    keys = [k for k in client if isinstance(k, str) and k.startswith(prefix)]
    if not keys:
        return _fail(name, "no VisionVideoDetailPhoto node")
    if photo_id:
        key = prefix + photo_id
        if key not in client:
            return _fail(name, f"no VisionVideoDetailPhoto node for id {photo_id} "
                               f"({len(keys)} other node(s))")
    elif len(keys) == 1:
        key = keys[0]
    else:
        return _fail(name, f"{len(keys)} VisionVideoDetailPhoto nodes and no id to choose")
    node = client.get(key)
    if not isinstance(node, dict):
        return _fail(name, "VisionVideoDetailPhoto node is not an object")
    photo_url = _str(node.get("photoUrl"))
    if not photo_url:
        return _fail(name, "VisionVideoDetailPhoto.photoUrl missing or empty")
    authors = [v for k, v in client.items()
               if isinstance(k, str) and k.startswith("VisionVideoDetailAuthor:") and isinstance(v, dict)]
    author = _str(authors[0].get("name")) if len(authors) == 1 else ""
    return StrategyOutcome(name=name, ok=True, post=KuaishouPost(
        post_type="video", strategy=name,
        photo_id=key[len(prefix):],
        title=_str(node.get("caption")),
        author=author,
        thumbnail=_str(node.get("coverUrl")),
        duration_ms=_int(node.get("duration")),
        video_urls=[photo_url],
    ))


# Strategy 2 ─ share page (fw/photo, chenzhongtech, gifshow), window.INIT_STATE
# Sources (read 2026-10-05):
#   KS-Downloader extractor.py (APP_KEYWORD "window.INIT_STATE = ", the
#     "photo" object; caption, coverUrls[0].url, timestamp, userName,
#     ext_params.single, ext_params.atlas.cdn[0] + ext_params.atlas.list[] →
#     "https://{cdn}{path}"; photoType VIDEO / VERTICAL_ATLAS / HORIZONTAL_ATLAS)
#   https://developer.aliyun.com/article/921451 (feed objects: mainMvUrls[0].url
#     is the video, coverUrls[0].url the cover, caption the title)
_INIT_MARKER = re.compile(r"window\.INIT_STATE\s*=")
_ATLAS_TYPES = ("VERTICAL_ATLAS", "HORIZONTAL_ATLAS")
_PHOTO_HINT_KEYS = ("mainMvUrls", "photoType", "ext_params", "coverUrls")
_CDN_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{3,253}$")


def _walk_photos(obj: Any, depth: int = 0, out: Optional[list] = None) -> list:
    out = [] if out is None else out
    if depth > 12 or len(out) > 50:
        return out
    if isinstance(obj, dict):
        p = obj.get("photo")
        if isinstance(p, dict) and any(k in p for k in _PHOTO_HINT_KEYS):
            out.append((obj, p))
        for v in obj.values():
            _walk_photos(v, depth + 1, out)
    elif isinstance(obj, list):
        for v in obj[:200]:
            _walk_photos(v, depth + 1, out)
    return out


def _photo_identity(p: dict) -> str:
    for k in ("photoId", "photo_id", "id"):
        v = p.get(k)
        if isinstance(v, (str, int)) and str(v):
            return str(v)
    share = p.get("share_info")
    if isinstance(share, str):
        v = parse_qs(share).get("photoId", [""])[0]
        if v:
            return v
    return ""


def _urls_from(items: Any) -> list:
    out = []
    if isinstance(items, list):
        for it in items:
            u = _str(it.get("url")) if isinstance(it, dict) else ""
            if u and u not in out:
                out.append(u)
    return out


def strategy_init_state(html: str, photo_id: Optional[str]) -> StrategyOutcome:
    name = "init_state"
    state, why = _embedded_json(html, _INIT_MARKER)
    if state is None:
        return _fail(name, why)
    found = _walk_photos(state)
    if not found:
        return _fail(name, "no 'photo' object with mainMvUrls/photoType/ext_params/coverUrls")
    # The same photo may be referenced from several places — dedupe by identity.
    unique: dict[str, tuple] = {}
    for parent, p in found:
        unique.setdefault(_photo_identity(p) or f"#{id(p)}", (parent, p))
    picks = list(unique.values())
    if len(picks) > 1 and photo_id:
        picks = [t for t in picks if _photo_identity(t[1]) == photo_id]
    if len(picks) != 1:
        return _fail(name, f"{len(unique)} distinct photo objects; cannot tell which one is this post")
    parent, p = picks[0]

    title = _str(p.get("caption"))
    covers = _urls_from(p.get("coverUrls"))
    thumb = covers[0] if covers else ""
    common = dict(strategy=name, photo_id=_photo_identity(p), title=title,
                  author=_str(p.get("userName")), thumbnail=thumb,
                  duration_ms=_int(p.get("duration")))

    ptype = _str(p.get("photoType"))
    ext = p.get("ext_params") if isinstance(p.get("ext_params"), dict) else {}
    atlas = ext.get("atlas") if isinstance(ext.get("atlas"), dict) else None
    if atlas is None and isinstance(parent.get("atlas"), dict):
        atlas = parent.get("atlas")
    videos = _urls_from(p.get("mainMvUrls"))

    if ptype in _ATLAS_TYPES or (not ptype and atlas and not videos):
        if not atlas:
            if ext.get("single") and thumb:
                # KS-Downloader treats ext_params.single as "the cover IS the image".
                return StrategyOutcome(name=name, ok=True, post=KuaishouPost(
                    post_type="images", image_urls=[thumb], **common))
            return _fail(name, f"photoType {ptype or '?'} but no atlas object")
        cdns = atlas.get("cdn")
        paths = atlas.get("list")
        cdn = cdns[0] if isinstance(cdns, list) and cdns else cdns
        if not isinstance(cdn, str) or not _CDN_HOST_RE.match(cdn.strip()):
            return _fail(name, "atlas.cdn missing or not a bare hostname")
        if not isinstance(paths, list) or not paths:
            return _fail(name, "atlas.list missing or empty")
        urls = []
        for path in paths:
            if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
                return _fail(name, "atlas.list entry is not an absolute path")
            urls.append(f"https://{cdn.strip()}{path}")
        return StrategyOutcome(name=name, ok=True, post=KuaishouPost(
            post_type="images", image_urls=urls, **common))

    if ptype == "VIDEO" or (not ptype and videos):
        if not videos:
            return _fail(name, "photoType VIDEO but mainMvUrls missing or empty")
        return StrategyOutcome(name=name, ok=True, post=KuaishouPost(
            post_type="video", video_urls=videos, **common))

    return _fail(name, f"unknown photoType {ptype!r}", unsupported=True)


# Strategy 3 ─ last resort: a single "photoUrl" string anywhere in the page
# Source: https://github.com/iawia002/lux/blob/master/extractors/kuaishou/kuaishou.go
#   (regex `"photoUrl":\s*"([^"]+)"`, `/` unescaped, title from <title>;
#    lux first GETs the page, then repeats the GET with the cookies the site set).
# Fails closed when the page holds more than one distinct photoUrl (it could be
# a recommended video, not this one).
_PHOTO_URL_RE = re.compile(r'"photoUrl"\s*:\s*"((?:[^"\\]|\\.){1,4000})"')
_TITLE_RE = re.compile(r"<title[^>]*>([^<]{1,500})</title>", re.I)


def strategy_photo_url_regex(html: str, photo_id: Optional[str]) -> StrategyOutcome:  # noqa: ARG001
    name = "photo_url_regex"
    urls: list[str] = []
    for m in _PHOTO_URL_RE.finditer(html or ""):
        try:
            u = json.loads(f'"{m.group(1)}"').strip()
        except json.JSONDecodeError:
            continue
        if u and u not in urls:
            urls.append(u)
    if not urls:
        return _fail(name, "no \"photoUrl\" string in page")
    if len(urls) > 1:
        return _fail(name, f"{len(urls)} distinct photoUrl values; cannot tell which one is this post")
    t = _TITLE_RE.search(html or "")
    title = " ".join(_html_unescape(t.group(1)).split()) if t else ""
    return StrategyOutcome(name=name, ok=True, post=KuaishouPost(
        post_type="video", strategy=name, title=title, video_urls=urls))


STRATEGIES = (strategy_apollo_state, strategy_init_state, strategy_photo_url_regex)


def run_strategies(html: str, photo_id: Optional[str]) -> list:
    """Run EVERY strategy (the capture tool reports them all)."""
    out = []
    for fn in STRATEGIES:
        try:
            out.append(fn(html, photo_id))
        except Exception as e:  # a parser bug must not look like a site change
            out.append(_fail(fn.__name__.replace("strategy_", ""),
                             f"strategy crashed: {type(e).__name__}"))
    return out


def photo_id_from_url(url: str) -> Optional[str]:
    link = parse_kuaishou_url(url)
    if link and link.photo_id:
        return link.photo_id
    try:
        v = parse_qs(urlparse(url).query).get("photoId", [""])[0]
    except ValueError:
        v = ""
    return v if re.fullmatch(r"[A-Za-z0-9_-]{4,64}", v or "") else None


_CHALLENGE_URL_RE = re.compile(r"captcha|verify|/safety|antispam", re.I)


def looks_like_challenge(final_url: str) -> bool:
    """UNVERIFIED heuristic: only the final URL is used, never page text."""
    try:
        p = urlparse(final_url)
    except ValueError:
        return False
    return bool(_CHALLENGE_URL_RE.search(f"{p.hostname or ''}{p.path or ''}"))


# ═══════════════════════════════════════════════════════════════════
# Media URL validation
# ═══════════════════════════════════════════════════════════════════

def validate_media_url(url: Any, *, resolver: Optional[Resolver] = None) -> str:
    """
    https only, no credentials, default port, a hostname that resolves ONLY to
    public addresses (ssrf_guard rules). Unresolvable → rejected (fail closed).
    """
    from app.core.ssrf_guard import BLOCKED_HOSTNAMES, ip_is_public
    resolver = resolver or _default_resolve
    if not isinstance(url, str) or not url.strip() or len(url) > 8192:
        raise KuaishouError("kuaishou_media_url_rejected", "empty or oversized media URL")
    u = url.strip()
    try:
        p = urlparse(u)
        port = p.port
    except ValueError:
        raise KuaishouError("kuaishou_media_url_rejected", "unparseable media URL")
    if p.scheme != "https":
        raise KuaishouError("kuaishou_media_url_rejected", f"media scheme {p.scheme!r}")
    if p.username or p.password or port not in (None, 443):
        raise KuaishouError("kuaishou_media_url_rejected", "credentials or port in media URL")
    host = _clean_host(p.hostname)
    if not host or any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTNAMES):
        raise KuaishouError("kuaishou_media_url_rejected", "blocked media host")
    if _is_ip_literal(host):
        raise KuaishouError("kuaishou_media_url_rejected", "IP literal media host")
    try:
        addrs = [str(a) for a in (resolver(host, 443) or [])]
    except (OSError, UnicodeError):
        addrs = []
    if not addrs:
        raise KuaishouError("kuaishou_media_url_rejected", f"media host {host} does not resolve")
    if not all(ip_is_public(a) for a in addrs):
        raise KuaishouError("kuaishou_media_url_rejected", f"media host {host} is not public")
    return u


def media_host(url: str) -> str:
    try:
        return _clean_host(urlparse(url).hostname)
    except ValueError:
        return ""


# ═══════════════════════════════════════════════════════════════════
# Cookies (optional)
# ═══════════════════════════════════════════════════════════════════

def _pool_cookies() -> dict:
    """Optional cookie from the admin pool (platform "kuaishou"). Netscape format."""
    try:
        from app.core.cookie_pool import get_cookie_from_pool
        b64 = get_cookie_from_pool(PLATFORM_SLUG)
    except Exception:
        return {}
    if not b64:
        return {}
    try:
        decoded = base64.b64decode(b64).decode("utf-8", errors="ignore")
    except Exception:
        return {}
    out: dict[str, str] = {}
    for line in decoded.splitlines():
        line = line.strip()
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and parts[5]:
            out[parts[5]] = parts[6]
    return out


# ═══════════════════════════════════════════════════════════════════
# Extraction
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ExtractionReport:
    """Everything the capture tool needs; the extractor only uses `post`."""
    link: Optional[KuaishouLink] = None
    fetch: Optional[FetchResult] = None
    outcomes: list = field(default_factory=list)
    retried_with_site_cookies: bool = False
    post: Optional[KuaishouPost] = None


def _pick(outcomes: list) -> Optional[KuaishouPost]:
    for o in outcomes:
        if o.ok and o.post is not None:
            return o.post
    return None


def extract_post(
    url: str,
    *,
    resolver: Optional[Resolver] = None,
    client_factory: Optional[ClientFactory] = None,
    report: Optional[ExtractionReport] = None,
) -> KuaishouPost:
    """
    Fetch + parse + validate. Returns a post whose every media URL passed
    validate_media_url, or raises KuaishouError. Never returns invented data.
    """
    rep = report if report is not None else ExtractionReport()
    link = parse_kuaishou_url(url)
    rep.link = link
    if link is None:
        raise KuaishouError("kuaishou_invalid_url", "not a recognised Kuaishou URL")

    deadline = time.monotonic() + EXTRACTION_BUDGET_S
    mobile = link.kind in ("fw_photo", "share_f", "short_link")
    pool = _pool_cookies()
    fetch = fetch_page(link.url, cookies=pool, deadline=deadline, resolver=resolver,
                       client_factory=client_factory, mobile=mobile)
    rep.fetch = fetch
    photo_id = link.photo_id or photo_id_from_url(fetch.final_url)
    outcomes = run_strategies(fetch.text, photo_id)
    rep.outcomes = outcomes
    post = _pick(outcomes)

    # lux's approach: the first response sets cookies (e.g. the device id);
    # repeat the GET once with exactly what the site set. Nothing is invented.
    new_cookies = {k: v for k, v in fetch.set_cookies.items() if k not in pool}
    if post is None and new_cookies and time.monotonic() < deadline - 1:
        rep.retried_with_site_cookies = True
        fetch = fetch_page(fetch.final_url, cookies=fetch.set_cookies, deadline=deadline,
                           resolver=resolver, client_factory=client_factory, mobile=mobile)
        rep.fetch = fetch
        outcomes = run_strategies(fetch.text, photo_id)
        rep.outcomes = outcomes
        post = _pick(outcomes)

    if post is None:
        if looks_like_challenge(fetch.final_url):
            raise KuaishouError("kuaishou_challenge_page", "final URL looks like a challenge page")
        if any(o.unsupported_type for o in outcomes):
            raise KuaishouError("kuaishou_unsupported_post_type",
                                "; ".join(f"{o.name}: {o.reason}" for o in outcomes))
        raise KuaishouError("kuaishou_parse_failed",
                            "; ".join(f"{o.name}: {o.reason}" for o in outcomes))

    if post.post_type == "video":
        good = []
        for u in post.video_urls:
            try:
                good.append(validate_media_url(u, resolver=resolver))
            except KuaishouError:
                continue
        if not good:
            raise KuaishouError("kuaishou_media_url_rejected", "no video URL passed validation")
        post.video_urls = good
    else:
        # Every image must pass; a partially-safe album is not served.
        post.image_urls = [validate_media_url(u, resolver=resolver) for u in post.image_urls]
    if post.thumbnail:
        try:
            post.thumbnail = validate_media_url(post.thumbnail, resolver=resolver)
        except KuaishouError:
            post.thumbnail = ""
    rep.post = post
    return post


def to_download_info(post: KuaishouPost, original_url: str, quality: str = "video") -> dict:
    """Adapt to the common /fetch-link schema (same shape as threads_extractor)."""
    title = (post.title or "").replace("\n", " ").strip()[:120] or f"Kuaishou {post.photo_id}".strip()
    formats: list[dict] = []
    if post.post_type == "video":
        formats.append({
            "type": "video", "label": "Video", "resolution": "Original", "height": 0,
            "ext": "mp4", "url": post.video_urls[0],
            "filesize_mb": 0, "requires_merge": False,
        })
        primary = post.video_urls[0]
        source_type = "single_video"
        count = 1
    else:
        for i, u in enumerate(post.image_urls):
            formats.append({
                "type": "image",
                "label": "Ảnh" if len(post.image_urls) == 1 else f"Ảnh #{i + 1}",
                "resolution": "Original", "height": 0, "ext": "jpg", "url": u,
                "filesize_mb": 0, "requires_merge": False,
            })
        primary = post.image_urls[0]
        source_type = "single_post"
        count = len(post.image_urls)
    return {
        "title": title,
        "thumbnail_url": post.thumbnail or (post.image_urls[0] if post.image_urls else ""),
        "direct_mp4_url": primary,
        "original_url": original_url,
        "quality": quality,
        "duration": round(post.duration_ms / 1000) if post.duration_ms else 0,
        "file_size_mb": 0,
        "available_formats": formats,
        "max_merge_height": 0,
        "downloaded_height": 0,
        "is_audio_only": False,
        "uploader": post.author or None,
        "platform": PLATFORM_SLUG,
        "source_type": source_type,
        "media_count": count,
        "provider": f"kuaishou:{post.strategy}",
    }


def extract_kuaishou_download_info(url: str, quality: str = "video") -> dict:
    """Entry point for app.services.downloader (only reached when the flag is ON)."""
    try:
        post = extract_post(url)
    except KuaishouError as e:
        logger.warning("[Kuaishou] %s: %s", e.error_code, e.detail)
        raise
    return to_download_info(post, url, quality)


# ═══════════════════════════════════════════════════════════════════
# BaseExtractor wrapper — registered ONLY when the flag is ON
# ═══════════════════════════════════════════════════════════════════

from app.services.base_extractor import (  # noqa: E402
    BaseExtractor, ExtractResult, FormatInfo, MediaItem,
)


class KuaishouExtractor(BaseExtractor):
    platform_name = PLATFORM_NAME
    supported_features = frozenset({"video", "image", "carousel"})
    cost_tier = "medium"
    requires_cookie = False
    supports_proxy = True
    _test_fixtures: list = []   # no verified public fixture exists yet

    def is_enabled(self) -> bool:
        return kuaishou_enabled()

    def detect_url(self, url: str) -> bool:
        return should_handle(url)

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        try:
            post = extract_post(url)
        except KuaishouError as e:
            return ExtractResult.error(self.platform_name, e.error_code, e.user_message)
        if post.post_type == "video":
            return ExtractResult(
                platform=self.platform_name, source_type="video",
                title=post.title, author=post.author, thumbnail=post.thumbnail,
                duration_ms=post.duration_ms,
                formats=[FormatInfo("Original", url=post.video_urls[0], ext="mp4")],
                direct_url=post.video_urls[0], extraction_method="custom",
            )
        items = [MediaItem(u, "image", u, f"kuaishou_{post.photo_id or 'post'}_{i + 1}.jpg")
                 for i, u in enumerate(post.image_urls)]
        return ExtractResult(
            platform=self.platform_name,
            source_type="mixed" if len(items) > 1 else "image",
            title=post.title, author=post.author, thumbnail=post.thumbnail,
            media_items=items, requires_zip=len(items) > 1, extraction_method="custom",
        )


def register_if_enabled(registry=None) -> bool:
    """Register with the extractor registry only when KUAISHOU_ENABLED is on."""
    if not kuaishou_enabled():
        return False
    if registry is None:
        from app.services.extractor_registry import REGISTRY as registry  # noqa: N811
    if any(isinstance(e, KuaishouExtractor) for e in getattr(registry, "_extractors", [])):
        return True
    registry.register(KuaishouExtractor())
    return True


try:
    register_if_enabled()
except Exception as _e:  # pragma: no cover
    logger.warning("Could not register KuaishouExtractor: %s", _e)
