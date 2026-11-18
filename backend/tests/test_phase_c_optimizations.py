"""
Phase C Optimizations Test Suite (C1–C8)
=========================================
Tests all 8 items of Phase C ("Tối ưu nhóm đang giới hạn") with mocks and fakeredis:
  C1. YouTube lane ordering & proxy cost metric per successful download
  C2. Cookie health (`get_expiring_soon_cookies`) & Self-Healing Cobalt reroute for IG/X
  C3. Prefer user's own session via Desktop/Extension (`/api/v1/client/config`, `/api/v1/client/lane-policy`)
  C4. Kuaishou hardening (mobile share normalization, Apollo/INIT_STATE resilience) & 7d promotion check
  C5. Facebook channel/profile >50 videos cursor/offset pagination (up to 200 across pages)
  C6. Xiaohongshu CN proxy pool (`XHS_CN_PROXY_URL`) + visitor `web_session` cookie fallback
  C7. iQIYI PhantomJS + Deno check in Dockerfile & Admin Health / Ops endpoints
  C8. Top 30 generic yt-dlp domains tracking & admin endpoints
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.admin import verify_admin
from app.main import app as fastapi_app

client = TestClient(fastapi_app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Inject a fresh fakeredis instance for every test."""
    import app.core.redis_client as rc
    import app.core.download_outcomes as do

    server = fakeredis.FakeServer()
    fake = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    monkeypatch.setattr(rc, "_client", fake, raising=False)
    monkeypatch.setattr(rc, "get_redis", lambda: fake)
    monkeypatch.setattr(do, "_redis", lambda: fake)
    monkeypatch.setattr("app.core.youtube_gate.get_redis", lambda: fake, raising=False)
    fake.flushall()
    yield fake
    fake.flushall()


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    from app.main import limiter
    prev = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = prev


@pytest.fixture
def as_admin():
    fastapi_app.dependency_overrides[verify_admin] = lambda: {"id": "admin-1", "role": "admin"}
    yield
    fastapi_app.dependency_overrides.pop(verify_admin, None)


# ═══════════════════════════════════════════════════════════════════════════════
# C1. YouTube lane ordering & proxy cost metric
# ═══════════════════════════════════════════════════════════════════════════════

class TestC1YouTubeLaneAndProxyCost:
    def test_lane_order_prefers_user_device_then_po_token_then_proxy(self):
        from app.core import youtube_gate as ytg

        assert ytg.get_youtube_lane_order() == [
            "user_device",
            "po_token_cookie",
            "residential_proxy",
        ]
        assert ytg.get_youtube_lane_order(has_user_device_hint=False) == [
            "po_token_cookie",
            "residential_proxy",
        ]

    def test_lane_order_drops_proxy_when_circuit_open_or_over_limit(self):
        from app.core import youtube_gate as ytg

        for _ in range(ytg.CB_FAIL_THRESHOLD):
            ytg.record_proxy_failure()
        assert "residential_proxy" not in ytg.get_youtube_lane_order()

        ytg.record_proxy_success()
        assert "residential_proxy" in ytg.get_youtube_lane_order()

        ytg.add_proxy_megabytes(ytg.DAILY_LIMIT_GB * 1024 + 10)
        assert "residential_proxy" not in ytg.get_youtube_lane_order()

    def test_lane_stats_and_cost_per_successful_download(self):
        from app.core import metrics, youtube_gate as ytg

        # 2 user_device downloads (free), 1 po_token_cookie download (free), 1 proxy download (512 MB = 0.5 GB)
        ytg.record_result(True, file_size_mb=120, lane="user_device")
        ytg.record_result(True, file_size_mb=90, lane="user_device")
        ytg.record_result(True, file_size_mb=80, lane="po_token_cookie")
        ytg.record_result(True, file_size_mb=512, lane="residential_proxy")
        ytg.record_result(False, lane="po_token_cookie")

        stats = ytg.get_youtube_lane_stats()
        assert stats["total_success"] == 4
        assert stats["total_fail"] == 1
        assert stats["lanes"]["user_device"]["success"] == 2
        assert stats["lanes"]["po_token_cookie"] == {"success": 1, "fail": 1}
        assert stats["lanes"]["residential_proxy"]["success"] == 1

        # Only the 512 MB residential_proxy download bills proxy bytes (0.5 GB * $1.50 = $0.75)
        expected_cost = 0.5 * ytg.PROXY_COST_PER_GB
        assert stats["proxy_gb"] == pytest.approx(0.5, abs=1e-3)
        assert stats["proxy_cost_usd"] == pytest.approx(expected_cost, abs=1e-3)
        assert stats["avg_proxy_cost_per_success_usd"] == pytest.approx(expected_cost / 4, abs=1e-4)
        assert stats["avg_proxy_cost_per_proxy_download_usd"] == pytest.approx(expected_cost, abs=1e-4)
        assert stats["proxy_share_pct"] == 25.0

        # Also exposed via metrics.get_youtube_lane_stats()
        m_stats = metrics.get_youtube_lane_stats()
        assert m_stats["total_success"] == 4
        assert m_stats["proxy_cost_usd"] == stats["proxy_cost_usd"]


