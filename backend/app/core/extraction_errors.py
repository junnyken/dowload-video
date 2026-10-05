"""
Map an extraction failure to the HTTP status it actually deserves.

/fetch-link used to answer every failure with 500. A link to a page that is
not a video, a deleted TikTok and a private post are the user's input being
wrong, not the server being broken, and a 500 for them tells monitoring,
retry logic and the user the opposite of the truth. Only real server-side
faults (extractor crash, upstream blocking us, timeouts) stay 5xx.

Status policy (kept deliberately small so callers can rely on it):

  400  unsupported_url            not a supported site / not a video page
  404  video_unavailable          video removed, deleted or does not exist
  422  private_or_login_required  exists but private / members-only / age-gated
  422  drm_protected | geo_blocked  exists but cannot be served by us
  502  provider_unavailable       upstream 5xx, extractor crash on a known site
  503  temporary_blocked          platform rate-limits / bot-checks OUR server
  504  extraction_timeout         extraction exceeded the hard timeout
  500  processing_failed          anything we could not classify

Classification reads the yt-dlp reason ("Lý do kỹ thuật: ...") when the
downloader appended one, and only falls back to the whole message otherwise.
The friendly Vietnamese prefixes the downloader writes mention "riêng tư",
"xoá", "đăng nhập" for EVERY failure on a platform, so matching on them would
classify by guesswork — the reason is what yt-dlp actually observed.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

_REASON_RE = re.compile(r"Lý do kỹ thuật:\s*(.+)\)\s*$", re.S)

# Ordered: first matching group wins. Server-side blocking is checked before
# "unavailable" because YouTube's bot throttle reads "Video unavailable. This
# content isn't available, try again later." — that is us being throttled,
# not the video being gone.
_TIMEOUT = ("quá thời gian chờ", "timed out", "read timeout", "timeout")
_BLOCKED = (
    "sign in to confirm", "not a bot", "confirm you're not a bot", "confirm you’re not a bot",
    "try again later", "rate-limit", "rate limit", "too many requests", "http error 429",
    "captcha", "please verify", "chặn bot",
)
_DRM = ("drm", "widevine", "encrypted", "clearkey")
_GEO = (
    "not available in your country", "not available in your region",
    "geo restrict", "geo-restrict", "geo_restriction", "made this video available in your country",
    "available in your region",
)
_PRIVATE = (
    "private video", "private account", "this account is private", "is private",
    "you do not have permission to view this post", "members-only", "members only",
    "join this channel", "subscriber only", "age-restricted", "confirm your age",
    "inappropriate for some users", "premium members",
)
_NOT_FOUND = (
    "video unavailable", "this video has been removed", "has been removed",
    "this video is no longer available", "no longer available", "does not exist",
    "doesn't exist", "video not found", "post not found", "page not found",
    "http error 404", "http error 410", "404: not found",
    "video not available, status code", "this post is unavailable",
    "content isn't available", "content is not available", "post may have been deleted",
    "page isn't available", "removed by the uploader", "account has been terminated",
    "has been deleted", "was deleted",
    # TikTok status 10204: yt-dlp words it as an IP block, but TikTok returns
    # it for item-not-found — verified 2026-10-02: a made-up video id on the
    # live API produced exactly this while the TikTok probe stayed green.
    "your ip address is blocked from accessing this post",
)
_UNSUPPORTED = ("unsupported url", "is not a valid url", "no video formats found",
                "no video could be found", "no media found")
_UPSTREAM = ("http error 5", "service unavailable", "bad gateway", "connection reset",
             "connection refused", "remote end closed", "unable to extract",
             "please report this issue", "extractorerror", "keyerror", "indexerror",
             "typeerror", "attributeerror")


# Codes our own (non-yt-dlp) extractors raise. Such an exception carries
# `.error_code`, and its text ends in "(Lý do kỹ thuật: <code>)" so the code
# survives paths that keep only the string (Celery). Checked before any
# keyword matching: these extractors already know what went wrong.
_EXTRACTOR_CODE_STATUS: dict[str, int] = {
    # Kuaishou (app.services.kuaishou_extractor) — our server cannot reach the
    # site / is challenged: server-side, 5xx, never the user's link.
    "kuaishou_geo_blocked":           503,
    "kuaishou_proxy_error":           503,
    "kuaishou_upstream_unreachable":  503,
    "kuaishou_challenge_page":        503,
    "kuaishou_too_many_redirects":    502,
    "kuaishou_unsafe_address":        502,
    "kuaishou_parse_failed":          502,
    "kuaishou_media_url_rejected":    502,
    "kuaishou_response_too_large":    502,
    # The link itself.
    "kuaishou_invalid_url":           400,
    "kuaishou_redirect_rejected":     400,
    "kuaishou_not_found":             404,
    "kuaishou_unsupported_post_type": 422,
}


def _own_code(message: str, exc: Optional[BaseException]) -> Optional[str]:
    code = getattr(exc, "error_code", None)
    if isinstance(code, str) and code in _EXTRACTOR_CODE_STATUS:
        return code
    reason = _reason(message).strip()
    return reason if reason in _EXTRACTOR_CODE_STATUS else None


def _reason(msg: str) -> str:
    m = _REASON_RE.search(msg or "")
    return m.group(1) if m else (msg or "")


def classify_extraction_error(
    message: str, exc: Optional[BaseException] = None
) -> Tuple[int, str]:
    """Return (http_status, error_code) for an extraction failure. Never raises."""
    try:
        own = _own_code(message, exc)
        if own:
            return _EXTRACTOR_CODE_STATUS[own], own
        if isinstance(exc, TimeoutError):
            return 504, "extraction_timeout"
        text = _reason(message).lower()
        full = (message or "").lower()

        if any(s in full for s in _TIMEOUT) and "lý do kỹ thuật" not in full:
            return 504, "extraction_timeout"
        if "không đủ dung lượng" in full:
            return 503, "server_error"
        # The generic extractor ran: the URL is not on a site we support, or
        # the page has no video. That is the user's link, whatever HTTP status
        # the page itself answered with.
        if text.lstrip().startswith("[generic]"):
            return 400, "unsupported_url"
        if any(s in text for s in _UNSUPPORTED):
            return 400, "unsupported_url"
        if any(s in text for s in _BLOCKED):
            return 503, "temporary_blocked"
        if any(s in text for s in _DRM):
            return 422, "drm_protected"
        if any(s in text for s in _GEO):
            return 422, "geo_blocked"
        if any(s in text for s in _PRIVATE):
            return 422, "private_or_login_required"
        if any(s in text for s in _NOT_FOUND):
            return 404, "video_unavailable"
        if any(s in text for s in _TIMEOUT):
            return 504, "extraction_timeout"
        if any(s in text for s in _UPSTREAM):
            return 502, "provider_unavailable"
        if exc is not None and not isinstance(exc, ValueError):
            # A non-ValueError escaping the downloader is a crash in our own
            # code path or an extractor, not a verdict about the link.
            return 502, "provider_unavailable"
        return 500, "processing_failed"
    except Exception:
        return 500, "processing_failed"


def is_user_error(status: int) -> bool:
    return 400 <= status < 500


# ── HTTP surface ──────────────────────────────────────────────────────

from fastapi import HTTPException  # noqa: E402


class ExtractionHTTPException(HTTPException):
    """
    HTTPException that also carries an error_code.

    `detail` stays the plain Vietnamese string it always was — every frontend
    call site and the extension read `data.detail` as text, and several do
    `new Error(data.detail)`, which would print "[object Object]" if detail
    became a dict. The code rides alongside at the top level instead, written
    by `extraction_http_exception_handler` (registered in app.main).
    """

    def __init__(self, status_code: int, message: str, error_code: str):
        super().__init__(status_code=status_code, detail=message)
        self.error_code = error_code


def extraction_exception(message: str, exc: Optional[BaseException] = None) -> ExtractionHTTPException:
    status, code = classify_extraction_error(message, exc)
    return ExtractionHTTPException(status, message, code)


async def extraction_http_exception_handler(request, exc: ExtractionHTTPException):
    from fastapi.responses import JSONResponse
    from app.core.error_codes import get_error_meta
    meta = get_error_meta(exc.error_code)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail":       exc.detail,
            "error_code":   exc.error_code,
            "user_message": exc.detail,
            "retryable":    meta.get("retryable", exc.status_code >= 500),
        },
        headers=getattr(exc, "headers", None),
    )
