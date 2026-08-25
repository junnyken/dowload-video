"""
Threads Public Scraper + Media Extractor (Lean V1)
==================================================
Isolated extractor for PUBLIC Threads content only.

Scope (hard rules — see project brief):
  • Public Threads single-post URLs        -> media extraction
  • Public Threads profile URLs            -> recent public post list
  • NO private / login-walled content
  • NO official Threads Graph API (third-party public content only)
  • NO GPU, NO generic large-scale crawler

Strategy (httpx + regex/JSON, no new dependencies — mirrors douyin_extractor):
  1. Detect URL type (post | profile).
  2. Fetch the public page HTML with realistic UAs + backoff (Threads is
     anti-bot sensitive, so we retry on transient blocks).
  3. Parse, in order of reliability:
       a. Open Graph meta tags   (og:title / og:description / og:image /
          og:video)  — authoritative for the primary post media.
       b. Embedded JSON blobs    (<script type="application/json" data-sjs>)
          containing Instagram-style media objects: video_versions,
          image_versions2.candidates, carousel_media — gives carousels and
          higher-quality variants.
  4. Normalize into the common VidGrab Threads schema.
  5. On failure, return an explicit error_code (never a silent failure).

Normalized result schema:
    {
      "platform":      "threads",
      "source_type":   "single_post" | "profile",
      "canonical_url": str,               # clean rebuilt URL (no tracking params)
      "original_url":  str,
      "post_id":       str | None,
      "author_handle": str | None,
      "caption":       str | None,
      "timestamp":     str | None,        # ISO-8601 if derivable
      "media_items":   [ {type,url,thumbnail,width,height,ext} ],
      "downloadable":  bool,
      "error_code":    str | None,
      "error_message": str | None,
      "posts":         [ {url,post_id,caption_snippet,has_media,timestamp} ],  # profile only
    }

Error codes (user-facing states):
  unsupported_threads_url, private_or_login_required, no_media_found,
  public_post_but_media_unavailable, extraction_temporarily_blocked,
  extractor_failed
"""

import asyncio
import html as _html
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

# ── Error codes (prefixed `threads_*` — keep in sync with frontend
#    THREADS_ERROR_COPY map in DashboardContent.jsx) ───────────────────
ERR_INVALID = "threads_invalid_url"
ERR_UNSUPPORTED = "threads_unsupported_url"
ERR_PRIVATE = "threads_private_or_login_required"
ERR_NO_MEDIA = "threads_no_media_found"
ERR_MEDIA_UNAVAILABLE = "threads_media_unavailable"
ERR_BLOCKED = "threads_extraction_blocked"
ERR_AMBIGUOUS = "threads_ambiguous_post_match"
ERR_FAILED = "threads_extractor_failed"

# Honest, user-facing copy (Vietnamese) keyed by error code.
ERR_MESSAGES = {
    ERR_INVALID: "Link Threads không hợp lệ.",
    ERR_UNSUPPORTED: "Link Threads không được hỗ trợ. Chỉ nhận link bài viết hoặc trang cá nhân công khai.",
    ERR_PRIVATE: "Nội dung này yêu cầu đăng nhập hoặc không công khai.",
    ERR_NO_MEDIA: "Bài viết này không có media tải xuống được.",
    ERR_MEDIA_UNAVAILABLE: "Bài Threads công khai nhưng không trích xuất được media.",
    ERR_BLOCKED: "Threads tạm thời chặn yêu cầu. Vui lòng thử lại sau ít phút.",
    ERR_AMBIGUOUS: "Phát hiện nhiều bài viết trong trang — không xác định chắc chắn được bài cần lấy.",
    ERR_FAILED: "Không trích xuất được nội dung Threads này.",
}

# Threads is served from threads.net (legacy) and threads.com (current).
_THREADS_DOMAINS = (r"threads\.net", r"threads\.com")
_THREADS_HOST_RE = re.compile(r"(?:" + "|".join(_THREADS_DOMAINS) + r")", re.IGNORECASE)

# Post permalink:  /@handle/post/<code>   or   /t/<code>
_POST_PATH_RE = re.compile(
    r"(?:^|/)(?:@[\w.\-]+/post|t|p)/(?P<code>[A-Za-z0-9_\-]+)", re.IGNORECASE
)
# Profile:  /@handle   (no /post/ segment)
_PROFILE_PATH_RE = re.compile(r"/@(?P<handle>[\w.\-]+)/?(?:\?|$)", re.IGNORECASE)

