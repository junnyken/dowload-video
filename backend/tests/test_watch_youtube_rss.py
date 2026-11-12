"""
Phase 33F (Part 4a) — YouTube RSS fast detection + fallback + platform floor tests (TDD).
"""
from __future__ import annotations

import fakeredis
import pytest

from app.core import admin_switches, watch_config as cfg
from app.services import channel_watch as cw
from tests.test_channel_watch import T0, World, env, w  # noqa: F401

YT_ID = "UCBJycsmduvYEL83R_U4JriQ"


def _yt_page(cid: str = YT_ID, title: str = "Marques Brownlee") -> str:
    return (
        f'<html><link rel="canonical" href="https://www.youtube.com/channel/{cid}">'
        f'<meta property="og:title" content="{title}"></html>'
    )


def _rss_xml(*entries: tuple[str, str, str], cid: str = YT_ID) -> str:
    body = []
    for vid, title, pub in entries:
        body.append(
            f"""
  <entry>
    <id>yt:video:{vid}</id>
    <yt:videoId>{vid}</yt:videoId>
    <yt:channelId>{cid}</yt:channelId>
    <title>{title}</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v={vid}"/>
    <published>{pub}</published>
  </entry>"""
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        'xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <yt:channelId>{cid}</yt:channelId>\n"
        "  <title>Marques Brownlee</title>"
        + "".join(body)
        + "\n</feed>"
    )


def test_youtube_platform_floor_default_1800_and_respects_tier_min_interval(monkeypatch):
    monkeypatch.setattr(admin_switches, "_r", lambda: fakeredis.FakeRedis(decode_responses=True))
    admin_switches.reset_cache()
    for k in list(__import__("os").environ):
        if k.startswith("WATCH_"):
            monkeypatch.delenv(k, raising=False)

    assert cfg.platform_floor_sec("youtube") == 1800
    assert cfg.platform_floor_sec("tiktok") == 21600

    # Default Pro min_interval_sec is 21600 -> respected (max(21600, 1800) == 21600)
    assert cw.source_interval_sec("youtube", [cfg.min_interval_sec("pro")]) == 21600

    # When paid tier interval is lowered via env to 1800s, YouTube scans at 1800s while Free stays 86400s and TikTok stays 21600s
    monkeypatch.setenv("WATCH_PRO_MIN_INTERVAL_SEC", "1800")
    assert cw.source_interval_sec("youtube", [cfg.min_interval_sec("pro")]) == 1800
    assert cw.source_interval_sec("youtube", [cfg.min_interval_sec("free")]) == 86400
    assert cw.source_interval_sec("tiktok", [cfg.min_interval_sec("pro")]) == 21600

    # Explicit WATCH_PLATFORM_FLOOR_SEC_YOUTUBE override works
    monkeypatch.setenv("WATCH_PLATFORM_FLOOR_SEC_YOUTUBE", "900")
    monkeypatch.setenv("WATCH_PRO_MIN_INTERVAL_SEC", "900")
    assert cfg.platform_floor_sec("youtube") == 900
    assert cw.source_interval_sec("youtube", [cfg.min_interval_sec("pro")]) == 900


def test_parse_youtube_rss_xml_extracts_id_url_title_and_published_at():
    xml = _rss_xml(
        ("_0G4OG39XjI", "Older Video", "2026-11-10T10:00:00+00:00"),
        ("bad_id", "Ignored Bad ID", "2026-11-11T10:00:00+00:00"),
        ("3iRUwVzRDZQ", "Newest Video &amp; Test", "2026-11-12T09:30:00+00:00"),
    )
    out = cw.parse_youtube_rss(xml, limit=10)
    assert [e["id"] for e in out] == ["3iRUwVzRDZQ", "_0G4OG39XjI"]
    assert out[0]["url"] == "https://www.youtube.com/watch?v=3iRUwVzRDZQ"
    assert out[0]["title"] == "Newest Video & Test"
    assert out[0]["published_at"].startswith("2026-11-12T09:30:00")

    # Rejects DOCTYPE / malformed XML with ListingError(permanent=False)
    with pytest.raises(cw.ListingError) as e_xxe:
        cw.parse_youtube_rss('<?xml version="1.0"?><!DOCTYPE foo><feed></feed>', limit=5)
    assert e_xxe.value.permanent is False

    with pytest.raises(cw.ListingError) as e_bad:
        cw.parse_youtube_rss("<html>not xml", limit=5)
    assert e_bad.value.permanent is False


