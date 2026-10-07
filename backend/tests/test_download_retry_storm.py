"""
Download reliability fixes measured on production 2026-10-06.

2026-10-05: ~175 Douyin jobs were created in the same second (a bulk/channel
run). The Douyin extractor answered every one with its "needs a valid cookie"
message; the failure classifier did not recognise it, so it defaulted to
RETRYABLE (3 retries), the worker counted an outcome on EVERY attempt, and the
admin showed 1233 failures that day labelled "processing_failed — lỗi không
xác định". These tests drive the real code paths (classifier, worker task,
bulk route, outcome store, admin endpoints) to hold each fix in place.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.core import download_outcomes as do
from app.core.failure_classifier import (
    FailureClass, MAX_TOTAL_ATTEMPTS, classify_failure, is_cookie_required,
    max_retries_for_class,
)
from app.services.douyin_extractor import DOUYIN_COOKIE_REQUIRED_MSG

# Fixtures shared with the existing outcome-store tests.
from tests.test_admin_download_metrics import (  # noqa: F401
    _FakeSupabase, admin, fixed_now, rc, route, sync_outcomes,
)


# ── 1. Classifier: our real cookie/login messages ───────────────────────────

# Copied verbatim from the code that raises them; test_messages_still_exist
# keeps these copies honest if the source wording changes.
REAL_COOKIE_MESSAGES = {
    "douyin":     DOUYIN_COOKIE_REQUIRED_MSG,
    # downloader wraps the extractor error like this on the Douyin path
    "douyin_wrapped": "Không thể tải video Douyin: " + DOUYIN_COOKIE_REQUIRED_MSG,
    "instagram":  "Instagram yêu cầu đăng nhập để tải video. "
                  "Vui lòng liên hệ admin để cấu hình cookie Instagram.",
    "twitter":    "Twitter/X yêu cầu đăng nhập để tải video. "
                  "Vui lòng liên hệ admin để cấu hình cookie Twitter/X.",
    "spaces":     "Twitter Spaces yêu cầu đăng nhập để tải audio. "
                  "Vui lòng liên hệ admin để cấu hình cookie Twitter/X.",
    "ig_story":   "Story Instagram yêu cầu đăng nhập. Thêm cookie Instagram vào admin.",
    "reddit":     "Nội dung NSFW yêu cầu đăng nhập. Thêm cookie Reddit vào admin.",
    "xhs":        "Xiaohongshu yêu cầu cookie. Thêm cookie XHS vào Admin → Cookie Pool (platform: xiaohongshu).",
    "xhs_prof":   "Profile XHS yêu cầu cookie đăng nhập.",
    "capability": "Chưa cấu hình cookie cho nền tảng này. Liên hệ admin để thêm cookie.",
}


def test_messages_still_exist_in_the_source():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "app" / "services"
    src = "".join(p.read_text(encoding="utf-8") for p in root.rglob("*.py"))
    flat = src.replace('"\n', "").replace("\n", " ")
    for key, needle in {
        "instagram": "Instagram yêu cầu đăng nhập để tải video",
        "twitter": "Twitter/X yêu cầu đăng nhập để tải video",
        "spaces": "Twitter Spaces yêu cầu đăng nhập để tải audio",
        "ig_story": "Story Instagram yêu cầu đăng nhập",
        "reddit": "Nội dung NSFW yêu cầu đăng nhập",
        "xhs": "Xiaohongshu yêu cầu cookie",
        "xhs_prof": "Profile XHS yêu cầu cookie đăng nhập",
        "capability": "Chưa cấu hình cookie cho nền tảng này",
    }.items():
        assert needle in src, f"{key}: message wording changed — update COOKIE_REQUIRED_SIGNALS"
    assert "raise ValueError(DOUYIN_COOKIE_REQUIRED_MSG)" in flat


@pytest.mark.parametrize("key", sorted(REAL_COOKIE_MESSAGES))
def test_cookie_messages_are_user_action_not_retried(key):
    msg = REAL_COOKIE_MESSAGES[key]
    assert is_cookie_required(msg)
    assert classify_failure(msg) == FailureClass.USER_ACTION
    assert max_retries_for_class(classify_failure(msg)) == 0


@pytest.mark.parametrize("key", sorted(REAL_COOKIE_MESSAGES))
def test_cookie_messages_get_the_cookie_required_code(key):
    from app.core.extraction_errors import classify_extraction_error
    assert classify_extraction_error(REAL_COOKIE_MESSAGES[key], ValueError("x")) == (422, "cookie_required")


def test_catch_all_instagram_message_is_not_called_a_cookie_problem():
    """Printed after ANY yt-dlp failure on Instagram — the reason decides."""
    msg = ("Không thể tải video Instagram. Instagram yêu cầu đăng nhập. "
           "Vui lòng upload cookies Instagram (hết hạn sau ~7 ngày) "
           "qua Admin panel: POST /admin/cookies/upload (Lý do kỹ thuật: HTTP Error 404: Not Found)")
    from app.core.extraction_errors import classify_extraction_error
    assert not is_cookie_required(msg)
    assert classify_extraction_error(msg)[1] == "video_unavailable"


def test_unrecognised_error_still_retries_but_never_beyond_three_runs():
    fc = classify_failure("something nobody has seen before")
    assert fc == FailureClass.RETRYABLE
    assert MAX_TOTAL_ATTEMPTS == 3
    for cls in FailureClass:
        assert 1 + max_retries_for_class(cls) <= MAX_TOTAL_ATTEMPTS


def test_cookie_required_has_a_label_for_the_admin():
    from app.api.admin import _error_label
    from app.core.error_codes import ERROR_META
    assert ERROR_META["cookie_required"]["retryable"] is False
    assert _error_label("cookie_required") == ERROR_META["cookie_required"]["user_message"]


# ── 2. Worker: attempts and outcome records per failing job ─────────────────

class _JobsDB:
    """download_jobs as the worker uses it: select(...).eq().single(), update().eq()."""

    def __init__(self, row):
        self.row = dict(row)
        self.updates = []

    def table(self, name):
        db = self

        class Q:
            def __init__(self):
                self._upd = None

            def select(self, *a, **k):
                return self

            def update(self, data):
                self._upd = data
                return self

            def insert(self, data):
                return self

            def eq(self, *a, **k):
                return self

            def neq(self, *a, **k):
                return self

            def single(self):
                return self

            def execute(self):
                if self._upd is not None:
                    db.updates.append(dict(self._upd))
                    db.row.update(self._upd)
                return MagicMock(data=dict(db.row))

        return Q()


class _Retry(Exception):
    pass


@pytest.fixture
def worker(monkeypatch, rc):
    """process_video_task with its I/O replaced; outcomes go to fakeredis."""
    import app.tasks.video_tasks as vt
    from app.core import platform_lanes

    calls = {"extract": 0, "dispatched": []}
    db = _JobsDB({"id": "job-1", "status": "pending", "retry_count": 0})
    monkeypatch.setattr(vt, "get_supabase_client", lambda: db)
    monkeypatch.setattr(vt, "get_cached_result", lambda url: None)
    monkeypatch.setattr(vt, "_platform_sleep", lambda url: None)
    monkeypatch.setattr(platform_lanes, "try_acquire_platform", lambda p: True)
    monkeypatch.setattr(platform_lanes, "try_acquire_user", lambda u: True)
    monkeypatch.setattr(platform_lanes, "release_platform", lambda p: None)
    monkeypatch.setattr(platform_lanes, "release_user", lambda u: None)
    monkeypatch.setattr("app.core.notifications.notify_job_failed_sync", lambda *a, **k: None)

    def _retry(countdown=None, exc=None, **k):
        raise _Retry()
    monkeypatch.setattr(vt.process_video_task, "retry", _retry)
    monkeypatch.setattr(vt.process_video_task, "apply_async",
                        lambda *a, **k: calls["dispatched"].append(k))

    def set_error(msg):
        def _boom(*a, **k):
            calls["extract"] += 1
            raise ValueError(msg)
        monkeypatch.setattr(vt, "extract_video_info_sync", _boom)

    def run_once(retries=0, url="https://www.douyin.com/video/7000000000000000001", **kw):
        """One Celery execution. Returns True when it asked for a retry."""
        vt.process_video_task.push_request(id=f"task-{retries}-{len(calls['dispatched'])}",
                                           retries=retries)
        try:
            vt.process_video_task.run("job-1", url, None, "video", **kw)
            return False
        except _Retry:
            return True
        finally:
            vt.process_video_task.pop_request()

    def run_like_celery(url="https://www.douyin.com/video/7000000000000000001"):
        """Execute, then re-execute while the task asks to be retried."""
        retries = 0
        while run_once(retries, url):
            retries += 1
        return retries

    return {"vt": vt, "db": db, "calls": calls, "set_error": set_error,
            "run_once": run_once, "run_like_celery": run_like_celery, "rc": rc}


def _outcomes(rc):
    day = do.read_days([do.day_str(do._now())])[do.day_str(do._now())] or {}
    codes = do.read_error_codes([do.day_str(do._now())])
    return day, codes


def test_cookie_required_job_runs_once_and_counts_one_cookie_required(worker):
    worker["set_error"]("Không thể tải video Douyin: " + DOUYIN_COOKIE_REQUIRED_MSG)
    worker["run_like_celery"]()

    assert worker["calls"]["extract"] == 1, "a cookie problem must not be auto-retried"
    day, codes = _outcomes(worker["rc"])
    assert day == {"douyin": {"ok": 0, "err": 1}}
    assert codes == {("douyin", "cookie_required"): 1}
    row = worker["db"].row
    assert row["status"] == "failed"
    assert row["auto_retry_status"] == "retry_suppressed"
    # the user sees what to do (which cookie, where), not a generic line
    assert DOUYIN_COOKIE_REQUIRED_MSG in row["error_message"]


def test_unrecognised_failure_runs_three_times_and_counts_once(worker):
    worker["set_error"]("weird upstream answer nobody classified")
    worker["run_like_celery"](url="https://www.tiktok.com/@a/video/1")

    assert worker["calls"]["extract"] == 3            # was 4 (1 + 3 retries)
    day, codes = _outcomes(worker["rc"])
    assert day == {"tiktok": {"ok": 0, "err": 1}}     # was 4 err (one per attempt)
    assert sum(codes.values()) == 1
    assert worker["db"].row["status"] == "failed"
    assert worker["db"].row["auto_retry_status"] == "retry_limit_reached"


def test_a_re_queued_job_keeps_its_used_retries(worker):
    """Crash recovery / a manual re-queue sends a NEW task (request.retries=0);
    the job row's retry_count still says two retries were spent."""
    worker["db"].row["retry_count"] = 2
    worker["set_error"]("weird upstream answer nobody classified")
    asked_retry = worker["run_once"](0, url="https://www.tiktok.com/@a/video/1")
    assert asked_retry is False
    assert worker["calls"]["extract"] == 1
    assert worker["db"].row["status"] == "failed"