# Share link:  /share/<code>  — what the app's "Copy link" button produces.
#
# These carry a share code, NOT the post shortcode, so they cannot simply be
# added to _POST_PATH_RE: extract_threads_post locks post_id from the URL and
# then accepts only the embedded node whose code matches it, so a share code
# would lock an id that never matches and fail in a new way. They have to be
# redirected to the real permalink first.
#
# Until this existed, /share/ links matched neither pattern, classified as
# "unsupported", and came back as "this is a Threads profile" — which sent the
# user looking for a problem with their link instead of ours.
_SHARE_PATH_RE = re.compile(r"/share/(?P<code>[A-Za-z0-9_\-]+)", re.IGNORECASE)

# Realistic UAs — alternate between desktop and mobile on each retry.
_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.1 Mobile/15E148 Safari/604.1"
)
_MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_ALL_UAS = [_DESKTOP_UA, _MOBILE_UA, _MAC_UA, _DESKTOP_UA, _MAC_UA]

_FETCH_TIMEOUT = 20.0
_MAX_RETRIES = 5
_PROFILE_POST_LIMIT = 100  # hard cap: recent posts only, no historical crawl


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        pass


# ── URL classification ───────────────────────────────────────────────

def is_threads_url(url: str) -> bool:
    """True for any Threads URL (post or profile, any subdomain)."""
    return bool(_THREADS_HOST_RE.search(url or ""))


def is_threads_post_url(url: str) -> bool:
    """True only for a single public-post permalink."""
    if not is_threads_url(url):
        return False
    return bool(_POST_PATH_RE.search(url))


def is_threads_profile_url(url: str) -> bool:
    """True for a profile URL (has @handle but no /post/ permalink)."""
    if not is_threads_url(url):
        return False
    return bool(_PROFILE_PATH_RE.search(url)) and not is_threads_post_url(url)


def is_threads_share_url(url: str) -> bool:
    """True for a /share/<code> link — needs resolving before it can be used."""
    if not is_threads_url(url):
        return False
    return bool(_SHARE_PATH_RE.search(url))


def classify_threads_url(url: str) -> str:
    """Return 'post', 'profile', 'share', or 'unsupported'."""
    if is_threads_post_url(url):
        return "post"
    if is_threads_share_url(url):
        return "share"
    if is_threads_profile_url(url):
        return "profile"
    return "unsupported"


async def _resolve_share_url(url: str) -> Optional[str]:
    """
    Follow a /share/<code> link to the permalink it points at.

    Returns the resolved URL, or None if it could not be resolved — the caller
    must treat that as a failure rather than carrying on with the share URL,
    because every step downstream reads a post shortcode out of the path and a
    share code is not one.

    Reuses this module's own UA rotation: a bare client gets a consent wall.
    """
    for attempt in range(len(_ALL_UAS)):
        ua = _ALL_UAS[attempt % len(_ALL_UAS)]
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=_FETCH_TIMEOUT,
            ) as client:
                resp = await client.get(url, headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
                })
            final = str(resp.url)
            # Only accept a redirect that actually landed on a post. Threads
            # answers an unresolvable share code with the home page or a login
            # wall, and following that to "not a post" is the honest outcome.
            if final and final != url and is_threads_post_url(final):
                return final
        except Exception as e:  # noqa: BLE001
            _safe_print(f"[Threads] share resolve attempt {attempt + 1} failed: {e}")
    return None


def _extract_post_code(url: str) -> Optional[str]:
    m = _POST_PATH_RE.search(url or "")
    return m.group("code") if m else None


def _extract_handle(url: str) -> Optional[str]:
    m = re.search(r"/@([\w.\-]+)", url or "")
    return m.group(1) if m else None


# Canonical domain for all rebuilt URLs (normalize .net/.com/mobile -> www.threads.com)
_CANON_HOST = "https://www.threads.com"


def _canonical_post_url(handle: Optional[str], post_id: Optional[str]) -> Optional[str]:
    """Rebuild the clean canonical post URL (no tracking params / fragments)."""
    if not post_id:
        return None
    if handle:
        return f"{_CANON_HOST}/@{handle}/post/{post_id}"
    return f"{_CANON_HOST}/t/{post_id}"


def _canonical_profile_url(handle: Optional[str]) -> Optional[str]:
    return f"{_CANON_HOST}/@{handle}" if handle else None


# ── Result helpers ───────────────────────────────────────────────────

def _base_result(url: str, source_type: str) -> Dict[str, Any]:
    return {
        "platform": "threads",
        "source_type": source_type,
        "original_url": url,
        "canonical_url": None,
        "post_id": None,
        "author_handle": None,
        "caption": None,
        "timestamp": None,
        "media_items": [],
        "downloadable": False,
        "error_code": None,
        "error_message": None,
        "posts": [],
    }