def test_youtube_rss_resolves_handle_once_and_uses_rss_through_ledger(w, env, monkeypatch):  # noqa: F811
    env.setenv("WATCH_PLATFORMS_ENABLED", "tiktok,youtube")
    page_calls = []
    rss_calls = []
    ytdlp_calls = []

    monkeypatch.setattr(cw, "_youtube_channel_page", lambda u: page_calls.append(u) or _yt_page())

    feed_state = [
        _rss_xml(("AAAAAAAAAA1", "Baseline Video", "2026-10-09T12:00:00+00:00")),
    ]

    def fake_rss_fetch(url: str) -> tuple[int, str]:
        rss_calls.append(url)
        return 200, feed_state[0]

    def fail_ytdlp(*_a, **_kw):
        ytdlp_calls.append(1)
        raise AssertionError("yt-dlp fallback should not be called when RSS succeeds")

    monkeypatch.setattr(cw, "_youtube_rss_fetch", fake_rss_fetch)
    monkeypatch.setattr(cw, "_list_youtube_ytdlp", fail_ytdlp)

    # 1. Add channel via @handle -> resolves @handle -> UC... ONCE and runs baseline via RSS
    res = cw.add_source(
        user_id="u1",
        tier="pro",
        is_admin=False,
        email_verified=True,
        url="https://www.youtube.com/@mkbhd",
        mode="one_tap",
        ip="1.1.1.1",
    )
    assert res["baseline"] == "done"
    src = res["source"]
    assert src["platform"] == "youtube"
    # Confirm UC... channel_id is persisted in watch_sources
    stored_src = next(s for s in w.rows("watch_sources") if s["id"] == src["id"])
    assert stored_src["external_channel_id"] == YT_ID
    assert page_calls == ["https://www.youtube.com/@mkbhd"]
    assert rss_calls == [f"https://www.youtube.com/feeds/videos.xml?channel_id={YT_ID}"]
    assert ytdlp_calls == []

    seen_baseline = w.rows("watch_items_seen")
    assert len(seen_baseline) == 1
    assert seen_baseline[0]["state"] == "baseline"
    assert seen_baseline[0]["external_item_id"] == "AAAAAAAAAA1"

    # 2. Next scan finds a new video in RSS -> no re-resolve of @handle, goes through ledger as state="new"
    feed_state[0] = _rss_xml(
        ("BBBBBBBBBB2", "Brand New Upload", "2026-10-10T08:15:00+00:00"),
        ("AAAAAAAAAA1", "Baseline Video", "2026-10-09T12:00:00+00:00"),
    )
    w.advance(hours=6, minutes=5)
    scan_res = cw.scan_source(src["id"])
    assert scan_res["outcome"] == "ok"
    assert scan_res["new_items"] == 1
    assert len(page_calls) == 1  # still 1 — never re-resolved!
    assert len(rss_calls) == 2

    items = cw.list_items(w.db, "u1")
    assert len(items) == 1
    assert items[0]["title"] == "Brand New Upload"
    assert items[0]["published_at"].startswith("2026-10-10T08:15:00")
    assert items[0]["discovered_at"] is not None


def test_youtube_rss_failure_falls_back_to_ytdlp_listing(w, env, monkeypatch):  # noqa: F811
    env.setenv("WATCH_PLATFORMS_ENABLED", "youtube")
    rss_calls = []
    ytdlp_calls = []

    def broken_rss(url: str) -> tuple[int, str]:
        rss_calls.append(url)
        return 404, "Not Found"

    def fake_extract(url: str, limit: int) -> dict:
        ytdlp_calls.append((url, limit))
        return {"entries": [{"id": "CCCCCCCCCC3", "title": "Fallback Video", "timestamp": 1760000000}]}

    src = {
        "id": "src_yt_1",
        "platform": "youtube",
        "external_channel_id": YT_ID,
        "canonical_url": f"https://www.youtube.com/channel/{YT_ID}",
    }
    out = cw._list_youtube(src, 10, rss_fetch=broken_rss, extract=fake_extract)
    assert rss_calls == [f"https://www.youtube.com/feeds/videos.xml?channel_id={YT_ID}"]
    assert ytdlp_calls == [(f"https://www.youtube.com/channel/{YT_ID}/videos", 10)]
    assert len(out) == 1
    assert out[0]["id"] == "CCCCCCCCCC3"


def test_youtube_legacy_source_without_uc_id_resolves_once_and_persists(w, env, monkeypatch):  # noqa: F811
    env.setenv("WATCH_PLATFORMS_ENABLED", "youtube")
    page_calls = []
    rss_calls = []
    monkeypatch.setattr(cw, "_youtube_channel_page", lambda u: page_calls.append(u) or _yt_page())
    monkeypatch.setattr(
        cw,
        "_youtube_rss_fetch",
        lambda u: rss_calls.append(u) or (200, _rss_xml(("DDDDDDDDDD4", "Legacy Resolved", "2026-10-10T01:00:00+00:00"))),
    )

    # Insert a legacy watch_sources row whose external_channel_id was a handle rather than UC...
    legacy = w.db.table("watch_sources").insert({
        "platform": "youtube",
        "external_channel_id": "@mkbhd",
        "canonical_url": "https://www.youtube.com/@mkbhd",
        "display_name": "@mkbhd",
        "status": "active",
        "scan_provider": "ytdlp_flat",
        "next_scan_at": T0.isoformat(),
        "subscriber_count": 1,
        "created_at": T0.isoformat(),
    }).execute().data[0]
    w.db.table("watch_subscriptions").insert({
        "user_id": "u1",
        "source_id": legacy["id"],
        "mode": "one_tap",
        "status": "active",
        "tier": "pro",
        "delivery_interval_sec": 21600,
        "created_at": T0.isoformat(),
    }).execute()

    res = cw.scan_source(legacy["id"])
    assert res["outcome"] == "baseline"
    updated = cw.get_source(w.db, legacy["id"])
    assert updated["external_channel_id"] == YT_ID
    assert updated["canonical_url"] == f"https://www.youtube.com/channel/{YT_ID}"
    assert page_calls == ["https://www.youtube.com/@mkbhd"]
    assert rss_calls == [f"https://www.youtube.com/feeds/videos.xml?channel_id={YT_ID}"]


def test_youtube_disabled_when_not_in_watch_platforms_enabled(w, env, monkeypatch):  # noqa: F811
    # env fixture sets WATCH_PLATFORMS_ENABLED="tiktok" (youtube NOT included)
    rss_calls = []
    monkeypatch.setattr(cw, "_youtube_rss_fetch", lambda u: rss_calls.append(u) or (200, ""))

    assert cfg.platform_enabled("youtube") is False
    with pytest.raises(cw.WatchError) as e:
        cw.add_source(
            user_id="u1",
            tier="pro",
            is_admin=False,
            email_verified=True,
            url="https://www.youtube.com/@mkbhd",
            mode="one_tap",
            ip="1.1.1.1",
        )
    assert e.value.code == "platform_not_enabled"
    assert rss_calls == []
