"""
The quality picker named portrait streams by the wrong side.

A resolution label names the SHORT side — a 1080x1920 reel is a 1080p video,
and _get_base_opts' format selector already reads the number that way (it has
a portrait-first clause and a measured YouTube Shorts case in its comment).
_extract_available_formats named them by height instead, so the two halves read
one number two ways and nobody noticed until the bytes were compared.

Measured on facebook.com/reel/2033078270746905 — real streams 1440x2560,
1080x1920, 720x1280:

    UI offered "4K 2560p"      -> downloaded 1440x2560   right, by luck
    UI offered "2K 1920p"      -> downloaded 1440x2560   same file as "4K"
    UI offered "Full HD 1280p" -> downloaded 1080x1920   a tier too high

Two bugs in one: every choice below the top returned a bigger stream than the
label and the size column promised, and the tier names were inflated a step
(1440x2560 called "4K" when a player calls it 1440p).

The selector was right. These tests pin the labels to it.
"""

from __future__ import annotations

import pytest

from app.services.downloader import _extract_available_formats, _stream_short_side


def _video(width, height, size, vcodec="av01.0.08M.08", fid=None):
    return {
        "format_id": fid or f"{width}x{height}",
        "url": f"https://example.invalid/{width}x{height}.mp4",
        "ext": "mp4", "width": width, "height": height,
        "vcodec": vcodec, "acodec": "none",
        "filesize": size, "protocol": "https",
    }


# The reel from the bug report, as yt-dlp actually reports it.
REEL_FORMATS = {
    "duration": 14.51,
    "formats": [
        {"format_id": "audio", "url": "https://example.invalid/a.m4a", "ext": "m4a",
         "vcodec": "none", "acodec": "mp4a.40.5", "filesize": 107_960, "protocol": "https"},
        _video(720, 1280, 2_979_000),
        _video(1080, 1920, 4_320_000),
        _video(1440, 2560, 7_790_000),
    ],
}


class TestShortSideIsTheLabelledSide:

    @pytest.mark.parametrize("width,height,expected", [
        (1440, 2560, 1440),   # portrait reel — named by width
        (1080, 1920, 1080),
        (720, 1280, 720),
        (1920, 1080, 1080),   # landscape — named by height, unchanged
        (3840, 2160, 2160),
        (1080, 1080, 1080),   # square
    ])
    def test_it_picks_the_shorter_side(self, width, height, expected):
        assert _stream_short_side({"width": width, "height": height}) == expected

    def test_a_stream_with_no_width_falls_back_to_height(self):
        """Some extractors report height only. Better a possibly-wrong label
        than dropping the format out of the picker entirely."""
        assert _stream_short_side({"height": 720}) == 720
        assert _stream_short_side({}) == 0


class TestTheReelFromTheBugReport:

    def test_the_picker_offers_the_resolutions_a_player_would_report(self):
        formats = _extract_available_formats(REEL_FORMATS)["video_formats"]
        by_height = {f["height"]: f for f in formats}
        assert sorted(by_height) == [720, 1080, 1440], (
            "portrait streams must be offered as 720/1080/1440, not 1280/1920/2560"
        )

    def test_the_tier_names_are_no_longer_inflated(self):
        formats = _extract_available_formats(REEL_FORMATS)["video_formats"]
        labels = {f["height"]: f["label"] for f in formats}
        assert labels[1440] == "2K", "1440x2560 is 1440p — calling it 4K oversells it"
        assert labels[1080] == "Full HD"
        assert labels[720] == "HD"

    def test_each_offer_maps_to_a_distinct_stream(self):
        """The whole complaint: two different picks produced one identical
        7.5 MB file. Distinct offers must describe distinct bytes."""
        formats = _extract_available_formats(REEL_FORMATS)["video_formats"]
        urls = [f["url"] for f in formats]
        assert len(set(urls)) == len(urls) == 3

    def test_the_advertised_size_belongs_to_the_stream_that_will_be_fetched(self):
        formats = _extract_available_formats(REEL_FORMATS)["video_formats"]
        by_height = {f["height"]: f for f in formats}
        # video-only stream + the merged audio track
        assert by_height[1440]["filesize_mb"] == pytest.approx(7.53, abs=0.05)
        assert by_height[1080]["filesize_mb"] == pytest.approx(4.22, abs=0.05)
        assert by_height[720]["filesize_mb"] == pytest.approx(2.94, abs=0.05)

    def test_the_merge_ceiling_uses_the_same_convention(self):
        """max_video_only_height feeds a >1080 paid-tier guard in routes.py.
        Left as a height it flagged every 1080p reel as above-1080 content."""
        assert _extract_available_formats(REEL_FORMATS)["max_video_only_height"] == 1440


class TestLandscapeIsUntouched:
    """The convention only ever differed for portrait video; a regression here
    would break the ordinary case to fix the rare one."""

    LANDSCAPE = {
        "duration": 230,
        "formats": [
            {"format_id": "audio", "url": "https://example.invalid/a.m4a", "ext": "m4a",
             "vcodec": "none", "acodec": "mp4a.40.2", "filesize": 3_000_000, "protocol": "https"},
            _video(1280, 720, 20_000_000, vcodec="avc1.64001f"),
            _video(1920, 1080, 45_000_000, vcodec="avc1.640028"),
            _video(3840, 2160, 180_000_000, vcodec="vp09.00.50.08"),
        ],
    }

    def test_heights_and_labels_are_what_they_always_were(self):
        formats = _extract_available_formats(self.LANDSCAPE)["video_formats"]
        labels = {f["height"]: f["label"] for f in formats}
        assert labels == {720: "HD", 1080: "Full HD", 2160: "4K"}

    def test_resolution_strings_still_read_as_expected(self):
        formats = _extract_available_formats(self.LANDSCAPE)["video_formats"]
        assert {f["resolution"] for f in formats} == {"720p", "1080p", "2160p"}


class TestTheSelectorAgreesWithTheLabel:
    """The property the bug violated, checked against the real selector string
    rather than a restatement of it: for every offered format, the format
    selector built from its own `height` must pick that exact stream.
    """

    @staticmethod
    def _pick(formats: list[dict], target_short: int) -> dict | None:
        """Resolve _get_base_opts' selector chain for `video_<target_short>`
        against a format list, returning the video stream it lands on."""
        from app.services.downloader import _get_base_opts
        import yt_dlp

        selector_str = _get_base_opts(
            "https://example.invalid/v", quality=f"video_{target_short}",
        )["format"]
        ydl = yt_dlp.YoutubeDL({"format": selector_str, "quiet": True, "simulate": True})
        selector = ydl.build_format_selector(selector_str)
        info = {"formats": formats, "id": "x", "title": "x"}
        chosen = list(selector({"formats": formats, "incomplete_formats": False}))
        if not chosen:
            return None
        picked = chosen[0]
        # A merged pick reports its parts; the video half is what we care about.
        parts = picked.get("requested_formats") or [picked]
        return next((p for p in parts if (p.get("vcodec") or "none") != "none"), None)

    @pytest.mark.parametrize("target,expected_dims", [
        (1440, (1440, 2560)),
        (1080, (1080, 1920)),
        (720, (720, 1280)),
    ])
    def test_every_portrait_offer_downloads_the_stream_it_names(self, target, expected_dims):
        picked = self._pick(REEL_FORMATS["formats"], target)
        assert picked is not None, f"selector found nothing for video_{target}"
        assert (picked["width"], picked["height"]) == expected_dims, (
            f"asking for {target}p handed back "
            f"{picked['width']}x{picked['height']} — the label lies about the bytes"
        )