def test_lane_redispatch_is_not_an_attempt_and_carries_the_retry_count(worker, monkeypatch):
    from app.core import job_lease, platform_lanes
    monkeypatch.setattr(platform_lanes, "try_acquire_platform", lambda p: False)
    worker["set_error"]("never reached")

    worker["run_once"](1, url="https://www.tiktok.com/@a/video/1", _lane_try=0, _prior_retries=1)

    assert worker["calls"]["extract"] == 0
    day, codes = _outcomes(worker["rc"])
    assert day == {} and codes == {}, "a deferral ran nothing and must record nothing"
    (sent,) = worker["calls"]["dispatched"]
    assert sent["kwargs"] == {"_lane_try": 1, "_prior_retries": 2,
                              "_requester": None, "_quota_precounted": False}
    # the deferred run's lease is released, so the re-dispatched task (a new
    # task id) is not skipped as a "duplicate" of it
    assert job_lease.get_lease_holder("job-1") is None


def test_redispatched_task_with_spent_retries_does_not_retry_again(worker):
    worker["set_error"]("weird upstream answer nobody classified")
    assert worker["run_once"](0, url="https://www.tiktok.com/@a/video/1", _prior_retries=2) is False
    assert worker["calls"]["extract"] == 1
    day, _ = _outcomes(worker["rc"])
    assert day == {"tiktok": {"ok": 0, "err": 1}}


