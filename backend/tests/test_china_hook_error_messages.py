"""
Task #6055 — Kuaishou / Xiaohongshu failure text by cause (owner 2026-10-07).
Before: every failure read "Nền tảng nguồn tạm thời không phản hồi." — also a
guest who had used up the day, and a dead share link.

Holds, for each cause: the Vietnamese text, the HTTP status /fetch-link
answers, and whether a Celery job retries it.
"""
from __future__ import annotations

import pytest

from app.core.extraction_errors import classify_extraction_error
from app.core.failure_classifier import FailureClass, classify_failure
from app.services.china_platforms import integration
from app.services.china_platforms.errors import ChinaAccessFailure, make_failure
from app.services.china_platforms.normalized_models import RequestContext
from tests._china_fakes import clean_env, rc  # noqa: F401

GUEST = RequestContext(requester_key="ip:198.51.100.7", origin="request")
USER = RequestContext(requester_key="user:u-1", origin="request")


def _fail(category: str, detail: str = "x", platform: str = "kuaishou") -> ChinaAccessFailure:
    f = make_failure(platform, "apify_kuaishou", category, detail)
    return ChinaAccessFailure(platform, category, [f])


def _err(exc, ctx=GUEST, platform="kuaishou"):
    return integration._hook_platform_error(platform, exc, ctx)


class TestMessages:

    def test_guest_daily_limit_invites_sign_in(self, clean_env):
        e = _err(_fail("budget_exceeded", "user_quota: ip daily managed limit"))
        assert str(e) == "Bạn đã dùng hết lượt tải Kuaishou của khách hôm nay. Đăng nhập để có 20 lượt/ngày."
        assert e.error_code == "china_daily_limit"

    def test_user_daily_limit_names_reset_time(self, clean_env):
        e = _err(_fail("budget_exceeded", "user_quota: user daily managed limit"), ctx=USER)
        assert "Bạn đã dùng hết lượt tải Kuaishou hôm nay" in str(e) and "07:00" in str(e)

    def test_platform_spend_ceiling_is_not_called_the_users_limit(self, clean_env):
        e = _err(_fail("budget_exceeded", "platform_spend: kuaishou daily spend ceiling"))
        assert e.error_code == "china_source_unavailable"
        assert str(e) == "Nền tảng nguồn tạm thời không phản hồi. Thử lại sau vài phút."

    @pytest.mark.parametrize("cat", ["parse_failed", "unsupported_url"])
    def test_dead_link(self, clean_env, cat):
        e = _err(_fail(cat))
        assert str(e).startswith("Link Kuaishou này không còn video hoặc đã hết hạn.")
        assert "Sao chép liên kết" in str(e) and e.error_code == "china_link_unavailable"

    @pytest.mark.parametrize("cat", ["provider_timeout", "provider_unavailable", "upstream_rate_limited",
                                     "unknown", "platform_disabled"])
    def test_upstream(self, clean_env, cat):
        e = _err(_fail(cat))
        assert str(e) == "Nền tảng nguồn tạm thời không phản hồi. Thử lại sau vài phút."

    def test_private(self, clean_env):
        e = _err(_fail("private_or_login_required"))
        assert e.error_code == "private_or_login_required" and "riêng tư" in str(e)

    def test_xiaohongshu_uses_its_own_name(self, clean_env):
        e = _err(_fail("parse_failed", platform="xiaohongshu"), platform="xiaohongshu")
        assert "Link Xiaohongshu này" in str(e) and "app Xiaohongshu" in str(e)

    def test_router_failure_reaches_the_caller_as_this_error(self, clean_env, monkeypatch):
        async def boom(self, req, ctx=None, **kw):
            raise _fail("parse_failed")
        monkeypatch.setattr("app.services.china_platforms.provider_router.ProviderRouter.resolve", boom)
        with pytest.raises(integration.ChinaUserError) as ei:
            integration._resolve_via_access_layer("kuaishou", "https://v.kuaishou.com/abc",
                                                  "https://v.kuaishou.com/abc", "video", None)
        assert ei.value.error_code == "china_link_unavailable"

    def test_douyin_keeps_its_cookie_wording(self, clean_env, monkeypatch):
        async def boom(self, req, ctx=None, **kw):
            raise _fail("cookie_required", platform="douyin")
        monkeypatch.setattr("app.services.china_platforms.provider_router.ProviderRouter.resolve", boom)
        with pytest.raises(ValueError) as ei:
            integration._resolve_via_access_layer("douyin", "https://www.douyin.com/video/7300000000000000001",
                                                  "x", "video", None)
        assert not isinstance(ei.value, integration.ChinaUserError)
        assert "cookie" in str(ei.value).lower()


class TestStatusAndRetry:

    def test_http_status(self, clean_env):
        cases = {
            ("budget_exceeded", "user_quota: ip"): (422, "china_daily_limit"),
            ("parse_failed", "x"): (404, "china_link_unavailable"),
            ("provider_timeout", "x"): (503, "china_source_unavailable"),
            ("private_or_login_required", "x"): (422, "private_or_login_required"),
        }
        for (cat, detail), want in cases.items():
            e = _err(_fail(cat, detail))
            assert classify_extraction_error(str(e), e) == want, cat

    def test_celery_retry_class_from_text_only(self, clean_env):
        limit = str(_err(_fail("budget_exceeded", "user_quota: ip")))
        dead = str(_err(_fail("parse_failed")))
        slow = str(_err(_fail("provider_timeout")))
        assert classify_failure(limit) == FailureClass.USER_ACTION
        assert classify_failure(dead) == FailureClass.PERMANENT
        assert classify_failure(slow) == FailureClass.RETRYABLE
