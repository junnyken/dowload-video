"""Phase 32A — provider layer: selection, Gemini output validation, chunk
merge offsets, per-chunk retry/billing rules. No network: SDKs are faked."""
from __future__ import annotations

import json
import sys
import types

import pytest

from app.services.asr import budget, get_provider
from app.services.asr.chunking import merge_chunk_results, plan_chunks
from app.services.asr.gemini import GeminiProvider, parse_json_text, validate_payload
from app.services.asr.pipeline import CostTracker, transcribe_chunked
from app.services.asr.types import (
    BudgetError,
    ProviderBadOutput,
    ProviderTransientError,
    ProviderUnavailable,
    Segment,
    TranscriptResult,
)
from tests._asr_fakes import asr_env  # noqa: F401 (fixture)


# ── provider selection ────────────────────────────────────────────────────

def test_default_provider_is_gemini_and_needs_its_key(asr_env):
    with pytest.raises(ProviderUnavailable) as ei:
        get_provider()
    assert ei.value.code == "provider_unavailable"
    assert "GEMINI_API_KEY" in ei.value.message


def test_no_silent_fallback_to_whisper(asr_env, monkeypatch):
    """OPENAI key present but Gemini selected without key -> unavailable, not Whisper."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(ProviderUnavailable):
        get_provider()


def test_whisper_selected_without_key_is_unavailable(asr_env, monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "whisper")
    with pytest.raises(ProviderUnavailable):
        get_provider()


def test_unknown_provider_is_unavailable(asr_env, monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "nope")
    with pytest.raises(ProviderUnavailable):
        get_provider()


def test_cost_estimate_uses_env_price(asr_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("ASR_PRICE_PER_MIN_GEMINI", "0.01")
    assert get_provider().estimate_cost_usd(600) == pytest.approx(0.1)


# ── Gemini output validation ──────────────────────────────────────────────

def test_validate_good_json():
    lang, segs = validate_payload(
        {"language": "vi", "segments": [
            {"start": 0.0, "end": 2.5, "text": "Xin chào"},
            {"start": 2.5, "end": 5, "text": "  "},          # empty -> dropped
            {"start": 5.0, "end": 7.25, "text": "Cảm ơn"},
        ]}, 10.0)
    assert lang == "vi"
    assert [(s.start, s.end, s.text) for s in segs] == [(0.0, 2.5, "Xin chào"), (5.0, 7.25, "Cảm ơn")]


def test_validate_fenced_json_is_parsed():
    payload = parse_json_text('```json\n{"segments": [{"start": 0, "end": 1, "text": "a"}]}\n```')
    assert validate_payload(payload, 5)[1][0].text == "a"


@pytest.mark.parametrize("segments", [
    [{"start": 3, "end": 4, "text": "b"}, {"start": 1, "end": 2, "text": "a"}],     # goes backwards
    [{"start": 0, "end": 3, "text": "a"}, {"start": 1, "end": 4, "text": "b"}],     # 2s overlap
    [{"start": 0, "end": 12, "text": "a"}],                                          # end past clip
    [{"start": 11, "end": 11.5, "text": "a"}],                                       # start past clip
    [{"start": -1, "end": 1, "text": "a"}],                                          # negative
    [{"start": 2, "end": 2, "text": "a"}],                                           # zero length
    [{"start": "00:01", "end": "00:02", "text": "a"}],                               # MM:SS strings
    [{"start": True, "end": 2, "text": "a"}],                                        # bool is not a number
    [{"start": float("nan"), "end": 2, "text": "a"}],
    [{"start": 0, "end": 1}],                                                         # no text
    ["not an object"],
])
def test_validate_rejects_bad_timelines(segments):
    with pytest.raises(ProviderBadOutput) as ei:
        validate_payload({"segments": segments}, 10.0)
    assert ei.value.code == "provider_bad_output"


@pytest.mark.parametrize("raw", ["", "Here is your transcript: ...", "{not json", '{"text": "no segments"}', "[1, 2"])
def test_garbage_is_provider_bad_output(raw):
    with pytest.raises(ProviderBadOutput):
        validate_payload(parse_json_text(raw), 10.0, raw=raw)


def test_validate_repairs_only_small_overlap_and_end_overshoot():
    _, segs = validate_payload({"segments": [
        {"start": 0, "end": 2.3, "text": "a"},
        {"start": 2.0, "end": 4, "text": "b"},      # 0.3s overlap -> prev end trimmed
        {"start": 4, "end": 10.6, "text": "c"},     # overshoot 0.6 <= 1.0 tolerance -> clamped
    ]}, 10.0)
    assert [(s.start, s.end) for s in segs] == [(0, 2.0), (2.0, 4), (4, 10.0)]


def test_speaker_kept_only_when_diarize():
    payload = {"segments": [{"start": 0, "end": 1, "text": "a", "speaker": "S1"}]}
    assert validate_payload(payload, 5)[1][0].speaker is None
    assert validate_payload(payload, 5, diarize=True)[1][0].speaker == "S1"


# ── GeminiProvider against a fake SDK ─────────────────────────────────────

def _fake_genai(monkeypatch, *, text=None, exc=None, finish="STOP"):
    calls = {"generate": 0}

    class _Model:
        def __init__(self, model_name, generation_config):
            calls["model"] = model_name
            calls["config"] = generation_config

        def generate_content(self, parts, request_options=None):
            calls["generate"] += 1
            calls["parts"] = parts
            if exc is not None:
                raise exc
            cand = types.SimpleNamespace(finish_reason=types.SimpleNamespace(name=finish))
            return types.SimpleNamespace(text=text, candidates=[cand])

    genai = types.ModuleType("google.generativeai")
    genai.configure = lambda api_key: calls.setdefault("key", api_key)
    genai.GenerativeModel = _Model
    google = types.ModuleType("google")
    google.generativeai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.generativeai", genai)
    return calls


def test_gemini_transcribe_inline_json_mode(asr_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("ASR_GEMINI_MODEL", "gemini-test")
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x" * 100)
    calls = _fake_genai(monkeypatch, text=json.dumps(
        {"language": "vi", "segments": [{"start": 0.5, "end": 2, "text": "Xin chào"}]}))
    res = GeminiProvider().transcribe(str(audio), duration_sec=5.0)
    assert res.language == "vi" and res.segments[0].text == "Xin chào"
    assert calls["model"] == "gemini-test"
    assert calls["config"]["response_mime_type"] == "application/json"
    assert calls["parts"][1]["mime_type"] == "audio/mpeg"


def test_gemini_truncated_output_is_bad_output(asr_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    _fake_genai(monkeypatch, text='{"segments": [', finish="MAX_TOKENS")
    with pytest.raises(ProviderBadOutput):
        GeminiProvider().transcribe(str(audio), duration_sec=5.0)


def test_gemini_rate_limit_is_transient(asr_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    ResourceExhausted = type("ResourceExhausted", (Exception,), {})
    _fake_genai(monkeypatch, exc=ResourceExhausted("429"))
    with pytest.raises(ProviderTransientError):
        GeminiProvider().transcribe(str(audio), duration_sec=5.0)


def test_gemini_rejected_key_is_unavailable(asr_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    PermissionDenied = type("PermissionDenied", (Exception,), {})
    _fake_genai(monkeypatch, exc=PermissionDenied("403"))
    with pytest.raises(ProviderUnavailable):
        GeminiProvider().transcribe(str(audio), duration_sec=5.0)


# ── chunking ──────────────────────────────────────────────────────────────

def test_plan_chunks_covers_duration_and_folds_tiny_tail():
    assert plan_chunks(100, 600) == [(0.0, 100.0)]
    assert plan_chunks(1500, 600) == [(0.0, 600.0), (600.0, 600.0), (1200.0, 300.0)]
    assert plan_chunks(1210, 600) == [(0.0, 600.0), (600.0, 610.0)]   # 10s tail folded


def test_merge_offsets_each_chunk():
    merged = merge_chunk_results([
        (600.0, TranscriptResult([Segment(1.0, 2.0, "c")], "vi", 300)),
        (0.0, TranscriptResult([Segment(0.0, 1.5, "a"), Segment(598.0, 600.0, "b")], "vi", 600)),
    ])
    assert [(s.start, s.end, s.text) for s in merged.segments] == [
        (0.0, 1.5, "a"), (598.0, 600.0, "b"), (601.0, 602.0, "c"),
    ]
    assert merged.language == "vi" and merged.duration_sec == 900


def test_merge_trims_segment_straddling_the_cut():
    merged = merge_chunk_results([
        (0.0, TranscriptResult([Segment(595.0, 600.0, "a")], "vi", 600)),
        (600.0, TranscriptResult([Segment(-0.0, 0.0001, "x"), Segment(0.0, 2.0, "b")], "vi", 100)),
    ])
    assert [(s.start, s.end) for s in merged.segments] == [(595.0, 600.0), (600.0, 602.0)]


# ── per-chunk retry / billing ─────────────────────────────────────────────

class _FlakyProvider:
    name = "gemini"

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    def estimate_cost_usd(self, duration_sec, diarize=False):
        return round(duration_sec / 60 * 0.01, 6)

    def transcribe(self, path, *, language=None, diarize=False, duration_sec=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise ProviderTransientError("503")
        return TranscriptResult([Segment(0.0, 1.0, "ok")], "vi", duration_sec)


def test_transient_chunk_retry_is_capped_and_every_attempt_billed(asr_env, monkeypatch, tmp_path):
    monkeypatch.setenv("ASR_CHUNK_MAX_RETRIES", "1")
    day = budget.reserve(0.01, 1.0)
    tracker = CostTracker(spend_day=day)
    p = _FlakyProvider(failures=1)
    res = transcribe_chunked(p, str(tmp_path / "a.mp3"), 60, work_dir=str(tmp_path), tracker=tracker)
    assert res.segments[0].text == "ok"
    assert p.calls == 2 and tracker.paid_calls == 2
    assert tracker.actual_cost == pytest.approx(0.02)
    assert tracker.extra_reserved == pytest.approx(0.01)
    assert budget.snapshot(day)["spend_usd"] == pytest.approx(0.02)  # retry hit the ledger up-front

    p2 = _FlakyProvider(failures=5)
    with pytest.raises(ProviderTransientError):
        transcribe_chunked(p2, str(tmp_path / "a.mp3"), 60, work_dir=str(tmp_path),
                           tracker=CostTracker(spend_day=day))
    assert p2.calls == 2  # 1 + 1 retry, never more


def test_retry_refused_when_it_would_cross_the_ceiling(asr_env, monkeypatch, tmp_path):
    monkeypatch.setenv("ASR_SPEND_CEILING_USD", "0.015")
    day = budget.reserve(0.01, 1.0)
    p = _FlakyProvider(failures=1)
    with pytest.raises(BudgetError) as ei:
        transcribe_chunked(p, str(tmp_path / "a.mp3"), 60, work_dir=str(tmp_path),
                           tracker=CostTracker(spend_day=day))
    assert ei.value.code == "spend_ceiling_reached"
    assert p.calls == 1


def test_killswitch_stops_before_the_next_provider_call(asr_env, tmp_path):
    budget.set_killswitch(True)
    p = _FlakyProvider(failures=0)
    with pytest.raises(BudgetError) as ei:
        transcribe_chunked(p, str(tmp_path / "a.mp3"), 60, work_dir=str(tmp_path), tracker=CostTracker())
    assert ei.value.code == "asr_paused"
    assert p.calls == 0