def test_channel_scrape_for_douyin_without_cookie_creates_no_jobs(monkeypatch, rc):
    import app.tasks.video_tasks as vt
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("DOUYIN_COOKIES_B64", raising=False)
    db = _JobsDB({"id": "chan-1", "status": "processing"})
    monkeypatch.setattr(vt, "get_supabase_client", lambda: db)
    scraped = MagicMock()
    monkeypatch.setattr(vt, "scrape_channel_entries_sync", scraped)
    vt.scrape_channel_task.run("https://www.douyin.com/user/MS4wLjABAAAA", "b1", "chan-1", 50)
    scraped.assert_not_called()
    assert db.row["status"] == "failed"
    assert db.row["error_message"] == DOUYIN_COOKIE_REQUIRED_MSG


def test_channel_scrape_stops_when_wave_scheduler_disables_the_platform(monkeypatch, rc):
    """Task #6062: the disabled branch used `supabase` before assignment; the
    NameError was swallowed and the channel was scanned anyway."""
    import app.tasks.video_tasks as vt
    from types import SimpleNamespace
    db = _JobsDB({"id": "chan-2", "status": "processing"})
    monkeypatch.setattr(vt, "get_supabase_client", lambda: db)
    monkeypatch.setattr("app.core.wave_scheduler.get_wave_params",
                        lambda p: SimpleNamespace(wave_size=0, wave_delay=0, mode="disabled", reason="circuit_open"))
    scraped = MagicMock()
    monkeypatch.setattr(vt, "scrape_channel_entries_sync", scraped)
    vt.scrape_channel_task.run("https://www.tiktok.com/@someone", "b2", "chan-2", 20)
    scraped.assert_not_called()
    assert db.row["status"] == "failed"
    assert "tiktok tạm thời không khả dụng" in db.row["error_message"]


