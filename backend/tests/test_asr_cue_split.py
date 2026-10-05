"""
2026-10-05 live selftest: gemini-3.5-flash returned 60 s of VTV1 news as three
12-18 s segments of ~60 words, although the prompt asked for ~7 s. Every ASR
result is now cut into subtitle-sized cues (<= ASR_CUE_MAX_SEC seconds,
<= ASR_CUE_MAX_CHARS characters) without losing or reordering any words.
"""
from app.services.asr import pipeline
from app.services.asr.cue_split import split_long_segments
from app.services.asr.types import Segment, TranscriptResult

# The exact segments Gemini returned in that selftest.
REAL = [
    Segment(14.0, 32.0, "Ngân hàng tài chính là ngành tiên phong trong thực hiện chuyển đổi số góp phần xây dựng chính phủ điện tử tiến tới chính phủ số. Đây là nhận định được đưa ra tại diễn đàn phát triển ngân hàng thông minh trong tiến trình công nghiệp hóa hiện đại hóa đến năm 2030 tầm nhìn đến năm 2045."),
    Segment(32.0, 48.0, "Dù dịch Covid-19 kéo dài thế nhưng nhờ việc thúc đẩy ứng dụng số hóa, ngành ngân hàng và các tổ chức tài chính vẫn duy trì ổn định. Đơn cử như ngân hàng VPBank vẫn đảm bảo trên 70% doanh số về huy động vốn, 98% các giao các giao dịch được thực hiện trên các kênh số hóa và kênh điện tử một cách thông suốt."),
    Segment(48.0, 60.0, "Nhờ số hóa, chi phí trên mỗi giao dịch của VPBank đã giảm 98% nên ngân hàng này sẽ tiến tới là 100% tự động hóa tất cả các sản phẩm dịch vụ cung ứng đến cho khách hàng."),
]


def _split(segs, max_sec=7.0, max_chars=84):
    return split_long_segments(segs, max_sec=max_sec, max_chars=max_chars)


def _words(segs):
    return " ".join(s.text for s in segs).split()


class TestRealSelftestOutput:
    def test_every_cue_fits_the_limits(self):
        out = _split(REAL)
        assert len(out) > len(REAL)
        for c in out:
            assert len(c.text) <= 84, c.text
            # Time is shared by character count, so allow a small overshoot.
            assert c.end - c.start <= 7.0 * 1.3, (c.start, c.end, c.text)

    def test_no_orphan_scraps(self):
        """The first greedy version left 0.8 s cues like "cách thông suốt."."""
        for c in _split(REAL):
            assert c.end - c.start >= 2.0 and len(c.text) >= 30, (c.start, c.end, c.text)

    def test_no_word_is_lost_or_reordered(self):
        assert _words(_split(REAL)) == _words(REAL)

    def test_outer_times_are_kept_and_cues_are_contiguous(self):
        out = _split(REAL)
        for seg in REAL:
            inside = [c for c in out if seg.start <= c.start < seg.end]
            assert inside[0].start == seg.start
            assert inside[-1].end == seg.end
            for a, b in zip(inside, inside[1:]):
                assert a.end == b.start and a.start < a.end

    def test_sentence_ends_are_preferred_cut_points(self):
        out = _split(REAL[:1])
        assert any(c.text.endswith("chính phủ số.") for c in out)


class TestEdgeCases:
    def test_short_segment_is_untouched(self):
        s = Segment(1.0, 3.0, "Xin chào các bạn.", speaker="S1")
        assert _split([s]) == [s]

    def test_speaker_is_carried_to_every_piece(self):
        out = _split([Segment(0.0, 20.0, REAL[2].text, speaker="S2")])
        assert len(out) > 1 and {c.speaker for c in out} == {"S2"}

    def test_long_duration_short_text_still_splits_by_time(self):
        out = _split([Segment(0.0, 30.0, "một hai ba bốn năm sáu bảy tám chín mười")])
        assert len(out) >= 2
        assert _words(out) == "một hai ba bốn năm sáu bảy tám chín mười".split()

    def test_text_without_punctuation_splits_between_words(self):
        text = " ".join(["từ"] * 80)
        out = _split([Segment(0.0, 6.0, text)])
        assert all(len(c.text) <= 84 for c in out)
        assert _words(out) == text.split()

    def test_one_huge_word_is_kept_whole(self):
        word = "x" * 200
        out = _split([Segment(0.0, 10.0, word)])
        assert _words(out) == [word]

    def test_zero_length_or_empty_segment_passes_through(self):
        segs = [Segment(5.0, 5.0, "a " * 100), Segment(6.0, 20.0, "   ")]
        assert _split(segs) == segs


class TestPipelineApplies:
    def test_transcribe_chunked_returns_split_cues(self, monkeypatch, tmp_path):
        class _Provider:
            def estimate_cost_usd(self, sec, diarize):
                return 0.0

            def transcribe(self, path, **kw):
                return TranscriptResult(segments=list(REAL), language="vi", duration_sec=60.0)

        monkeypatch.setattr(pipeline.budget, "killswitch_on", lambda: False)
        monkeypatch.delenv("ASR_CUE_MAX_SEC", raising=False)
        monkeypatch.delenv("ASR_CUE_MAX_CHARS", raising=False)
        res = pipeline.transcribe_chunked(
            _Provider(), str(tmp_path / "a.mp3"), 60.0,
            work_dir=str(tmp_path), tracker=pipeline.CostTracker(),
        )
        assert len(res.segments) > 3
        assert all(len(s.text) <= 84 for s in res.segments)
        assert _words(res.segments) == _words(REAL)