# ═══════════════════════════════════════════════════════════════════════════════
# C2. Cookie health & proactive refresh alerts + Self-Healing Cobalt reroute
# ═══════════════════════════════════════════════════════════════════════════════

def _make_netscape_cookie(domain: str, name: str, value: str, expires_at: int) -> str:
    text = f"# Netscape HTTP Cookie File\n{domain}\tTRUE\t/\tTRUE\t{expires_at}\t{name}\t{value}\n"
    return base64.b64encode(text.encode("utf-8")).decode("utf-8")


class TestC2CookieHealthAndSelfHealing:
    def test_get_expiring_soon_cookies_within_48h(self):
        from app.core import cookie_pool as cp

        now = int(time.time())
        # Expiring in 12h (should be returned)
        c_ig = _make_netscape_cookie(".instagram.com", "sessionid", "ig_soon", now + 12 * 3600)
        # Expiring in 36h (should be returned)
        c_tw = _make_netscape_cookie(".x.com", "auth_token", "tw_soon", now + 36 * 3600)
        # Expiring in 20 days (healthy, should NOT be returned for within_hours=48)
        c_bili = _make_netscape_cookie(".bilibili.com", "SESSDATA", "bili_ok", now + 20 * 86400)

        cp.add_cookie("instagram", c_ig, label="ig-1")
        cp.add_cookie("twitter", c_tw, label="tw-1")
        cp.add_cookie("bilibili", c_bili, label="bili-1")

        expiring = cp.get_expiring_soon_cookies(within_hours=48)
        platforms_found = [e["platform"] for e in expiring]
        assert "instagram" in platforms_found
        assert "twitter" in platforms_found
        assert "bilibili" not in platforms_found

        ig_entry = next(e for e in expiring if e["platform"] == "instagram")
        assert 11.0 <= ig_entry["hours_left"] <= 12.5
        assert ig_entry["is_expired"] is False
        assert ig_entry["label"] == "ig-1"

        # Filter by single platform
        only_tw = cp.get_expiring_soon_cookies(within_hours=48, platform="twitter")
        assert len(only_tw) == 1 and only_tw[0]["platform"] == "twitter"

    def test_self_healing_reroutes_instagram_and_twitter_to_cobalt_on_cookie_or_rate_errors(self, monkeypatch):
        from app.core import platform_health as ph
        from app.services import self_healing as sh

        monkeypatch.setattr(sh, "TELEGRAM_ENABLED", False)
        monkeypatch.setattr(sh, "_diagnose_with_ai", lambda *a, **k: None)

        hm = sh.HealthMonitor()
        # 5 successes + 3 cookie_expired errors = 3/8 = 37.5% (> 30% threshold, >= 5 min_samples)
        for _ in range(5):
            hm.record_success("instagram")
        diag = None
        for i in range(3):
            res = hm.record_error(
                "instagram",
                "cookie_expired",
                "Instagram session expired",
                url_hash=f"h{i}",
                async_diagnose=False,
            )
            diag = res or diag

        assert diag is not None
        assert diag["should_reroute"] is True
        assert diag["suggested_route"] == "cobalt"
        assert diag["rerouted_to"] == "cobalt"
        assert hm.get_active_reroute("instagram") == "cobalt"
        assert ph.prefer_cobalt("instagram") is True

        # Same for twitter on rate_limited even if AI suggested browser_direct
        monkeypatch.setattr(
            sh,
            "_diagnose_with_ai",
            lambda *a, **k: {
                "source": "ai",
                "root_cause": "rate limit",
                "severity": "high",
                "should_reroute": False,
                "suggested_route": "browser_direct",
            },
        )
        for _ in range(4):
            hm.record_success("twitter")
        diag_tw = None
        for i in range(3):
            res_tw = hm.record_error(
                "twitter",
                "rate_limited",
                "HTTP 429 Too Many Requests",
                url_hash=f"tw{i}",
                http_status=429,
                async_diagnose=False,
            )
            diag_tw = res_tw or diag_tw
        assert diag_tw is not None
        assert diag_tw["rerouted_to"] == "cobalt"
        assert hm.get_active_reroute("twitter") == "cobalt"
        assert ph.prefer_cobalt("twitter") is True