def _error_result(url: str, source_type: str, code: str, detail: str = "", *,
                  post_id: Optional[str] = None, author_handle: Optional[str] = None,
                  canonical_url: Optional[str] = None) -> Dict[str, Any]:
    res = _base_result(url, source_type)
    res["error_code"] = code
    res["error_message"] = detail or ERR_MESSAGES.get(code, ERR_MESSAGES[ERR_FAILED])
    # Carry the locked post identity into errors too (spec: canonical post_id
    # is the primary key, present even on failure states).
    if post_id:
        res["post_id"] = post_id
    if author_handle:
        res["author_handle"] = author_handle
    if canonical_url:
        res["canonical_url"] = canonical_url
    return res


# ── HTTP fetch with backoff (anti-bot aware) ─────────────────────────

async def _fetch_html(url: str) -> Dict[str, Any]:
    """
    Fetch a public Threads page.

    Returns {"html": str, "status": int, "blocked": bool, "login": bool}.
    `login` is True when the response looks like a login/consent wall.
    `blocked` is True for transient anti-bot responses (429/403/503).
    """
    last_status = 0
    for attempt in range(_MAX_RETRIES):
        ua = _ALL_UAS[attempt % len(_ALL_UAS)]
        is_mobile = "iPhone" in ua or "Android" in ua
        is_chrome = "Chrome/" in ua

        headers: Dict[str, str] = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
        }
        if is_chrome:
            headers["Sec-Ch-Ua"] = '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'
            headers["Sec-Ch-Ua-Mobile"] = "?1" if is_mobile else "?0"
            headers["Sec-Ch-Ua-Platform"] = '"Android"' if is_mobile else '"Windows"'
        if attempt > 0:
            # Simulate organic click-through on retries
            headers["Referer"] = "https://www.google.com/"

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=_FETCH_TIMEOUT, http2=True
            ) as client:
                resp = await client.get(url, headers=headers)
                last_status = resp.status_code

                if resp.status_code in (403, 429, 503):
                    # Transient block — exponential backoff with jitter.
                    base = 2.0 * (attempt + 1)
                    jitter = (attempt * 0.7)
                    await asyncio.sleep(base + jitter)
                    continue

                if resp.status_code >= 400:
                    return {"html": "", "status": resp.status_code,
                            "blocked": False, "login": False}

                body = resp.text or ""
                final_url = str(resp.url).lower()

                # Login / consent wall detection
                login_wall = (
                    "/login" in final_url
                    or "/accounts/login" in final_url
                    or ('"viewer"' not in body
                        and "loginForm" in body
                        and "og:" not in body)
                )
                return {"html": body, "status": resp.status_code,
                        "blocked": False, "login": login_wall}

        except httpx.TimeoutException:
            _safe_print(f"[Threads] Timeout (attempt {attempt + 1})")
            await asyncio.sleep(1.5 * (attempt + 1))
        except Exception as e:
            _safe_print(f"[Threads] Fetch error: {e}")
            await asyncio.sleep(1.0 * (attempt + 1))

    return {"html": "", "status": last_status, "blocked": True, "login": False}


# ── Parsing: Open Graph meta tags ────────────────────────────────────

# Single <meta ...> tag (bounded: a tag never contains '>' so no backtracking).
_META_TAG_RE = re.compile(r"<meta\s+[^>]{0,2000}?>", re.IGNORECASE)
_META_PROP_RE = re.compile(
    r'(?:property|name)=["\']([^"\']{1,80})["\']', re.IGNORECASE
)
_META_CONTENT_RE = re.compile(r'content=["\']([^"\']{0,4000})["\']', re.IGNORECASE)


def _parse_og(html: str) -> Dict[str, Any]:
    """
    Extract Open Graph tags — authoritative for the primary post media.

    Scans each <meta> tag once into a dict. NB: avoid a full-document DOTALL
    regex per property — that backtracks catastrophically on Threads' ~500KB
    pages. Each meta tag is matched in isolation instead.
    """
    tags: Dict[str, str] = {}
    # Only the document <head> carries OG tags; cap the scan window for speed.
    head = html[:200_000]
    for tag in _META_TAG_RE.findall(head):
        pm = _META_PROP_RE.search(tag)
        cm = _META_CONTENT_RE.search(tag)
        if pm and cm:
            key = pm.group(1).lower()
            if key not in tags:  # first occurrence wins
                tags[key] = _html.unescape(cm.group(1)).strip()

    return {
        "title": tags.get("og:title"),
        "description": tags.get("og:description"),
        "image": tags.get("og:image"),
        "video": (tags.get("og:video") or tags.get("og:video:url")
                  or tags.get("og:video:secure_url")),
        "video_type": tags.get("og:video:type"),
    }