# ── 3. Bulk / single Douyin without any cookie: reject before creating jobs ──

DOUYIN = "https://www.douyin.com/video/7000000000000000001"


@pytest.fixture
def no_douyin_cookie(monkeypatch, rc):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("DOUYIN_COOKIES_B64", raising=False)
    return rc


def _bulk(app, urls, **extra):
    return app.post("/api/v1/bulk-download", json={"urls": urls, **extra},
                    headers={"X-Forwarded-For": "198.51.100.9"})


def test_bulk_douyin_without_cookie_is_rejected_and_creates_nothing(app, route, no_douyin_cookie, monkeypatch):
    dispatched = []
    monkeypatch.setattr(route.process_video_task, "apply_async", lambda *a, **k: dispatched.append(1))
    monkeypatch.setattr(route.scrape_channel_task, "apply_async", lambda *a, **k: dispatched.append(1))
    for body in ({"urls": [DOUYIN] * 3},
                 {"urls": ["https://www.douyin.com/user/MS4wLjABAAAA"], "channel_mode": True},
                 {"urls": [DOUYIN, "https://www.tiktok.com/@a/video/1"]}):
        r = _bulk(app, **body)
        assert r.status_code == 422, r.text
        assert r.json()["error_code"] == "cookie_required"
        assert isinstance(r.json()["detail"], str) and "Douyin" in r.json()["detail"]
    assert route._test_sb.inserts == []
    assert dispatched == []


def test_bulk_douyin_with_a_pool_cookie_is_accepted(app, route, no_douyin_cookie, monkeypatch):
    import base64
    no_douyin_cookie.rpush("cookie_pool:douyin", base64.b64encode(b"# Netscape\n").decode())
    monkeypatch.setattr(route.process_video_task, "apply_async", lambda *a, **k: None)
    monkeypatch.setattr(route, "check_platform_quota", lambda *a, **k: {"allowed": True})
    monkeypatch.setattr(route, "record_platform_download", lambda *a, **k: False)
    r = _bulk(app, [DOUYIN])
    assert r.status_code != 422, r.text


def test_pool_precheck_has_no_side_effects(no_douyin_cookie):
    """The bulk pre-check must not put the cookie in cooldown like a real pick."""
    import base64
    from app.core.cookie_pool import has_selectable_cookie
    rc = no_douyin_cookie
    assert has_selectable_cookie("douyin") is False
    rc.rpush("cookie_pool:douyin", base64.b64encode(b"x").decode())
    before = set(rc.keys("*"))
    assert has_selectable_cookie("douyin") is True
    assert set(rc.keys("*")) == before


