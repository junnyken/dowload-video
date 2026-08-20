"""
googlevideo.com CDN request headers.

A signed videoplayback URL is issued to the InnerTube client named in its `c=`
parameter, and the CDN answers 403 when the User-Agent does not match. This
project extracts with player_client android_vr, so the URLs it hands to
/proxy-download carry c=ANDROID_VR while that endpoint sent a desktop Chrome UA
to everything — which is why 4K downloads failed with 403 even though the
server's own IP matched the ip= embedded in the link.
"""
import pytest

from app.core.youtube_cdn import cdn_client_name, request_headers

BASE = "https://rr1---sn-jhjup-nboz.googlevideo.com/videoplayback?expire=1787229748&itag=135"


def _url(client=None):
    return BASE + (f"&c={client}" if client else "")


def test_reads_the_client_from_the_url():
    assert cdn_client_name(_url("ANDROID_VR")) == "ANDROID_VR"
    assert cdn_client_name(_url("web")) == "WEB"
    assert cdn_client_name(_url()) is None


def test_android_vr_gets_its_own_user_agent_not_a_browser_one():
    ua = request_headers(_url("ANDROID_VR"))["User-Agent"]
    assert "youtube.vr" in ua, ua
    assert "Mozilla" not in ua, "a desktop UA here is exactly what the CDN rejects"


def test_native_client_sends_no_referer():
    """A native app identifying itself with a browser Referer is incoherent."""
    h = request_headers(_url("ANDROID_VR"))
    assert "Referer" not in h and "Origin" not in h, h


def test_web_client_keeps_browser_identity_and_referer():
    h = request_headers(_url("WEB"))
    assert "Mozilla" in h["User-Agent"]
    assert h.get("Referer") == "https://www.youtube.com/"


def test_unknown_or_missing_client_falls_back_to_a_browser():
    for u in (_url(), _url("SOME_FUTURE_CLIENT")):
        h = request_headers(u)
        assert "Mozilla" in h["User-Agent"], h
        assert h.get("Referer") == "https://www.youtube.com/"


def test_user_agent_carries_no_transport_suffix():
    """yt-dlp stores some UAs with a trailing ',gzip(gfe)' that is not part of it."""
    for client in ("ANDROID_VR", "WEB", "IOS"):
        assert "gzip(gfe)" not in request_headers(_url(client))["User-Agent"]


def test_every_client_yields_a_non_empty_user_agent():
    for client in ("ANDROID_VR", "ANDROID", "IOS", "WEB", "MWEB", "TVHTML5"):
        ua = request_headers(_url(client))["User-Agent"]
        assert ua and len(ua) > 10, (client, ua)