# ═══════════════════════════════════════════════════════════════════════════════
# C3. Prefer user's own session via Desktop / Extension
# ═══════════════════════════════════════════════════════════════════════════════

class TestC3PreferUserLaneConfig:
    EXPECTED_PLATFORMS = [
        "youtube", "instagram", "twitter", "facebook",
        "tiktok", "douyin", "bilibili", "xiaohongshu",
    ]

    def test_client_config_and_lane_policy_endpoints(self, monkeypatch):
        monkeypatch.delenv("CLIENT_PREFER_USER_LANE_PLATFORMS", raising=False)
        for path in ("/api/v1/client/config", "/api/v1/client/lane-policy"):
            r = client.get(path)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["preferUserLanePlatforms"] == self.EXPECTED_PLATFORMS
            assert body["youtubeLaneOrder"][0] == "user_device"

    def test_prefer_user_lane_env_override(self, monkeypatch):
        monkeypatch.setenv("CLIENT_PREFER_USER_LANE_PLATFORMS", "instagram, youtube, unknown_plat")
        r = client.get("/api/v1/client/config")
        assert r.status_code == 200
        assert r.json()["preferUserLanePlatforms"] == ["youtube", "instagram"]

        monkeypatch.setenv("CLIENT_PREFER_USER_LANE_PLATFORMS", "none")
        assert client.get("/api/v1/client/lane-policy").json()["preferUserLanePlatforms"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# C4. Kuaishou hardening & 7-day rolling auto-promotion check
# ═══════════════════════════════════════════════════════════════════════════════

class TestC4KuaishouHardeningAndPromotion:
    def test_mobile_share_url_normalization(self):
        from app.services import kuaishou_extractor as ks

        # Share copy-text with embedded short link and trailing Chinese punctuation
        share_text = "快手作品分享 https://v.kuaishou.com/AbCd12，复制此消息打开【快手】查看！"
        assert ks.normalize_kuaishou_share_url(share_text) == "https://v.kuaishou.com/AbCd12"

        # Mobile /fw/next-photo/<id> normalized to /fw/photo/<id>
        next_url = "https://v.m.chenzhongtech.com/fw/next-photo/3xvsedz34m82ppi?fid=123"
        norm = ks.normalize_kuaishou_share_url(next_url)
        assert norm is not None and "/fw/photo/3xvsedz34m82ppi" in norm

        # Mobile /fw/photo?photoId=<id> normalized to /fw/photo/<id>
        q_url = "https://m.gifshow.com/fw/photo?photoId=3xvsedz34m82ppi&cc=share_copylink"
        norm_q = ks.normalize_kuaishou_share_url(q_url)
        assert norm_q is not None and "/fw/photo/3xvsedz34m82ppi" in norm_q

    def test_apollo_inline_vision_video_detail_and_video_resource_resilience(self):
        from app.services import kuaishou_extractor as ks

        # Case 1: Inline visionVideoDetail.photo without VisionVideoDetailPhoto:* keys
        inline_state = {
            "defaultClient": {
                "visionVideoDetail": {
                    "photo": {
                        "id": "3xinline01",
                        "caption": "Inline video",
                        "coverUrl": "https://cdn-img.example-ks.net/c.jpg",
                        "duration": 12000,
                        "photoUrl": "https://cdn-media.example-ks.net/inline.mp4",
                    },
                    "author": {"name": "InlineAuthor"},
                }
            }
        }
        html1 = f"<script>window.__APOLLO_STATE__={json.dumps(inline_state)};</script>"
        o1 = ks.strategy_apollo_state(html1, "3xinline01")
        assert o1.ok and o1.post.video_urls == ["https://cdn-media.example-ks.net/inline.mp4"]
        assert o1.post.author == "InlineAuthor"

        # Case 2: VisionVideoDetailPhoto node with videoResource fallback when photoUrl is empty
        vr_state = {
            "defaultClient": {
                "VisionVideoDetailPhoto:3xvr01": {
                    "id": "3xvr01",
                    "caption": "VR fallback",
                    "videoResource": {
                        "h264": {
                            "adaptationSet": [
                                {"representation": [{"url": "https://cdn-media.example-ks.net/vr.mp4"}]}
                            ]
                        }
                    },
                }
            }
        }
        html2 = f"<script>window.__APOLLO_STATE__={json.dumps(vr_state)};</script>"
        o2 = ks.strategy_apollo_state(html2, "3xvr01")
        assert o2.ok and o2.post.video_urls == ["https://cdn-media.example-ks.net/vr.mp4"]

    def test_init_state_h265_and_mp4url_resilience(self):
        from app.services import kuaishou_extractor as ks

        photo = {
            "photoId": "3xh265",
            "photoType": "VIDEO",
            "caption": "H265 video",
            "h265Urls": [{"backupUrl": "https://cdn-media.example-ks.net/h265_backup.mp4"}],
        }
        html = "<script>window.INIT_STATE = " + json.dumps({"k": {"photo": photo}}) + "</script>"
        o = ks.strategy_init_state(html, "3xh265")
        assert o.ok and o.post.video_urls == ["https://cdn-media.example-ks.net/h265_backup.mp4"]

    def test_check_experimental_promotion_7d_rolling(self):
        import app.core.download_outcomes as do
        from app.core import platform_health as ph

        now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)

        # 1. Insufficient samples (< 20)
        for _ in range(10):
            do.record("kuaishou", True, now=now)
        res1 = ph.check_experimental_promotion("kuaishou", now=now)
        assert res1["total"] == 10
        assert res1["ready_for_promotion"] is False
        assert res1["reason"] == "insufficient_samples"
        assert res1["recommended_level"] == "experimental"

        # 2. Enough samples (25 across 7 days) and > 90% success rate (24 ok, 1 err = 96%)
        for d in range(1, 4):
            day_ts = now - timedelta(days=d)
            for _ in range(4):
                do.record("kuaishou", True, now=day_ts)
        for _ in range(2):
            do.record("kuaishou", True, now=now)
        do.record("kuaishou", False, "kuaishou_parse_failed", now=now)

        res2 = ph.check_experimental_promotion("kuaishou", now=now)
        assert res2["total"] == 25
        assert res2["ok"] == 24 and res2["err"] == 1
        assert res2["success_rate_pct"] == 96.0
        assert res2["ready_for_promotion"] is True
        assert res2["recommended_level"] == "full"
        assert res2["reason"] == "ready"

        # 3. Drop below 90% threshold (add 5 errors -> 24/30 = 80%)
        for _ in range(5):
            do.record("kuaishou", False, "kuaishou_geo_blocked", now=now)
        res3 = ph.check_experimental_promotion("kuaishou", now=now)
        assert res3["total"] == 30
        assert res3["ready_for_promotion"] is False
        assert res3["reason"] == "below_threshold"