def test_gate_fails_open_when_redis_is_down(monkeypatch):
    from app.services import douyin_extractor
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("DOUYIN_COOKIES_B64", raising=False)

    def _down(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr("app.core.cookie_pool.has_selectable_cookie", _down)
    assert douyin_extractor.douyin_server_access_available() is True


def test_single_fetch_link_douyin_without_cookie_answers_at_once(app, route, no_douyin_cookie, sync_outcomes, monkeypatch):
    called = []

    async def _never(*a, **k):
        called.append(1)
        raise AssertionError("extractor must not run")
    monkeypatch.setattr(route, "extract_video_info", _never)
    r = app.post("/api/v1/fetch-link", json={"url": DOUYIN, "quality": "video"},
                 headers={"X-Forwarded-For": "198.51.100.10"})
    assert r.status_code == 422
    assert r.json()["error_code"] == "cookie_required"
    assert r.json()["detail"] == DOUYIN_COOKIE_REQUIRED_MSG
    assert called == []
    assert sync_outcomes == [("douyin", False, "cookie_required")]


def test_single_fetch_link_with_user_cookie_is_not_gated(no_douyin_cookie):
    from app.api.routes import _douyin_cookie_gate
    assert _douyin_cookie_gate([DOUYIN], user_has_cookies=True, single=True) is None
    assert _douyin_cookie_gate(["https://www.tiktok.com/@a/video/1"]) is None
    assert _douyin_cookie_gate([DOUYIN]) is not None


def test_container_queue_douyin_without_cookie_is_rejected(no_douyin_cookie, monkeypatch):
    from fastapi import HTTPException
    from app.api import container
    meta = MagicMock(platform="douyin", status="ready")
    monkeypatch.setattr(container, "_get_container_or_404", lambda cid: ("u", meta))
    created = MagicMock()
    monkeypatch.setattr(container, "_create_bulk_jobs", created)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(container.queue_container("cid", container.QueueRequest(queue_mode="all"), MagicMock()))
    assert ei.value.status_code == 422
    assert ei.value.detail["error_code"] == "cookie_required"
    created.assert_not_called()


# ── 4. "other" platform broken down by domain ───────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://www.bilibili.com/video/BV1xx", "bilibili.com"),
    ("https://m.bilibili.com/video/BV1xx", "bilibili.com"),
    ("https://player.vimeo.com/video/1", "vimeo.com"),
    ("dailymotion.com/video/x8", "dailymotion.com"),
    ("https://news.bbc.co.uk/a", "bbc.co.uk"),
    ("https://vnexpress.net/x", "vnexpress.net"),
    ("https://video.zing.com.vn/x?y=1", "zing.com.vn"),
    ("https://user:pw@Example.COM:8443/p", "example.com"),
    ("https://203.0.113.5/v.mp4", do.OTHER_DOMAIN_UNKNOWN),
    ("not a url", do.OTHER_DOMAIN_UNKNOWN),
    ("", do.OTHER_DOMAIN_UNKNOWN),
    (None, do.OTHER_DOMAIN_UNKNOWN),
])
def test_registrable_domain(url, expected):
    assert do.registrable_domain(url) == expected


def test_other_outcomes_are_counted_per_domain(rc, fixed_now):
    do.record("other", True, domain="https://www.bilibili.com/video/1", now=fixed_now)
    do.record("other", False, "processing_failed", domain="https://m.bilibili.com/v/2", now=fixed_now)
    do.record("other", False, "unsupported_url", domain="https://vimeo.com/3", now=fixed_now)
    do.record("tiktok", False, "x", domain="https://www.tiktok.com/@a/video/1", now=fixed_now)
    out = do.other_domains(fixed_now - timedelta(hours=1), fixed_now + timedelta(minutes=1))
    assert out["rows"] == [
        {"domain": "bilibili.com", "ok": 1, "err": 1, "total": 2, "success_rate": 50.0},
        {"domain": "vimeo.com", "ok": 0, "err": 1, "total": 1, "success_rate": 0.0},
    ]
    assert out["distinct"] == 2
    # the platform counters are unchanged by the breakdown
    assert do.read_days([do.day_str(fixed_now)])[do.day_str(fixed_now)]["other"] == {"ok": 1, "err": 2}


