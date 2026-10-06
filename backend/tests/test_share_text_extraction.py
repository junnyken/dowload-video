"""
Share text (prose around a link) is reduced to the URL on the server, so API
clients (extension, desktop app, bulk) behave like the web UI's paste handler.
"""

import fakeredis
import pytest
from fastapi import HTTPException

from app.core.url_normalizer import extract_share_url

DOUYIN = ("8.94 :1pm anD:/ i@c.nQ 01/09 这是还没正式开拍前的热身开嗓版本 # pokerface  "
          "https://v.douyin.com/l4F4tDYO5oU/ 复制此链接，打开Dou音搜索，直接观看视频！")
TIKTOK = ("Check out this video https://vm.tiktok.com/ZMabc123/ 复制此链接，打开TikTok搜索，直接观看视频！")
XHS = ("68 【标题 - 作者 | 小红书】 😆 abcDEF 😆 http://xhslink.com/a/AbCdEf，复制本条信息，打开【小红书】App查看精彩内容！")
KUAISHOU = ("https://v.kuaishou.com/AbCdEf 一个好看的作品「标题」 复制此消息，打开【快手】直接观看！")


@pytest.mark.parametrize("text,expected", [
    (DOUYIN, "https://v.douyin.com/l4F4tDYO5oU/"),
    (TIKTOK, "https://vm.tiktok.com/ZMabc123/"),
    (XHS, "http://xhslink.com/a/AbCdEf"),
    (KUAISHOU, "https://v.kuaishou.com/AbCdEf"),
    ("see https://example.com/a?b=1&c=2. thanks", "https://example.com/a?b=1&c=2"),
    ("first https://a.example/1 then https://b.example/2", "https://a.example/1"),
    ("link：https://a.example/x！", "https://a.example/x"),
])
def test_extracts_first_url(text, expected):
    assert extract_share_url(text) == expected


@pytest.mark.parametrize("text", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://v.douyin.com/l4F4tDYO5oU/",
    "www.tiktok.com/@a/video/1",                 # scheme-less single token
    "  https://example.com/x  ",                  # padding kept, as before
    "https://example.com/路径/视频?id=1",          # CJK inside a clean URL
])
def test_clean_single_token_passes_through_byte_identical(text):
    assert extract_share_url(text) == text


@pytest.mark.parametrize("text", ["just some words 复制此链接", "8.94 :1pm anD:/ i@c.nQ", "", "   ", None, 5])
def test_no_url_returns_none(text):
    assert extract_share_url(text) is None


# ── endpoints ────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    import app.api.routes as routes
    from app.core import ssrf_guard, disk_guardrail
    monkeypatch.setattr(disk_guardrail, "_DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(disk_guardrail, "check_can_accept_job", lambda *a, **k: (True, ""))
    from fastapi.testclient import TestClient
    import app.main as main_mod
    monkeypatch.setattr("app.core.redis_client._client", fakeredis.FakeRedis(decode_responses=True))
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))
    for lim in {id(x): x for x in (main_mod.limiter, routes.limiter,
                                     getattr(main_mod.app.state, "limiter", None)) if x}.values():
        monkeypatch.setattr(lim, "enabled", False)
    return TestClient(main_mod.app, raise_server_exceptions=False)


@pytest.fixture
def seen(monkeypatch):
    """Capture what reaches the SSRF guard; stop the request right there."""
    import app.api.routes as routes
    calls = []

    def guard(url):
        calls.append(url)
        raise HTTPException(status_code=418, detail="stop")
    monkeypatch.setattr(routes, "_assert_safe_url", guard)
    return calls


def test_fetch_link_guard_sees_extracted_url(client, seen):
    r = client.post("/api/v1/fetch-link", json={"url": DOUYIN})
    assert r.status_code == 418
    assert seen == ["https://v.douyin.com/l4F4tDYO5oU/"]


def test_fetch_link_plain_url_untouched(client, seen):
    client.post("/api/v1/fetch-link", json={"url": "https://www.youtube.com/watch?v=abc"})
    assert seen == ["https://www.youtube.com/watch?v=abc"]


def test_fetch_link_text_without_url_still_400_invalid_url(client):
    r = client.post("/api/v1/fetch-link", json={"url": "8.94 :1pm anD:/ i@c.nQ 没有链接"})
    assert r.status_code == 400
    assert "invalid_url" in r.text


def test_fetch_link_ssrf_guard_still_applies_to_extracted_url(client):
    r = client.post("/api/v1/fetch-link",
                    json={"url": "看这个 http://169.254.169.254/latest/meta-data 复制此链接"})
    assert r.status_code == 400
    assert "invalid_url" in r.text


def test_bulk_each_item_and_line_is_extracted(client, seen, monkeypatch):
    import app.api.routes as routes
    guarded = []
    monkeypatch.setattr(routes, "_assert_safe_url", lambda u: guarded.append(u))
    monkeypatch.setattr(routes, "_douyin_cookie_gate", lambda *a, **k: HTTPException(418, "stop"))
    r = client.post("/api/v1/bulk-download", json={"urls": [
        DOUYIN, "https://www.youtube.com/watch?v=abc", TIKTOK + "\n" + XHS, "没有链接的文字",
    ]})
    assert r.status_code == 418
    assert guarded == [
        "https://v.douyin.com/l4F4tDYO5oU/",
        "https://www.youtube.com/watch?v=abc",
        "https://vm.tiktok.com/ZMabc123/",
        "http://xhslink.com/a/AbCdEf",
        "没有链接的文字",           # unchanged -> the guard rejects it (400 in prod)
    ]


def test_resolve_input_container_route_extracts(client):
    r = client.post("/api/v1/resolve-input", json={"url": DOUYIN})
    assert r.status_code == 200, r.text
    assert r.json()["canonical_url"].startswith("https://v.douyin.com/l4F4tDYO5oU")


def test_resolve_input_container_route_no_url_is_400(client):
    r = client.post("/api/v1/resolve-input", json={"url": "8.94 :1pm anD:/ i@c.nQ 没有链接"})
    assert r.status_code == 400


def test_resolve_input_batch_helper_handles_share_text_and_comma_lists():
    import asyncio
    from app.api.resolve_input import resolve_input, ResolveInputRequest
    res = asyncio.run(resolve_input(ResolveInputRequest(raw_input=DOUYIN + "\nhttps://a.example/1, https://b.example/2"), None))
    assert [i.raw_input for i in res.items][1:] == ["https://a.example/1", "https://b.example/2"]
    assert res.items[0].canonical_url.startswith("https://v.douyin.com/l4F4tDYO5oU")
    assert len(res.items) == 3
