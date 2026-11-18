"""
Tests for Phase B2 (Weibo) & Phase B3 (Xigua / Toutiao) Extractors
==================================================================
All HTTP and yt-dlp calls are mocked; no live network requests are made.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.core.source_classifier import classify
from app.services import weibo_extractor as wb
from app.services import xigua_extractor as xg
from app.services.extractor_registry import REGISTRY


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    wb._MEMORY_VISITOR_SUB = ("", 0.0)


# ── B2: Weibo URL Parsing & Extraction ─────────────────────────────────────

def test_weibo_url_parsing_and_registry():
    u1 = "https://weibo.com/1234567890/NabcDef12"
    u2 = "https://weibo.com/tv/show/1034:4912345678901234"
    u3 = "https://m.weibo.cn/status/4912345678901234"
    u4 = "https://video.weibo.com/show?fid=1034:4912345678901234"

    for u in (u1, u2, u3, u4):
        assert wb.is_weibo_url(u)
        ext = REGISTRY.detect(u)
        assert ext.platform_name == "weibo"
        clf = classify(u)
        assert clf.platform == "weibo"
        assert clf.source_type == "single_video"


def test_weibo_mobile_status_extraction_with_visitor_sub():
    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "genvisitor" in url_str:
            return httpx.Response(200, text='window.gen_callback && gen_callback({"retcode":20000000,"data":{"tid":"MOCK_TID_1"}});')
        if "visitor?a=incarnate" in url_str or "a=incarnate" in url_str:
            return httpx.Response(200, text='window.cross_domain && cross_domain({"retcode":20000000,"data":{"sub":"_2AkMR_MOCK_SUB_COOKIE"}});')
        if "m.weibo.cn/statuses/show" in url_str:
            assert "_2AkMR_MOCK_SUB_COOKIE" in (request.headers.get("Cookie") or "")
            return httpx.Response(200, json={
                "ok": 1,
                "data": {
                    "text": "Weibo mobile video post",
                    "user": {"screen_name": "WeiboCreator"},
                    "page_info": {
                        "content2": "Awesome Weibo Clip",
                        "page_pic": {"url": "https://wx1.sinaimg.cn/large/cover1.jpg"},
                        "media_info": {
                            "duration": 42,
                            "mp4_720p_mp4": "https://f.video.weibocdn.com/001/clip_720p.mp4",
                            "mp4_hd_url": "https://f.video.weibocdn.com/001/clip_480p.mp4",
                        },
                    },
                },
            })
        raise AssertionError(f"Unexpected URL: {url_str}")

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    ext = wb.WeiboExtractor()
    res = ext.extract_single("https://m.weibo.cn/status/4912345678901234", client=mock_client)
    assert res.ok
    assert res.platform == "weibo"
    assert res.title == "Awesome Weibo Clip"
    assert res.author == "WeiboCreator"
    assert res.duration_ms == 42000
    assert res.direct_url == "https://f.video.weibocdn.com/001/clip_720p.mp4"
    assert len(res.formats) == 2


def test_weibo_tv_component_extraction():
    wb.store_visitor_sub("_2AkMR_CACHED_SUB")

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        assert "weibo.com/tv/api/component" in url_str
        return httpx.Response(200, json={
            "code": "100000",
            "data": {
                "Component_Play_Playinfo": {
                    "title": "Weibo TV HD Show",
                    "author": "TVHost",
                    "cover_image": "//wx2.sinaimg.cn/large/tv_cover.jpg",
                    "duration": 120,
                    "urls": {
                        "高清 1080P": "//f.video.weibocdn.com/tv/1080p.mp4",
                        "高清 720P": "//f.video.weibocdn.com/tv/720p.mp4",
                    },
                }
            },
        })

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    ext = wb.WeiboExtractor()
    res = ext.extract_single("https://weibo.com/tv/show/1034:4912345678901234", client=mock_client)
    assert res.ok
    assert res.title == "Weibo TV HD Show"
    assert res.author == "TVHost"
    assert res.direct_url == "https://f.video.weibocdn.com/tv/1080p.mp4"
    assert res.thumbnail == "https://wx2.sinaimg.cn/large/tv_cover.jpg"


# ── B3: Xigua / Toutiao URL Parsing & Extraction ───────────────────────────

def test_xigua_toutiao_url_parsing_and_registry():
    u1 = "https://www.ixigua.com/7123456789012345678"
    u2 = "https://www.toutiao.com/video/7123456789012345678/"
    u3 = "https://v.ixigua.com/iAbCdEf/"

    for u in (u1, u2, u3):
        assert xg.is_xigua_url(u)
        ext = REGISTRY.detect(u)
        assert ext.platform_name == "xigua"
        clf = classify(u)
        assert clf.platform == "xigua"
        assert clf.source_type == "single_video"


def test_xigua_mobile_json_fallback():
    b64_1080 = base64.b64encode(b"https://v3-dy.ixigua.com/video/1080p.mp4").decode("ascii")
    b64_720 = base64.b64encode(b"https://v3-dy.ixigua.com/video/720p.mp4").decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        assert "m.toutiao.com/i7123456789012345678/info/" in url_str
        return httpx.Response(200, json={
            "success": True,
            "data": {
                "title": "Toutiao Documentary Clip",
                "detail_source": "XiguaStudio",
                "poster_url": "https://p1.toutiaoimg.com/cover.jpg",
                "video_duration": 95,
                "video_list": {
                    "video_1": {"definition": "720p", "bitrate": 1200, "main_url": b64_720},
                    "video_2": {"definition": "1080p", "bitrate": 2800, "main_url": b64_1080},
                },
            },
        })

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    ext = xg.XiguaExtractor()
    res = ext.extract_single(
        "https://www.toutiao.com/video/7123456789012345678/",
        client=mock_client,
        skip_ytdlp=True,
    )
    assert res.ok
    assert res.platform == "xigua"
    assert res.title == "Toutiao Documentary Clip"
    assert res.author == "XiguaStudio"
    assert res.duration_ms == 95000
    assert res.direct_url == "https://v3-dy.ixigua.com/video/1080p.mp4"
    assert res.extraction_method == "xigua_mobile_json"


def test_xigua_ssr_html_fallback():
    b64_url = base64.b64encode(b"https://v9.ixigua.com/ssr/stream_hd.mp4").decode("ascii")
    ssr_payload = {
        "anyVideo": {
            "gidInformation": {
                "packerData": {
                    "video": {
                        "title": "Xigua SSR Hydrated Video",
                        "user_info": {"name": "SSRAuthor"},
                        "poster_url": "https://p3.toutiaoimg.com/ssr.jpg",
                        "video_duration": 60,
                        "videoResource": {
                            "normal": {
                                "video_list": {
                                    "video_1": {
                                        "definition": "1080p",
                                        "bitrate": 3000,
                                        "main_url": b64_url,
                                    }
                                }
                            }
                        },
                    }
                }
            }
        }
    }
    html = f"<html><body><script>window._SSR_HYDRATED_DATA={json.dumps(ssr_payload)}</script></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "m.toutiao.com" in url_str:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=html)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    ext = xg.XiguaExtractor()
    res = ext.extract_single(
        "https://www.ixigua.com/7123456789012345678",
        client=mock_client,
        skip_ytdlp=True,
    )
    assert res.ok
    assert res.title == "Xigua SSR Hydrated Video"
    assert res.author == "SSRAuthor"
    assert res.direct_url == "https://v9.ixigua.com/ssr/stream_hd.mp4"
    assert res.extraction_method == "xigua_ssr"