def test_domain_cardinality_is_capped_per_day(rc, fixed_now, monkeypatch):
    monkeypatch.setattr(do, "MAX_OTHER_DOMAINS_PER_DAY", 3)
    for i in range(6):
        do.record("other", False, "x", domain=f"https://site{i}.example{i}.org/v", now=fixed_now)
    do.record("other", True, domain="https://site0.example0.org/again", now=fixed_now)
    out = do.other_domains(fixed_now - timedelta(hours=1), fixed_now + timedelta(minutes=1))
    by = {r["domain"]: r for r in out["rows"]}
    assert set(by) == {"example0.org", "example1.org", "example2.org", do.OTHER_DOMAIN_OVERFLOW}
    assert by[do.OTHER_DOMAIN_OVERFLOW]["err"] == 3
    assert by["example0.org"] == {"domain": "example0.org", "ok": 1, "err": 1, "total": 2, "success_rate": 50.0}
    assert rc.scard(do.OTHER_DOMAIN_SEEN_KEY_TPL.format(date=do.day_str(fixed_now))) == 3


def test_route_records_other_domain(app, route, sync_outcomes, monkeypatch):
    async def _bad(url, *a, **k):
        raise ValueError("Không thể trích xuất thông tin video.")
    monkeypatch.setattr(route, "extract_video_info", _bad)
    app.post("/api/v1/fetch-link", json={"url": "https://www.dailymotion.com/video/x8abc", "quality": "video"},
             headers={"X-Forwarded-For": "198.51.100.11"})
    now = do._now()
    out = do.other_domains(now - timedelta(hours=1), now + timedelta(minutes=1))
    assert [r["domain"] for r in out["rows"]] == ["dailymotion.com"]


def test_admin_endpoints_expose_other_domains(app, rc, admin, fixed_now):
    do.record("other", True, domain="https://vimeo.com/1", now=fixed_now)
    errs = app.get("/api/v1/admin/errors").json()
    assert errs["other_domains_24h"]["rows"][0]["domain"] == "vimeo.com"
    assert "platform_fail_rates" in errs                     # old keys kept
    stats = app.get("/api/v1/admin/platform-stats?days=1").json()
    assert stats["other_domains"]["rows"][0]["domain"] == "vimeo.com"
    assert "totals" in stats and "daily" in stats


# ── 5. Funnel: fetch_failed grouped by platform and error_code ──────────────

def test_funnel_groups_fetch_failed_by_platform_and_error_code():
    from app.api.admin import get_funnel

    def ev(anon, plat, code=None):
        props = {"platform": plat}
        if code:
            props["error_code"] = code
        return {"event_name": "fetch_failed", "user_id": None, "anonymous_id": anon,
                "properties": props, "created_at": "2026-10-05T00:00:00+00:00"}

    rows = [ev("a", "douyin", "cookie_required"), ev("a", "douyin", "cookie_required"),
            ev("b", "douyin", "cookie_required"), ev("c", "tiktok", "video_unavailable"),
            ev("d", "other")]
    sb = MagicMock()
    (sb.table.return_value.select.return_value.gte.return_value.in_.return_value
       .limit.return_value.execute.return_value.data) = rows
    with patch("app.api.admin.get_supabase_client", return_value=sb):
        r = asyncio.run(get_funnel(days=7, _=None))
    assert r["fetch_failed_breakdown"] == [
        {"platform": "douyin", "error_code": "cookie_required", "users": 2, "events": 3},
        {"platform": "other", "error_code": "unknown", "users": 1, "events": 1},
        {"platform": "tiktok", "error_code": "video_unavailable", "users": 1, "events": 1},
    ]
    assert "by_platform" in r                                  # old key kept


def test_web_client_sends_error_code_with_fetch_failed():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "components"
           / "DashboardContent.jsx").read_text(encoding="utf-8")
    block = src[src.index("trackEvent(EVENT.FETCH_FAILED"):][:400]
    assert "error_code:" in block and "platform:" in block
