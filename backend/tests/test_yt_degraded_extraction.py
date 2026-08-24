"""
Detecting a YouTube extraction that parsed fine but carries nothing usable.

A Short came back as 0.52 MB for 13 seconds. The request had asked for
quality=video_4320 and the response reported downloaded_height=640 with the
file named kGWFwVWwJYU_18.mp4 — YouTube's legacy progressive format 18, 360p.

The height cap was not involved. android_vr answered without any adaptive
(video-only) streams, which is where every resolution above 360p lives, so the
format selector fell to `best[ext=mp4]` and 18 was the only candidate. Layer 1
treated "did not raise" as success, set `info`, and every later layer is
guarded by `if info is None` — so the web_safari + PO-token layer that would
have returned the adaptive streams never ran.
"""

from __future__ import annotations

from app.services.downloader import _yt_extraction_is_degraded


def _progressive_18():
    """Format 18 as YouTube reports it for a vertical Short: 360x640, muxed."""
    return {"format_id": "18", "ext": "mp4", "width": 360, "height": 640,
            "vcodec": "avc1.42001E", "acodec": "mp4a.40.2"}


def _adaptive_video(height, width):
    return {"format_id": f"v{height}", "ext": "mp4", "width": width, "height": height,
            "vcodec": "avc1.640028", "acodec": "none"}


def _audio_only():
    return {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"}


class TestDegradedDetection:

    def test_progressive_only_is_degraded(self):
        assert _yt_extraction_is_degraded({"formats": [_progressive_18()]}) is True

    def test_progressive_plus_audio_is_still_degraded(self):
        """Muxed video + audio-only is not enough: nothing above 360p exists."""
        info = {"formats": [_progressive_18(), _audio_only()]}
        assert _yt_extraction_is_degraded(info) is True

    def test_adaptive_streams_present_is_healthy(self):
        info = {"formats": [_progressive_18(), _audio_only(),
                            _adaptive_video(1920, 1080)]}
        assert _yt_extraction_is_degraded(info) is False

    def test_empty_and_missing_formats_are_degraded(self):
        assert _yt_extraction_is_degraded({"formats": []}) is True
        assert _yt_extraction_is_degraded({}) is True
        assert _yt_extraction_is_degraded(None) is True

    def test_a_vertical_short_is_not_judged_by_height(self):
        """Format 18 on a Short reports height=640, so any "height looks too
        low" test would call this healthy. The check is about adaptive streams
        being offered at all, which is orientation-independent."""
        info = {"formats": [_progressive_18()]}
        assert info["formats"][0]["height"] == 640
        assert _yt_extraction_is_degraded(info) is True

    def test_vcodec_none_entries_do_not_count_as_video(self):
        info = {"formats": [_audio_only(), {"format_id": "sb0", "vcodec": "none",
                                            "acodec": "none"}]}
        assert _yt_extraction_is_degraded(info) is True


class TestDegradedResultIsNotCachedForLong:
    """Phase A caches for 30 minutes. Caching a degraded extraction for that
    long pinned the URL to 360p for everyone asking, with no retry of the
    clients that return adaptive formats — the failure outlived its cause by
    half an hour."""

    def test_degraded_cache_hit_is_discarded_and_deleted(self):
        import inspect
        from app.services import downloader
        src = inspect.getsource(downloader)
        assert "HIT but degraded" in src
        assert "_rc.delete(_yt_phase_a_key)" in src, (
            "a degraded entry written before this check existed must be cleared, "
            "not served for the rest of its TTL"
        )

    def test_degraded_write_uses_a_short_ttl(self):
        import inspect
        from app.services import downloader
        src = inspect.getsource(downloader)
        assert "_ttl = 60 if _degraded_now else _YT_PHASE_A_TTL" in src

    def test_full_ttl_still_applies_to_a_healthy_extraction(self):
        from app.services.downloader import _yt_extraction_is_degraded
        healthy = {"formats": [_adaptive_video(1920, 1080), _audio_only()]}
        assert _yt_extraction_is_degraded(healthy) is False


class TestAdaptiveFallbackClients:
    """Measured against the Short that exposed this, from a datacenter IP: of
    nine YouTube clients tried, only visionos returned adaptive streams
    (29 of them, up to 1080x1920). android_vr returned format 18 alone.
    web_safari — the client the bgutil PO-token layer uses — returned nothing,
    so letting the chain run past a degraded result was necessary but not
    sufficient on its own."""

    def test_visionos_is_tried_first(self):
        from app.services.downloader import _YT_ADAPTIVE_FALLBACK_CLIENTS
        assert _YT_ADAPTIVE_FALLBACK_CLIENTS[0] == "visionos"

    def test_ytdlp_defaults_are_kept_as_a_second_attempt(self):
        """yt-dlp's default client set moves with each release — an unpinned run
        picked visionos by itself, so it is the next best guess if visionos is
        ever blocked."""
        from app.services.downloader import _YT_ADAPTIVE_FALLBACK_CLIENTS
        assert None in _YT_ADAPTIVE_FALLBACK_CLIENTS

    def test_the_clients_that_measured_empty_are_not_in_the_list(self):
        from app.services.downloader import _YT_ADAPTIVE_FALLBACK_CLIENTS
        for dud in ("android_vr", "ios", "tv", "web_safari", "web", "mweb",
                    "web_embedded", "android"):
            assert dud not in _YT_ADAPTIVE_FALLBACK_CLIENTS, (
                f"{dud} returned zero adaptive streams when measured; listing it "
                "only adds latency to every degraded extraction"
            )

    def test_layer_1b_runs_before_any_paid_layer(self):
        """It must sit inside the Layer 1 degraded branch — proxy and ScraperAPI
        cost money, and this costs nothing."""
        import inspect
        from app.services import downloader
        src = inspect.getsource(downloader)
        pos_1b = src.index("Layer 1b")
        pos_proxy = src.index("Layer 2: android_vr via residential proxy")
        assert pos_1b < pos_proxy


class TestFallbackIsPreserved:
    """Holding the degraded result aside must never turn a 360p download into a
    failure — it is restored when no better layer produces anything."""

    def test_layer_one_stashes_rather_than_discards(self):
        import inspect
        from app.services import downloader
        src = inspect.getsource(downloader)
        assert "_degraded_info = info" in src
        assert "info = _degraded_info" in src, (
            "the progressive-only result must be restored when nothing better "
            "turns up, or this trades 360p for an error"
        )
