"""
Unit tests for app.services.subtitle_format (SRT / WebVTT parser + serializer).
Run: pytest backend/tests/test_subtitle_format.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.subtitle_format import Cue, parse_srt, parse_vtt, serialize_srt, serialize_vtt


SRT_SAMPLE = """1
00:00:01,000 --> 00:00:04,000
Hello world

2
00:00:05,500 --> 00:00:07,250
Line one
Line two

3
00:01:02,010 --> 00:01:05,000
Final cue
"""

VTT_SAMPLE = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
Hello world

2
00:00:05.500 --> 00:00:07.250
Line one
Line two

00:01:02.010 --> 00:01:05.000
Cue without explicit identifier
"""


class TestSrtRoundTrip:
    def test_parse_srt_basic(self):
        cues = parse_srt(SRT_SAMPLE)
        assert len(cues) == 3
        assert cues[0].index == 1
        assert cues[0].start == "00:00:01,000"
        assert cues[0].end == "00:00:04,000"
        assert cues[0].text == "Hello world"
        assert cues[1].text == "Line one\nLine two"
        assert cues[2].index == 3
        assert cues[2].start == "00:01:02,010"
        assert cues[2].end == "00:01:05,000"

    def test_serialize_srt_round_trip(self):
        cues = parse_srt(SRT_SAMPLE)
        serialized = serialize_srt(cues)
        reparsed = parse_srt(serialized)

        assert len(reparsed) == len(cues)
        for original, again in zip(cues, reparsed):
            assert original.index == again.index
            assert original.start == again.start
            assert original.end == again.end
            assert original.text == again.text

    def test_translated_round_trip_preserves_timing(self):
        """Swap text (simulate translation), verify timing/index untouched."""
        cues = parse_srt(SRT_SAMPLE)
        translated = [
            Cue(index=c.index, start=c.start, end=c.end, text=f"[TRANSLATED] {c.text}")
            for c in cues
        ]
        serialized = serialize_srt(translated)
        reparsed = parse_srt(serialized)

        assert len(reparsed) == len(cues)
        for original, translated_cue, again in zip(cues, translated, reparsed):
            # Timing/index preserved byte-for-byte
            assert again.index == original.index
            assert again.start == original.start
            assert again.end == original.end
            # Text reflects the translated value
            assert again.text == translated_cue.text
            assert again.text.startswith("[TRANSLATED]")


class TestVttRoundTrip:
    def test_parse_vtt_basic(self):
        cues = parse_vtt(VTT_SAMPLE)
        assert len(cues) == 3
        assert cues[0].start == "00:00:01.000"
        assert cues[0].end == "00:00:04.000"
        assert cues[0].text == "Hello world"
        assert cues[1].text == "Line one\nLine two"
        # Cue without explicit identifier still parses correctly
        assert cues[2].start == "00:01:02.010"
        assert cues[2].text == "Cue without explicit identifier"

    def test_serialize_vtt_round_trip(self):
        cues = parse_vtt(VTT_SAMPLE)
        serialized = serialize_vtt(cues)
        assert serialized.startswith("WEBVTT")
        reparsed = parse_vtt(serialized)

        assert len(reparsed) == len(cues)
        for original, again in zip(cues, reparsed):
            assert original.start == again.start
            assert original.end == again.end
            assert original.text == again.text

    def test_translated_round_trip_preserves_timing(self):
        cues = parse_vtt(VTT_SAMPLE)
        translated = [
            Cue(index=c.index, start=c.start, end=c.end, text=f"[TRANSLATED] {c.text}")
            for c in cues
        ]
        serialized = serialize_vtt(translated)
        reparsed = parse_vtt(serialized)

        assert len(reparsed) == len(cues)
        for original, translated_cue, again in zip(cues, translated, reparsed):
            assert again.start == original.start
            assert again.end == original.end
            assert again.text == translated_cue.text
            assert again.text.startswith("[TRANSLATED]")


class TestEdgeCases:
    def test_empty_content(self):
        assert parse_srt("") == []
        assert parse_vtt("") == []
        assert parse_vtt("WEBVTT\n") == []

    def test_srt_crlf_line_endings(self):
        crlf_sample = SRT_SAMPLE.replace("\n", "\r\n")
        cues = parse_srt(crlf_sample)
        assert len(cues) == 3
        assert cues[0].text == "Hello world"
