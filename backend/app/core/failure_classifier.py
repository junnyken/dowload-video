"""
Failure Classifier — Phase 12
================================
Classifies download/job errors into categories that drive
auto-retry, user messaging, and anomaly detection.

Categories:
  TRANSIENT     — brief network blip, retry now
  RETRYABLE     — rate limit / block, retry with backoff
  PERMANENT     — DRM, private, unsupported — do not retry
  USER_ACTION   — login needed, user must fix

Usage:
  fc = classify_failure("sign in to confirm your age")
  # → FailureClass.USER_ACTION
"""

from enum import Enum


class FailureClass(str, Enum):
    TRANSIENT   = "transient"      # retry immediately (short backoff)
    RETRYABLE   = "retryable"      # retry with longer backoff
    PERMANENT   = "permanent"      # no auto-retry
    USER_ACTION = "user_action"    # user must fix before retry


# ── Signal lists (checked in order: PERMANENT > USER_ACTION > RETRYABLE > TRANSIENT)

_PERMANENT_SIGNALS = [
    "drm", "content is not available", "video unavailable", "this video has been removed",
    "this video is not available", "no video formats found", "unsupported url",
    "this channel does not have a video", "private video", "copyright", "dmca",
    "geo_restriction", "not available in your region", "this content isn't available",
    "this playlist does not exist", "account has been terminated",
    "content may not be available", "no media", "no formats", "webpage not found",
    "unable to extract", "404", "410",
]

_USER_ACTION_SIGNALS = [
    "sign in to confirm", "sign in to watch", "log in", "login_required",
    "login required", "authentication required", "members only", "subscriber only",
    "purchase required", "this video is only available to", "age-restricted",
    "confirm your age", "age verification", "not available to anonymous",
    "content is age-restricted",
]

_RETRYABLE_SIGNALS = [
    "rate limit", "too many requests", "429", "quota exceeded",
    "bị chặn", "xác thực", "bot", "captcha", "please verify", "temporarily unavailable",
    "503", "502", "500", "cloudflare", "access denied", "forbidden", "403",
    "your ip has been blocked", "ip ban", "temporary block",
    "video is being processed", "processing", "try again later",
    "temporary error", "temporary failure", "service temporarily unavailable",
]

_TRANSIENT_SIGNALS = [
    "connection reset", "connection refused", "connection timed out",
    "read timeout", "remote end closed", "broken pipe", "network unreachable",
    "name resolution failed", "dns", "ssl", "certificate verify failed",
    "chunked encoding", "incomplete read", "connection error",
    "connection aborted", "socket",
]


def classify_failure(error_msg: str) -> FailureClass:
    """Return the failure class for a given error message."""
    msg = error_msg.lower()

    # Check PERMANENT first — do not loop on these
    if any(sig in msg for sig in _PERMANENT_SIGNALS):
        return FailureClass.PERMANENT

    # Check USER_ACTION — user must intervene
    if any(sig in msg for sig in _USER_ACTION_SIGNALS):
        return FailureClass.USER_ACTION

    # Check RETRYABLE — backoff and retry
    if any(sig in msg for sig in _RETRYABLE_SIGNALS):
        return FailureClass.RETRYABLE

    # Check TRANSIENT — brief network blip
    if any(sig in msg for sig in _TRANSIENT_SIGNALS):
        return FailureClass.TRANSIENT

    # Default: treat as retryable (safer than giving up silently)
    return FailureClass.RETRYABLE


def backoff_seconds(attempt: int, failure_class: FailureClass) -> int:
    """Exponential backoff with jitter, bounded by class."""
    import random
    base = {
        FailureClass.TRANSIENT:   15,
        FailureClass.RETRYABLE:   60,
        FailureClass.PERMANENT:    0,
        FailureClass.USER_ACTION:  0,
    }[failure_class]
    if base == 0:
        return 0
    jitter = random.uniform(0.8, 1.2)
    return int(min(base * (2 ** attempt) * jitter, 1800))   # cap at 30 min


def max_retries_for_class(failure_class: FailureClass) -> int:
    """Maximum auto-retry count per failure class."""
    return {
        FailureClass.TRANSIENT:   4,
        FailureClass.RETRYABLE:   3,
        FailureClass.PERMANENT:   0,
        FailureClass.USER_ACTION: 0,
    }[failure_class]


def auto_retry_status(failure_class: FailureClass, attempt: int) -> str:
    """Return the download_jobs.auto_retry_status value."""
    if failure_class in (FailureClass.PERMANENT, FailureClass.USER_ACTION):
        return "retry_suppressed"
    max_r = max_retries_for_class(failure_class)
    if attempt >= max_r:
        return "retry_limit_reached"
    return "auto_retry_scheduled"


def user_recovery_message(failure_class: FailureClass, attempt: int, backoff: int) -> str:
    """Short Vietnamese message shown to user in UI."""
    max_r = max_retries_for_class(failure_class)
    if failure_class == FailureClass.PERMANENT:
        return "Không thể tải — nội dung không khả dụng."
    if failure_class == FailureClass.USER_ACTION:
        return "Cần đăng nhập hoặc quyền truy cập — thực hiện thủ công."
    if attempt >= max_r:
        return "Đã thử lại nhiều lần nhưng không thành công. Nhấn Retry để thử thủ công."
    if backoff < 120:
        return f"⏳ Đang thử lại tự động sau {backoff}s (lần {attempt + 1}/{max_r})..."
    return f"⏳ Sẽ thử lại sau {backoff // 60} phút (lần {attempt + 1}/{max_r})..."