# ── Parsing: post-scoped structured JSON (STRICT single-post matching) ──
#
# A Threads post page embeds the author's OTHER posts too (related feed,
# threaded replies). Regex-sweeping the whole document for media would pull
# in unrelated posts. Instead we parse the real embedded JSON, locate the
# node whose canonical `code` equals the requested post_id, and extract media
# ONLY from that node (and its carousel children). Anything else is discarded.

# <script type="application/json" ...>{...}</script> blocks carry the data.
_JSON_SCRIPT_RE = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

# Fields that identify a dict as a real POST node (not a profile avatar /
# link-preview, which also carry image_versions2).
_POST_MARKER_KEYS = ("code", "pk", "taken_at", "caption", "like_count",
                     "carousel_media_count")


def _is_post_media_node(n: Any) -> bool:
    """
    True only for a media-bearing POST/item node. A bare `image_versions2`
    (profile pic, link thumbnail) is NOT a post unless it also carries a
    post marker (code/pk/taken_at/caption/…). This stops avatars from being
    counted as candidate posts (which would trigger false ambiguity).
    """
    if not isinstance(n, dict):
        return False
    if "video_versions" in n or "carousel_media" in n:
        return True
    if "image_versions2" in n and any(k in n for k in _POST_MARKER_KEYS):
        return True
    return False


def _norm_text(s: Optional[str]) -> Optional[str]:
    """Normalize a caption/text field: JSON is already unicode-decoded by
    json.loads; apply NFC normalization + HTML unescape for safe rendering."""
    if not s:
        return None
    try:
        s = _html.unescape(s)
        s = unicodedata.normalize("NFC", s)
    except Exception:
        pass
    return s.strip() or None


def _iter_json_scripts(html: str):
    """Yield successfully-parsed JSON objects from embedded script blocks."""
    for m in _JSON_SCRIPT_RE.finditer(html):
        raw = m.group(1).strip()
        if not raw or raw[0] not in "{[":
            continue
        try:
            yield json.loads(raw)
        except Exception:
            continue


def _collect_media_nodes(node: Any, out: List[Dict[str, Any]],
                         _depth: int = 0) -> None:
    """Recursively collect every media-bearing POST node in a parsed blob."""
    if _depth > 40:
        return
    if isinstance(node, dict):
        if _is_post_media_node(node):
            out.append(node)
        for v in node.values():
            _collect_media_nodes(v, out, _depth + 1)
    elif isinstance(node, list):
        for v in node:
            _collect_media_nodes(v, out, _depth + 1)


def _node_pk(n: Dict[str, Any]) -> str:
    return str(n.get("pk") or n.get("id") or n.get("code") or id(n))


def _media_from_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build ONE normalized media item from a post node or carousel child."""
    if not isinstance(item, dict):
        return None
    iv2 = item.get("image_versions2") or {}
    cover = None
    cover_dims = (None, None)
    cands = iv2.get("candidates") if isinstance(iv2, dict) else None
    if isinstance(cands, list) and cands:
        best_img = max(cands, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0))
        cover = best_img.get("url")
        cover_dims = (best_img.get("width"), best_img.get("height"))

    vv = item.get("video_versions")
    if isinstance(vv, list) and vv:
        best_vid = max(
            vv, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0)
            or (c.get("bandwidth") or 0)
        )
        vurl = best_vid.get("url")
        if vurl:
            return {
                "type": "video", "url": vurl, "thumbnail": cover,
                "width": best_vid.get("width"), "height": best_vid.get("height"),
                "ext": "mp4",
            }
    if cover:
        return {
            "type": "image", "url": cover, "thumbnail": cover,
            "width": cover_dims[0], "height": cover_dims[1], "ext": "jpg",
        }
    return None


def _media_from_node(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract media from a single post node (handles carousels), in order."""
    carousel = node.get("carousel_media")
    if isinstance(carousel, list) and carousel:
        out = []
        for child in carousel:
            it = _media_from_item(child)
            if it:
                out.append(it)
        return out
    it = _media_from_item(node)
    return [it] if it else []


