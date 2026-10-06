"""
Owner 2026-10-06 (correction): a guest gets 5 downloads a day IN TOTAL across
all platforms, a signed-in user 20 in total, admin unlimited. Default
QUOTA_SCOPE=total; per-platform counters are still kept for display.
"""
import pytest

from app.core import quotas
from app.core.quotas import QuotaRequester, REQ_ADMIN, REQ_ANON, REQ_USER
from tests.test_admin_download_metrics import rc  # noqa: F401


@pytest.fixture(autouse=True)
def _env(monkeypatch, rc):  # noqa: F811
    for k in ("PLATFORM_DAILY_LIMIT_ANON", "PLATFORM_DAILY_LIMIT_USER", "QUOTA_SCOPE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(QuotaRequester, "tier", property(lambda self: "free"))


def _use(rq, platform, n, start=0):
    for i in range(start, start + n):
        assert quotas.record_platform_download(rq, platform, f"https://{platform}.example/{i}")


def test_default_scope_is_total():
    assert quotas.quota_scope() == "total"


def test_guest_gets_five_in_total_across_platforms():
    rq = QuotaRequester(REQ_ANON, "1.2.3.4")
    _use(rq, "tiktok", 3)
    _use(rq, "youtube", 2)
    out = quotas.check_platform_quota(rq, "instagram", "https://instagram.example/new")
    assert out["allowed"] is False and out["quota_scope"] == "total"
    assert out["downloads_today"] == 5 and out["daily_limit"] == 5
    assert "cho Instagram" not in out["message"]
    assert out["message"].startswith("Khách tải được tối đa 5 lượt/ngày.")


def test_user_gets_twenty_in_total():
    rq = QuotaRequester(REQ_USER, "u-1")
    _use(rq, "tiktok", 10)
    _use(rq, "douyin", 9)
    assert quotas.check_platform_quota(rq, "youtube", "https://youtube.example/x")["allowed"] is True
    _use(rq, "youtube", 1)
    out = quotas.check_platform_quota(rq, "kuaishou", "https://kuaishou.example/y")
    assert out["allowed"] is False and out["daily_limit"] == 20
    assert out["message"].startswith("Bạn đã dùng hết 20 lượt hôm nay.")


def test_admin_unlimited():
    rq = QuotaRequester(REQ_ADMIN)
    assert quotas.check_platform_quota(rq, "tiktok")["allowed"] is True
    assert quotas.record_platform_download(rq, "tiktok", "https://t/1") is False


def test_same_url_still_counts_once():
    rq = QuotaRequester(REQ_ANON, "5.6.7.8")
    assert quotas.record_platform_download(rq, "tiktok", "https://tiktok.example/a")
    assert not quotas.record_platform_download(rq, "tiktok", "https://tiktok.example/a")
    assert quotas.platform_used(rq, quotas._TOTAL_BUCKET) == 1


def test_batch_allowance_uses_the_total():
    rq = QuotaRequester(REQ_ANON, "9.9.9.9")
    _use(rq, "tiktok", 3)
    b = quotas.BatchAllowance(rq)
    assert b.take("youtube", "https://youtube.example/1") is None
    assert b.take("douyin", "https://douyin.example/1") is None
    refused = b.take("tiktok", "https://tiktok.example/new")
    assert refused and "cho TikTok" not in refused


def test_snapshot_reports_total_and_per_platform_display():
    rq = QuotaRequester(REQ_USER, "u-2")
    _use(rq, "tiktok", 2)
    _use(rq, "youtube", 1)
    snap = quotas.platform_usage_snapshot(rq)
    assert snap["scope"] == "total" and snap["used_total"] == 3 and snap["remaining_total"] == 17
    rows = {r["platform"]: r["used"] for r in snap["platforms"]}
    assert rows["tiktok"] == 2 and rows["youtube"] == 1
    legacy = quotas.legacy_usage_fields(snap)
    assert legacy["used"] == 3 and legacy["limit"] == 20 and legacy["used_platform"] is None


def test_per_platform_mode_still_available(monkeypatch):
    monkeypatch.setenv("QUOTA_SCOPE", "per_platform")
    rq = QuotaRequester(REQ_ANON, "7.7.7.7")
    _use(rq, "tiktok", 5)
    assert quotas.check_platform_quota(rq, "youtube", "https://youtube.example/z")["allowed"] is True