# ═══════════════════════════════════════════════════════════════════════════════
# C5. Facebook channel/profile >50 videos pagination
# ═══════════════════════════════════════════════════════════════════════════════

class TestC5FacebookPagination:
    def _fake_entries(self, count: int = 180):
        return [
            {
                "url": f"https://www.facebook.com/page/videos/{1000 + i}",
                "title": f"FB Video #{i + 1}",
                "uploader": "TestPage",
                "duration": 60,
                "view_count": 100 * (i + 1),
                "upload_date": "20261015",
            }
            for i in range(count)
        ]

    def test_discover_and_cursor_pagination_across_pages_up_to_200(self, monkeypatch):
        import yt_dlp
        from app.services.container_expanders.facebook_expander import FacebookExpander

        all_entries = self._fake_entries(220)
        seen_opts = []

        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts
                seen_opts.append(dict(opts))

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                start = int(self.opts.get("playliststart", 1)) - 1
                end = int(self.opts.get("playlistend", 50))
                return {"title": "Test FB Page", "entries": all_entries[start:end]}

        monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
        exp = FacebookExpander()

        # Initial discover fetches first 50 and provides next_cursor="50"
        meta = exp.discover("https://www.facebook.com/TestPage", max_items=50)
        assert meta.status == "ready"
        assert len(meta.sections[0].items) == 50
        assert meta.sections[0].has_more is True
        assert meta.sections[0].next_cursor == "50"

        # Page 2: cursor="50", limit=50 -> items 51..100, next_cursor="100"
        p2 = exp.expand_page("https://www.facebook.com/TestPage", cursor="50", limit=50)
        assert len(p2["items"]) == 50
        assert p2["items"][0].id == "fb:1050"
        assert p2["has_more"] is True
        assert p2["next_cursor"] == "100"
        assert seen_opts[-1]["playliststart"] == 51
        assert seen_opts[-1]["playlistend"] == 100

        # Page 3: cursor="100", limit=100 -> items 101..200, hits 200 cap so has_more=False
        p3 = exp.expand_page("https://www.facebook.com/TestPage", cursor="100", limit=100)
        assert len(p3["items"]) == 100
        assert p3["items"][-1].id == "fb:1199"
        assert p3["has_more"] is False
        assert p3["next_cursor"] == ""

        # expand_section with cursor="offset:150" returns last 50 items (151..200)
        sec_items = exp.expand_section("https://www.facebook.com/TestPage", "videos", cursor="offset:150", limit=80)
        assert len(sec_items) == 50
        assert sec_items[0].id == "fb:1150"