def _meta_from_node(node: Dict[str, Any]) -> Dict[str, Any]:
    """Pull caption / timestamp / author from a matched post node."""
    caption = None
    cap = node.get("caption")
    if isinstance(cap, dict):
        caption = _norm_text(cap.get("text"))
    elif isinstance(cap, str):
        caption = _norm_text(cap)
    if not caption:
        caption = _norm_text(node.get("caption_text"))

    timestamp = None
    taken = node.get("taken_at") or node.get("taken_at_ts")
    if isinstance(taken, (int, float)) and taken > 0:
        try:
            timestamp = datetime.fromtimestamp(int(taken), tz=timezone.utc).isoformat()
        except Exception:
            timestamp = None

    author = None
    for key in ("user", "owner"):
        u = node.get(key)
        if isinstance(u, dict) and u.get("username"):
            author = u["username"]
            break

    return {"caption": caption, "timestamp": timestamp, "author_handle": author}


def _extract_scoped_post(html: str, post_id: str) -> Dict[str, Any]:
    """
    Resolve media for ONLY the requested post. Decision order:

      1. EXACT code match — node(s) whose `code` == post_id (rich pages key
         the post by its shortcode). One pk → use it. Distinct pks sharing the
         code → ambiguous (fail).
      2. No code match, but exactly ONE post-media node on the page → use it
         (a single-post page leads with its own post; safe, no contamination).
      3. No code match, MULTIPLE post-media nodes → ambiguous (fail; never
         guess which is the requested post).
      4. No post-media nodes at all → caller falls back to OG (post-scoped).

    Returns {node, media_items, caption, timestamp, author_handle, ambiguous,
    had_media_nodes, match}.
    """
    nodes: List[Dict[str, Any]] = []
    for blob in _iter_json_scripts(html):
        _collect_media_nodes(blob, nodes)

    # Dedupe by primary key — Threads repeats the same post across blocks;
    # keep the richest copy of each.
    by_pk: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        pk = _node_pk(n)
        if pk not in by_pk or len(n) > len(by_pk[pk]):
            by_pk[pk] = n
    uniq = list(by_pk.values())

    def _empty(ambiguous: bool, match: str):
        return {"node": None, "media_items": [], "caption": None,
                "timestamp": None, "author_handle": None,
                "ambiguous": ambiguous, "had_media_nodes": bool(uniq),
                "match": match}

    if not uniq:
        return _empty(False, "none")  # -> OG fallback

    # 1) Exact code match (strict, primary path)
    matched = [n for n in uniq
               if isinstance(n.get("code"), str) and n["code"] == post_id]
    if len(matched) > 1:
        return _empty(True, "code")           # genuine ambiguity
    if len(matched) == 1:
        node, match = matched[0], "code"
    elif len(uniq) == 1:
        node, match = uniq[0], "sole"         # single group → confident
    else:
        return _empty(True, "multi")          # many groups, no code → fail

    meta = _meta_from_node(node)
    return {
        "node": node, "media_items": _media_from_node(node),
        "caption": meta["caption"], "timestamp": meta["timestamp"],
        "author_handle": meta["author_handle"],
        "ambiguous": False, "had_media_nodes": True, "match": match,
    }


def _author_from_og_title(title: Optional[str]) -> Optional[str]:
    """og:title is usually 'Display Name (@handle) on Threads'."""
    if not title:
        return None
    m = re.search(r"\(@([\w.\-]+)\)", title)
    return m.group(1) if m else None


# ── Public: single post extraction ───────────────────────────────────

