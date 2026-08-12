from app.services.subtitle_format import Cue
from app.services.subtitle_qa import check_reading_speed


def _cue(index, start, end, text):
    return Cue(index=index, start=start, end=end, text=text)


def test_normal_speed_cue_not_flagged():
    # ~15 chars over 2s = 7.5 CPS, well under threshold
    cues = [_cue(1, "00:00:01,000", "00:00:03,000", "Hello there!")]
    assert check_reading_speed(cues) == []


def test_too_fast_cue_flagged():
    # 60 chars in 1s = 60 CPS, way over 20 CPS threshold
    text = "x" * 60
    cues = [_cue(1, "00:00:01,000", "00:00:02,000", text)]
    warnings = check_reading_speed(cues)
    assert len(warnings) == 1
    assert warnings[0].cue_index == 1
    assert warnings[0].reason in ("too_fast", "both")


def test_too_long_cue_flagged_even_if_cps_ok():
    # 50 chars over 10s = 5 CPS (fine) but exceeds the 42-char cap
    text = "x" * 50
    cues = [_cue(1, "00:00:01,000", "00:00:11,000", text)]
    warnings = check_reading_speed(cues)
    assert len(warnings) == 1
    assert warnings[0].reason == "too_long"


def test_zero_duration_cue_skipped_not_flagged():
    cues = [_cue(1, "00:00:01,000", "00:00:01,000", "x" * 100)]
    assert check_reading_speed(cues) == []


def test_malformed_timestamp_skipped_not_flagged():
    cues = [_cue(1, "not-a-time", "00:00:02,000", "x" * 100)]
    assert check_reading_speed(cues) == []


def test_empty_text_skipped():
    cues = [_cue(1, "00:00:01,000", "00:00:02,000", "   ")]
    assert check_reading_speed(cues) == []


def test_vtt_timestamp_format_supported():
    text = "x" * 60
    cues = [_cue(1, "00:00:01.000", "00:00:02.000", text)]
    assert len(check_reading_speed(cues)) == 1