# ═══════════════════════════════════════════════════════════════════════════════
# C6. Xiaohongshu CN proxy pool + visitor cookie fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestC6XiaohongshuProxyAndVisitorCookie:
    def test_cn_proxy_selection_priority(self, monkeypatch):
        from app.core import proxy_pool
        from app.services import xiaohongshu_extractor as xhs

        monkeypatch.delenv("XHS_CN_PROXY_URL", raising=False)
        monkeypatch.delenv("XIAOHONGSHU_PROXY_CN", raising=False)

        # 1. Pool fallback when env vars are unset
        proxy_pool.add_proxy("xiaohongshu", "http://u:p@pool-xhs.example.com:8080")
        assert xhs._get_xhs_cn_proxy() == "http://u:p@pool-xhs.example.com:8080"

        # 2. XIAOHONGSHU_PROXY_CN overrides pool
        monkeypatch.setenv("XIAOHONGSHU_PROXY_CN", "http://u:p@legacy-cn.example.com:8080")
        assert xhs._get_xhs_cn_proxy() == "http://u:p@legacy-cn.example.com:8080"

        # 3. XHS_CN_PROXY_URL has highest priority
        monkeypatch.setenv("XHS_CN_PROXY_URL", "http://u:p@primary-cn.example.com:8080")
        assert xhs._get_xhs_cn_proxy() == "http://u:p@primary-cn.example.com:8080"

    def test_visitor_cookie_fallback_when_pool_is_empty(self, monkeypatch):
        from app.services import xiaohongshu_extractor as xhs

        monkeypatch.setattr(xhs, "_try_ytdlp", lambda u: None)
        monkeypatch.setenv("XHS_CN_PROXY_URL", "http://cn-proxy.local:8888")

        captured = {"cookies": None, "proxy": None}

        class FakeClient:
            def __init__(self, **kw):
                captured["proxy"] = kw.get("proxy") or (kw.get("proxies") or {}).get("all://")
                self.headers = kw.get("headers") or {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, **kw):
                resp = MagicMock()
                resp.status_code = 200
                resp.cookies = {"web_session": "visitor_live_session_123"}
                return resp

            def post(self, url, json=None, **kw):
                captured["cookies"] = self.headers.get("Cookie", "")
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "note_card": {
                                    "type": "video",
                                    "title": "XHS Visitor Video",
                                    "user": {"nickname": "RedAuthor"},
                                    "video": {
                                        "duration": 18,
                                        "image": {"url_default": "https://ci.xiaohongshu.com/cover.jpg"},
                                        "media": {
                                            "stream": {
                                                "h264": [
                                                    {
                                                        "master_url": "https://sns-video-bd.xhscdn.com/v1.mp4",
                                                        "average_bitrate": 1500,
                                                        "video_codec": {"height": 1080},
                                                    }
                                                ]
                                            }
                                        },
                                    },
                                }
                            }
                        ]
                    },
                }
                return resp

        monkeypatch.setattr(xhs.httpx, "Client", FakeClient)

        res = xhs.extract_xiaohongshu("https://www.xiaohongshu.com/explore/64ab1234567890abcdef1234")
        assert res["error_code"] is None
        assert res["title"] == "XHS Visitor Video"
        assert res["direct_url"] == "https://sns-video-bd.xhscdn.com/v1.mp4"
        assert captured["proxy"] == "http://cn-proxy.local:8888"
        assert "web_session=visitor_live_session_123" in captured["cookies"]
        assert "a1=" in captured["cookies"] and "webId=" in captured["cookies"]


