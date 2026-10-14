"""china_proxy_test: masking and parsing (no network)."""
import json

from app.core import china_proxy_test as t


def test_mask_never_shows_credentials():
    assert t.mask("http://user-country-hk-session-1:S3cret@proxy.example.com:5959") == "proxy.example.com:5959"
    assert "S3cret" not in t.mask("socks5://u:S3cret@h.io:1080")
    assert t.mask("h.io:8080") == "h.io:8080"


def test_router_play_url_extracts_no_watermark():
    data = {"loaderData": {"video_(id)/page": {"videoInfoRes": {"item_list": [
        {"video": {"play_addr": {"url_list": ["https://x/aweme/v1/playwm/?video_id=v1"]}}}]}}}}
    html = f"<script>window._ROUTER_DATA = {json.dumps(data)}</script>"
    assert t._router_play_url(html) == "https://x/aweme/v1/play/?video_id=v1"


def test_router_without_video_is_none():
    html = '<script>window._ROUTER_DATA = {"loaderData": {"video_(id)/page": {"ua": "x"}}}</script>'
    assert t._router_play_url(html) is None
    assert t._router_play_url("<html></html>") is None


def test_pool_mapping_for_china_hosts():
    from app.core.proxy_pool import platform_of_url
    assert platform_of_url("https://www.xiaohongshu.com/explore/abc") == "xiaohongshu"
    assert platform_of_url("https://www.kuaishou.com/short-video/abc") == "kuaishou"
    assert platform_of_url("https://www.iesdouyin.com/share/video/1/") == "douyin"
