"""
21/10 — /channel-relay: the extension hands a whole Douyin channel to the app /
website. TestClient + fakeredis; no network.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app as fastapi_app
from tests._china_fakes import rc  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)
SEC = "MS4wLjABAAAAkQs2tRzfg_eQ94ErmIEe4ba2ADzMVA7N7PbV-92qtgw3tLxfFB15eEW4hYgnRYPh"
PROFILE = f"https://www.douyin.com/user/{SEC}"


@pytest.fixture(autouse=True)
def _unlimited():
    from app.api import channel_relay
    was = channel_relay.limiter.enabled
    channel_relay.limiter.enabled = False
    yield
    channel_relay.limiter.enabled = was


@pytest.fixture(autouse=True)
def _signed_in():
    from app.core.auth_middleware import get_optional_user
    fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": "u-relay"}
    yield
    fastapi_app.dependency_overrides.pop(get_optional_user, None)


def _start(url=PROFILE):
    return client.post("/api/v1/channel-relay", json={"url": url})


def test_round_trip(rc):
    r = _start(PROFILE + "?from_tab_name=main")
    assert r.status_code == 200, r.text
    body = r.json()
    tok = body["token"]
    assert body["openUrl"] == f"{PROFILE}#vg-relay={tok}" and len(tok) == 32
    assert 1700 < rc.ttl(f"vidgrab:relay:{tok}") <= 1800

    g = client.get(f"/api/v1/channel-relay/{tok}").json()
    assert g["status"] == "waiting" and g["items"] == [] and g["channelUrl"] == PROFILE

    items = [{"url": "https://www.douyin.com/video/7696450520369712419?previous_page=x", "title": "  《夜盲》 3 "},
             {"url": "https://www.douyin.com/video/7696450520369712419"},           # duplicate
             {"url": "https://www.douyin.com/note/7600000000000000001"},            # image post
             {"url": "https://evil.example/video/7600000000000000002"}]
    p = client.put(f"/api/v1/channel-relay/{tok}", json={"items": items, "channelTitle": "凌晨睡不着", "skipped": 1})
    assert p.status_code == 200 and p.json()["count"] == 1
    g = client.get(f"/api/v1/channel-relay/{tok}").json()
    assert g["status"] == "scrolling" and g["channelTitle"] == "凌晨睡不着" and g["skipped"] == 1
    assert g["items"] == [{"id": "7696450520369712419", "url": "https://www.douyin.com/video/7696450520369712419", "title": "《夜盲》 3"}]

    client.put(f"/api/v1/channel-relay/{tok}", json={"items": items, "done": True})
    assert client.get(f"/api/v1/channel-relay/{tok}").json()["status"] == "done"
    assert 1700 < rc.ttl(f"vidgrab:relay:{tok}") <= 1800                      # updates keep the TTL


@pytest.mark.parametrize("url", ["https://www.tiktok.com/@a", "https://www.douyin.com/video/7696450520369712419",
                                 "https://www.douyin.com.evil.example/user/MS4wLjABAAAAx1234567890"])
def test_start_refuses_other_links(url, rc):
    r = _start(url)
    assert r.status_code == 400 and r.json()["error_code"] == "unsupported_url"


@pytest.mark.parametrize("tok", ["nope", "A" * 32, "../../etc/passwd" + "a" * 16])
def test_unknown_or_malformed_token(tok, rc):
    assert client.get(f"/api/v1/channel-relay/{tok}").status_code == 404
    assert client.put(f"/api/v1/channel-relay/{tok}", json={"items": []}).status_code in (404, 405)


def test_start_needs_sign_in(rc):
    # 21/10: a whole channel is signed-in only; the server's first page stays open to guests
    from app.core.auth_middleware import get_optional_user
    fastapi_app.dependency_overrides[get_optional_user] = lambda: None
    r = _start()
    assert r.status_code == 401 and r.json()["error_code"] == "login_required"
    assert not rc.keys("vidgrab:relay:*")


def test_login_wall_then_resume(rc):
    # 5.11.1: Douyin's sign-in wall → "login"; after signing in the extension goes on to "done"
    tok = _start().json()["token"]
    item = [{"url": "https://www.douyin.com/video/7696450520369712419"}]
    client.put(f"/api/v1/channel-relay/{tok}", json={"items": item, "loginWall": True})
    assert client.get(f"/api/v1/channel-relay/{tok}").json()["status"] == "login"
    client.put(f"/api/v1/channel-relay/{tok}", json={"items": item})
    assert client.get(f"/api/v1/channel-relay/{tok}").json()["status"] == "scrolling"
    client.put(f"/api/v1/channel-relay/{tok}", json={"items": item, "done": True, "loginWall": False})
    assert client.get(f"/api/v1/channel-relay/{tok}").json()["status"] == "done"