# ═══════════════════════════════════════════════════════════════════════════════
# C7. iQIYI PhantomJS & Deno check in Dockerfile & Admin Health / Ops
# ═══════════════════════════════════════════════════════════════════════════════

class TestC7JsRuntimesHealthAndDockerfile:
    def test_dockerfile_contains_phantomjs_and_deno(self):
        dockerfile_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "Dockerfile",
        )
        content = open(dockerfile_path, encoding="utf-8").read()
        assert "PHANTOMJS_VERSION=2.1.1" in content
        assert "/usr/local/bin/phantomjs" in content
        assert "/usr/local/bin/deno" in content

    def test_all_js_runtimes_status_and_admin_endpoints(self, monkeypatch, as_admin):
        from app.core import js_runtimes
        from app.core import ytdlp_channel as yc

        def fake_which(name):
            if name == "deno":
                return "/usr/local/bin/deno"
            if name == "phantomjs":
                return "/usr/local/bin/phantomjs"
            return None

        def fake_run(cmd, *a, **k):
            if "deno" in cmd[0]:
                return SimpleNamespace(returncode=0, stdout="deno 2.4.1 (stable, release, x86_64-unknown-linux-gnu)\n")
            return SimpleNamespace(returncode=0, stdout="2.1.1\n")

        monkeypatch.setattr(js_runtimes.shutil, "which", fake_which)
        monkeypatch.setattr(js_runtimes.subprocess, "run", fake_run)
        monkeypatch.setattr(yc, "status", lambda: {"active": "2026.08.19"})

        st = js_runtimes.all_js_runtimes_status(refresh=True)
        assert st["healthy"] is True
        assert st["iqiyi_ready"] is True
        assert st["youtube_ejs_ready"] is True
        assert st["deno"] == {"found": True, "path": "/usr/local/bin/deno", "version": "2.4.1"}
        assert st["phantomjs"] == {"found": True, "path": "/usr/local/bin/phantomjs", "version": "2.1.1"}
        assert st["warnings"] == []

        # Dedicated endpoint
        r1 = client.get("/api/v1/admin/platform-health/js-runtimes")
        assert r1.status_code == 200
        assert r1.json()["iqiyi_ready"] is True

        # Included in /api/v1/admin/platform-health
        r2 = client.get("/api/v1/admin/platform-health")
        assert r2.status_code == 200
        assert r2.json()["js_runtimes"]["phantomjs"]["version"] == "2.1.1"

        # Included in /api/v1/admin/ops-signals
        r3 = client.get("/api/v1/admin/ops-signals")
        assert r3.status_code == 200
        assert r3.json()["js_runtimes"]["deno"]["version"] == "2.4.1"

        js_runtimes._cache = None
        js_runtimes._deno_cache = None