async def extract_threads_post(url: str) -> Dict[str, Any]:
    """
    Extract a single public Threads post into the normalized schema.

    STRICT single-post semantics:
      • Lock the canonical post_id (shortcode) from the URL first.
      • Return only media from the embedded post node whose code == post_id.
      • Never sweep the page for unrelated posts; never fall back to the
        profile feed. OG tags (server-rendered for THIS url) are the only
        permitted fallback.
      • Ambiguous match (distinct posts claiming the same code) -> fail.
    """
    # ── Lock the canonical post_id up front ──
    post_id = _extract_post_code(url)
    handle = _extract_handle(url)
    canonical = _canonical_post_url(handle, post_id)
    result = _base_result(url, "single_post")
    result["post_id"] = post_id
    result["author_handle"] = handle
    result["canonical_url"] = canonical

    # Local error helper — every failure still carries the locked identity.
    def _err(code: str, detail: str = "") -> Dict[str, Any]:
        return _error_result(url, "single_post", code, detail,
                             post_id=post_id, author_handle=result["author_handle"],
                             canonical_url=canonical)

    if not post_id:
        return _err(ERR_UNSUPPORTED)

    fetched = await _fetch_html(url)
    if fetched["login"]:
        return _err(ERR_PRIVATE)
    if fetched["blocked"]:
        return _err(ERR_BLOCKED)
    html = fetched["html"]
    if not html or len(html) < 500:
        if fetched["status"] in (404, 410):
            return _err(ERR_UNSUPPORTED, "Bài viết không tồn tại hoặc đã bị xóa.")
        return _err(ERR_FAILED)

    og = _parse_og(html)

    # ── Layer A: STRICT post-scoped structured JSON (matched by code) ──
    scoped = _extract_scoped_post(html, post_id)
    if scoped["ambiguous"]:
        return _err(ERR_AMBIGUOUS)

    media: List[Dict[str, Any]] = list(scoped["media_items"])
    result["caption"] = scoped["caption"]
    result["timestamp"] = scoped["timestamp"]
    result["author_handle"] = (
        scoped["author_handle"]
        or _author_from_og_title(og.get("title"))
        or result["author_handle"]
    )

    # ── Layer B: Open Graph fallback (post-scoped: server renders og:* for
    # THIS url). Only used when structured matching yielded no media. ──
    if not media:
        if og.get("video"):
            media.append({
                "type": "video", "url": og["video"],
                "thumbnail": og.get("image"),
                "width": None, "height": None, "ext": "mp4",
            })
        elif og.get("image"):
            media.append({
                "type": "image", "url": og["image"], "thumbnail": og["image"],
                "width": None, "height": None, "ext": "jpg",
            })
        if not result["caption"]:
            result["caption"] = _norm_text(og.get("description"))

    # Carousel detection: more than one media item
    if len(media) > 1:
        result["media_type"] = "carousel"
    elif media:
        result["media_type"] = media[0]["type"]

    result["media_items"] = media
    result["downloadable"] = len(media) > 0

    if not media:
        # Public post reached but no media for THIS post id. Distinguish
        # "text-only / no media" from "media present but unreadable".
        if scoped["had_media_nodes"] or og.get("title") or og.get("description"):
            return _err(ERR_NO_MEDIA)
        return _err(ERR_MEDIA_UNAVAILABLE)

    _safe_print(f"[Threads] post {post_id} -> {len(media)} media item(s) "
                f"[match={scoped['match'] if scoped['media_items'] else 'og'}]")
    return result


# ── Public: profile recent-post listing ──────────────────────────────

_PROFILE_POST_LINK_RE = re.compile(r'/(@[\w.\-]+)/post/([A-Za-z0-9_\-]+)')


def _extract_caption_from_json(html: str, code: str) -> Optional[str]:
    """
    Try to extract a caption snippet for a specific post code from the
    embedded JSON blobs already in the profile HTML.
    """
    for blob in _iter_json_scripts(html):
        nodes: List[Dict[str, Any]] = []
        _collect_media_nodes(blob, nodes)
        for n in nodes:
            if isinstance(n.get("code"), str) and n["code"] == code:
                meta = _meta_from_node(n)
                if meta.get("caption"):
                    cap = meta["caption"]
                    return cap[:120] + "…" if len(cap) > 120 else cap
    return None


