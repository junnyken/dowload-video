"""
Normalized failures for the China access layer (plan §5.3) + redaction.

Categories map onto the EXISTING error codes in app/core/error_codes.py — no
parallel codes for the same thing (e.g. Douyin's signature failure has been
reported as `cookie_required` since 2026-10-05 and stays that way).
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel

from app.services.china_platforms import settings

FailureCategory = Literal[
    "unsupported_url",
    "private_or_login_required",
    "geo_restricted",
    "signature_or_verification_failed",
    "cookie_required",
    "cookie_invalid",
    "upstream_rate_limited",
    "provider_timeout",
    "provider_unavailable",
    "parse_failed",
    "budget_exceeded",
    "platform_disabled",
    "unknown",
]

ALL_CATEGORIES: tuple[str, ...] = FailureCategory.__args__  # type: ignore[attr-defined]

# Category → existing error code (app/core/error_codes.py ERROR_META keys).
CATEGORY_TO_ERROR_CODE: dict[str, str] = {
    "unsupported_url": "unsupported_url",
    "private_or_login_required": "private_or_login_required",
    "geo_restricted": "geo_blocked",
    # 2026-10-05 incident: Douyin's signature/verification failure is what
    # production already reports as cookie_required (DOUYIN_COOKIE_REQUIRED_MSG).
    "signature_or_verification_failed": "cookie_required",
    "cookie_required": "cookie_required",
    "cookie_invalid": "cookie_required",
    "upstream_rate_limited": "rate_limited",
    "provider_timeout": "provider_unavailable",
    "provider_unavailable": "provider_unavailable",
    "parse_failed": "no_media_found",
    # Users are not told about internal budgets or switches.
    "budget_exceeded": "provider_unavailable",
    "platform_disabled": "provider_unavailable",
    "unknown": "processing_failed",
}

# Never treated as a provider-health failure (user/policy outcomes, not
# provider faults) — keeps health from flapping on bad links.
NON_HEALTH_CATEGORIES = frozenset({
    "unsupported_url", "private_or_login_required", "budget_exceeded", "platform_disabled",
})

# Retryable by the user/job later (mirrors ERROR_META retryable of the code).
_RETRYABLE = frozenset({"upstream_rate_limited", "provider_timeout", "provider_unavailable", "unknown"})


def error_code_for(category: str) -> str:
    return CATEGORY_TO_ERROR_CODE.get(category, "processing_failed")


def user_message_for(category: str) -> str:
    from app.core.error_codes import get_error_meta  # noqa: PLC0415
    return get_error_meta(error_code_for(category))["user_message"]


class ProviderFailure(BaseModel):
    platform: str
    provider_name: str
    category: FailureCategory
    retryable: bool
    user_message: str
    internal_detail: Optional[str] = None


def make_failure(platform: str, provider: str, category: str, detail: str | None = None) -> ProviderFailure:
    if category not in ALL_CATEGORIES:
        category = "unknown"
    return ProviderFailure(
        platform=platform,
        provider_name=provider,
        category=category,  # type: ignore[arg-type]
        retryable=category in _RETRYABLE,
        user_message=user_message_for(category),
        internal_detail=redact(detail)[:500] if detail else None,
    )


class ProviderFailureError(Exception):
    """Raised by a provider; carries one normalized failure."""

    def __init__(self, failure: ProviderFailure):
        super().__init__(f"{failure.provider_name}: {failure.category}")
        self.failure = failure


class ChinaAccessFailure(Exception):
    """Router result when no provider produced a usable result."""

    def __init__(self, platform: str, category: str, failures: list[ProviderFailure],
                 stop_reason: str = "exhausted"):
        self.platform = platform
        self.category = category if category in ALL_CATEGORIES else "unknown"
        self.failures = failures
        self.stop_reason = stop_reason
        super().__init__(f"china_access {platform}: {self.category} ({stop_reason})")

    @property
    def error_code(self) -> str:
        return error_code_for(self.category)

    @property
    def user_message(self) -> str:
        return user_message_for(self.category)

    def has_category(self, category: str) -> bool:
        return self.category == category or any(f.category == category for f in self.failures)


class AlreadyProcessing(Exception):
    """An identical canonical URL is being resolved right now (plan §9.3)."""

    def __init__(self, platform: str, url_hash: str):
        self.platform = platform
        self.url_hash = url_hash
        super().__init__(f"already_processing {platform}:{url_hash[:12]}")


# ── redaction ───────────────────────────────────────────────────────────────

# Any URL's query string: Douyin CDN URLs are signed with x-signature/x-expires,
# which the generic redact_secrets pattern does not match (it needs [?&]sig=).
_URL_QUERY = re.compile(r"(https?://[^\s\"'<>?#]+)\?[^\s\"'<>]*", re.IGNORECASE)
_COOKIE_HEADER = re.compile(r"(?i)(cookie|set-cookie)\s*[:=]\s*[^\n]+")


def strip_query(url: str | None) -> str:
    if not url:
        return ""
    return _URL_QUERY.sub(r"\1?<redacted>", str(url))


def redact(text) -> str:
    """Remove tokens, cookies and signed-URL query strings from free text."""
    if text is None:
        return ""
    s = str(text)
    for secret in settings.secret_values():
        s = s.replace(secret, "<redacted>")
    s = _URL_QUERY.sub(r"\1?<redacted>", s)
    s = _COOKIE_HEADER.sub(r"\1: <redacted>", s)
    try:
        from app.core.structured_log import redact_secrets  # noqa: PLC0415
        s = redact_secrets(s)
    except Exception:  # noqa: BLE001
        pass
    return s
