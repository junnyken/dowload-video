import os
from unittest.mock import MagicMock, patch

import pytest

from app.services.asr_service import AsrUnavailableError, transcribe_audio


def test_raises_when_no_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake")
    with pytest.raises(AsrUnavailableError):
        transcribe_audio(str(audio))


def _fake_segment(start, end, text):
    seg = MagicMock()
    seg.start = start
    seg.end = end
    seg.text = text
    return seg


def test_transcribe_maps_segments_to_cues(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake")

    fake_response = MagicMock()
    fake_response.language = "vietnamese"
    fake_response.segments = [
        _fake_segment(0.0, 2.5, "Xin chào"),
        _fake_segment(2.5, 5.0, "  "),   # blank — must be filtered out
        _fake_segment(5.0, 7.25, "Cảm ơn"),
    ]

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create.return_value = fake_response

    with patch("openai.OpenAI", return_value=mock_client):
        result = transcribe_audio(str(audio))

    assert result["language"] == "vietnamese"
    cues = result["cues"]
    assert len(cues) == 2  # blank segment dropped
    assert cues[0].index == 1
    assert cues[0].start == "00:00:00,000"
    assert cues[0].end == "00:00:02,500"
    assert cues[0].text == "Xin chào"
    assert cues[1].index == 2
    assert cues[1].start == "00:00:05,000"
    assert cues[1].end == "00:00:07,250"