async def extract_threads_profile(url: str, selector: int = 50) -> Dict[str, Any]:
    """
    List recent public posts from a profile (lean — no deep crawl).

    Args:
        url:      Profile URL (e.g. https://www.threads.com/@handle)
        selector: Caller-requested post count (capped at _PROFILE_POST_LIMIT).
    """
    limit = min(selector, _PROFILE_POST_LIMIT)

    result = _base_result(url, "profile")
    result["author_handle"] = _extract_handle(url)
    result["canonical_url"] = _canonical_profile_url(result["author_handle"])

    fetched = await _fetch_html(url)
    if fetched["login"]:
        return _error_result(url, "profile", ERR_PRIVATE)
    if fetched["blocked"]:
        return _error_result(url, "profile", ERR_BLOCKED)
    html = fetched["html"]
    if not html or len(html) < 500:
        return _error_result(url, "profile", ERR_FAILED)

    og = _parse_og(html)
    result["author_handle"] = (
        result["author_handle"] or _author_from_og_title(og.get("title"))
    )
    result["caption"] = og.get("description")  # profile bio snippet, if any

    # Collect recent post permalinks (dedupe, preserve order, cap to limit).
    posts: List[Dict[str, Any]] = []
    seen_codes: set = set()
    base = "https://www.threads.com"

    def _add_posts_from_html(source_html: str) -> None:
        for handle, code in _PROFILE_POST_LINK_RE.findall(source_html):
            if code in seen_codes or len(posts) >= limit:
                continue
            seen_codes.add(code)
            has_media = (
                f'"code":"{code}"' in source_html
                and ('"video_versions"' in source_html or '"image_versions2"' in source_html)
            )
            caption_snippet = _extract_caption_from_json(source_html, code)
            posts.append({
                "url": f"{base}/{handle}/post/{code}",
                "post_id": code,
                "caption_snippet": caption_snippet,
                "has_media": has_media,
                "timestamp": None,
            })

    _add_posts_from_html(html)

    # ── Pagination attempt via ?__a=1 ────────────────────────────────
    # If we have fewer posts than requested and the page hints at more
    # content, try the ?__a=1 JSON endpoint (Instagram-style API).
    has_more_hint = '"has_next_page":true' in html or '"end_cursor":"' in html
    if len(posts) < limit and has_more_hint:
        _safe_print("[Threads] Attempting pagination via ?__a=1")
        try:
            base_url = url.split("?")[0].rstrip("/")
            json_url = base_url + "?__a=1"
            json_fetched = await _fetch_html(json_url)
            json_body = json_fetched.get("html") or ""
            if json_body and json_body.lstrip()[:1] in ("{", "["):
                # Looks like a JSON response — scan it for post links and nodes.
                _add_posts_from_html(json_body)
                _safe_print(f"[Threads] Pagination yielded {len(posts)} posts total")
        except Exception as page_err:
            # Silently continue with whatever we already have.
            _safe_print(f"[Threads] Pagination failed (non-fatal): {page_err}")

    result["posts"] = posts
    result["downloadable"] = False  # profile listing itself is not a download
    if not posts:
        # Public profile reached but no posts parsed (anti-bot stripped the
        # feed, or profile has no public posts). Honest, non-fatal state.
        return _error_result(url, "profile", ERR_MEDIA_UNAVAILABLE,
                             "Không lấy được danh sách bài công khai gần đây.")

    _safe_print(f"[Threads] profile @{result['author_handle']} -> {len(posts)} post(s)")
    return result


# ── Public: unified entry ────────────────────────────────────────────

async def extract_threads(url: str) -> Dict[str, Any]:
    """Route a Threads URL to the post or profile extractor."""
    # Empty / non-http(s) / not-a-Threads-URL -> invalid (distinct from a
    # Threads URL we simply don't support, e.g. /search).
    u = (url or "").strip()
    if not u or not re.match(r"^https?://", u, re.IGNORECASE) or not is_threads_url(u):
        return _error_result(url, "single_post", ERR_INVALID)

    kind = classify_threads_url(u)

    # A share link carries a share code, not a post shortcode, so resolve it to
    # the real permalink before anything downstream tries to read an id out of
    # the path. Done here rather than inside the post extractor so the profile
    # branch and the error paths all see the resolved URL too.
    if kind == "share":
        resolved = await _resolve_share_url(u)
        if not resolved:
            return _error_result(
                u, "single_post", ERR_UNSUPPORTED,
                "Không mở được link chia sẻ Threads này. Link có thể đã hết hạn, "
                "bài viết ở chế độ riêng tư, hoặc đã bị xoá. Thử dán link bài viết "
                "trực tiếp (dạng threads.com/@tên/post/...).",
            )
        _safe_print(f"[Threads] share link resolved -> {resolved}")
        u = resolved
        kind = classify_threads_url(u)

    if kind == "post":
        try:
            return await extract_threads_post(u)
        except Exception as e:
            _safe_print(f"[Threads] post extractor crashed: {e}")
            return _error_result(u, "single_post", ERR_FAILED, str(e),
                                 post_id=_extract_post_code(u),
                                 author_handle=_extract_handle(u),
                                 canonical_url=_canonical_post_url(
                                     _extract_handle(u), _extract_post_code(u)))
    if kind == "profile":
        try:
            return await extract_threads_profile(u)
        except Exception as e:
            _safe_print(f"[Threads] profile extractor crashed: {e}")
            return _error_result(u, "profile", ERR_FAILED, str(e))
    # A Threads URL, but neither post nor profile (search, explore, …)
    return _error_result(u, "single_post", ERR_UNSUPPORTED)