# ═══════════════════════════════════════════════════════════════════════════════
# C8. Track top 30 generic (yt-dlp fallback) domains
# ═══════════════════════════════════════════════════════════════════════════════

class TestC8TopGenericDomains:
    def test_record_and_get_top_generic_domains_limit_30(self, as_admin):
        from app.core import platform_health as ph

        # Record 35 distinct domains with varying volume & success rates
        for i in range(35):
            dom_url = f"https://www.site-{i:02d}.org/video/123"
            for _ in range(i + 1):
                ph.record_generic_domain(dom_url, ok=(i % 3 != 0))

        # Add extra failures on top domain site-34.org
        ph.record_generic_domain("https://site-34.org/watch/99", ok=False)
        ph.record_generic_domain("not-a-valid-host", ok=True)  # ignored safely

        top = ph.get_top_generic_domains(limit=30)
        assert len(top) == 30
        assert top[0]["domain"] == "site-34.org"
        assert top[0]["total"] == 36 and top[0]["total"] >= top[1]["total"]

        # Test distinct registrable domains
        ph._r().delete(ph.GENERIC_DOMAINS_KEY)
        for _ in range(10):
            ph.record_generic_domain("https://www.streamable.com/abc", ok=True)
        for _ in range(4):
            ph.record_generic_domain("https://clips.streamable.com/def", ok=False)
        for _ in range(6):
            ph.record_generic_domain("https://v.redd.it/xyz", ok=True)

        top2 = ph.get_top_generic_domains(limit=30)
        assert len(top2) == 2
        assert top2[0]["domain"] == "streamable.com"
        assert top2[0]["ok"] == 10 and top2[0]["err"] == 4 and top2[0]["total"] == 14
        assert top2[0]["success_rate"] == pytest.approx(71.4, abs=0.1)
        assert top2[1]["domain"] == "redd.it"
        assert top2[1]["total"] == 6 and top2[1]["success_rate"] == 100.0

        # Exposed on /api/v1/admin/self-healing/generic-domains
        r = client.get("/api/v1/admin/self-healing/generic-domains?limit=30")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 2
        assert body["domains"][0]["domain"] == "streamable.com"

        # Also exposed on /api/v1/admin/platform-health/generic-domains
        r2 = client.get("/api/v1/admin/platform-health/generic-domains?limit=1")
        assert r2.status_code == 200
        assert len(r2.json()["domains"]) == 1