def extract_threads_sync(url: str) -> Dict[str, Any]:
    """Synchronous wrapper for Celery / sync contexts."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, extract_threads(url)).result(timeout=60)
    return asyncio.run(extract_threads(url))


# ── Bulk schema adapter: Threads profile -> channel-scrape schema ─────

async def _scrape_threads_profile_bulk(url: str, max_posts: int = 50) -> Dict[str, Any]:
    """
    Internal async helper: call extract_threads_profile and repack into the
    bulk schema used by _scrape_channel_entries_impl in downloader.py.
    """
    profile = await extract_threads_profile(url, selector=max_posts)

    error_code = profile.get("error_code")
    error_message = profile.get("error_message")

    # Map Threads-internal error codes to channel-scrape equivalents.
    if error_code:
        mapped_code = error_code  # pass through; caller can map further
        return {
            "channel_title": profile.get("author_handle") or "",
            "entries": [],
            "total_found": 0,
            "total_queued": 0,
            "error_code": mapped_code,
            "error_message": error_message,
        }

    posts: List[Dict[str, Any]] = profile.get("posts") or []
    entries: List[Dict[str, Any]] = [
        {
            "url": p["url"],
            "title": p.get("caption_snippet") or "Threads post",
        }
        for p in posts
    ]

    handle = profile.get("author_handle") or ""
    channel_title = f"@{handle}" if handle else "Threads Profile"

    return {
        "channel_title": channel_title,
        "entries": entries,
        "total_found": len(entries),
        "total_queued": len(entries),
        "error_code": None,
        "error_message": None,
    }


def scrape_threads_profile(url: str, max_posts: int = 50) -> Dict[str, Any]:
    """
    Adapter: run profile extraction and return bulk-schema result.

    Schema (compatible with _scrape_channel_entries_impl):
        {
          "channel_title": str,
          "entries":       [{"url": str, "title": str}],
          "total_found":   int,
          "total_queued":  int,
          "error_code":    str | None,
          "error_message": str | None,
        }

    This is the sync entry point for use from downloader.py.
    """
    return scrape_threads_profile_sync(url, max_posts)


def scrape_threads_profile_sync(url: str, max_posts: int = 50) -> Dict[str, Any]:
    """Synchronous wrapper for scrape_threads_profile (Celery / sync contexts)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run, _scrape_threads_profile_bulk(url, max_posts)
            ).result(timeout=60)
    return asyncio.run(_scrape_threads_profile_bulk(url, max_posts))


# ── Adapter: Threads post -> common downloader schema ────────────────

def to_download_info(threads_result: Dict[str, Any], quality: str = "video") -> Dict[str, Any]:
    """
    Adapt a single-post Threads result into the common VidGrab download
    schema (title / thumbnail_url / direct_mp4_url / available_formats),
    so the existing /fetch-link download flow can serve Threads media.

    Raises ValueError (with the honest user message) when not downloadable.
    """
    if threads_result.get("error_code"):
        raise ValueError(threads_result.get("error_message")
                         or ERR_MESSAGES[ERR_FAILED])

    media = threads_result.get("media_items", [])
    if not media:
        raise ValueError(ERR_MESSAGES[ERR_NO_MEDIA])

    handle = threads_result.get("author_handle") or "threads"
    caption = (threads_result.get("caption") or "").strip().replace("\n", " ")
    title = (caption[:80] or f"Threads post by @{handle}")

    videos = [m for m in media if m["type"] == "video"]
    images = [m for m in media if m["type"] == "image"]

    primary = videos[0]["url"] if videos else images[0]["url"]
    thumbnail = (videos[0].get("thumbnail") if videos else None) or (
        images[0]["url"] if images else None
    )

    available_formats: List[Dict[str, Any]] = []
    for idx, m in enumerate(videos):
        available_formats.append({
            "type": "video",
            "label": f"Video{'' if len(videos) == 1 else f' #{idx + 1}'}",
            "resolution": "Original", "height": m.get("height") or 0,
            "ext": "mp4", "url": m["url"],
            "filesize_mb": 0, "requires_merge": False,
        })
    for idx, m in enumerate(images):
        available_formats.append({
            "type": "image",
            "label": f"Ảnh{'' if len(images) == 1 else f' #{idx + 1}'}",
            "resolution": "Original", "height": 0,
            "ext": "jpg", "url": m["url"],
            "filesize_mb": 0, "requires_merge": False,
        })

    return {
        "title": title,
        "thumbnail_url": thumbnail,
        "direct_mp4_url": primary,
        "original_url": threads_result.get("original_url"),
        "quality": quality,
        "duration": 0,
        "file_size_mb": 0,
        "available_formats": available_formats,
        "max_merge_height": 0,
        "downloaded_height": 0,
        "is_audio_only": False,
        "platform": "threads",
        "source_type": "single_post",
        "canonical_url": threads_result.get("canonical_url"),
        "media_count": len(media),
    }


# ── Manual test ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://www.threads.com/@zuck"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"Classify: {classify_threads_url(test_url)}")
    out = extract_threads_sync(test_url)
    print(json.dumps(out, indent=2, ensure_ascii=False))
