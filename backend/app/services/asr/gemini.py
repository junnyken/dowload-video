"""
Gemini provider — audio in, STRICT JSON segments out, validated hard.

SDK: google-generativeai (requirements.txt), same as app.services.llm_client.
Small audio (every chunk at the default 10-min / 48 kbps) goes inline; larger
audio uses the File API and is deleted afterwards.

Gemini is a general LLM, not a dedicated ASR model: its timestamps can drift,
go backwards, or exceed the clip. validate_payload() never invents or
re-times anything — it only drops empty-text segments, trims a sub-second
overlap with the previous segment and clamps an end that overshoots the clip
by less than the tolerance. Anything else fails the job as
provider_bad_output rather than shipping a broken SRT.
"""
from __future__ import annotations

import json
import math
import os
import re
import time

from app.services.asr import config
from app.services.asr.types import (
    ProviderBadOutput,
    ProviderError,
    ProviderTransientError,
    ProviderUnavailable,
    Segment,
    TranscriptResult,
)

# Timestamps may overshoot the clip end by this much (seconds) and are clamped.
END_TOLERANCE_SEC = 1.0
# A segment may start this much before the previous one ended; the previous
# end is trimmed. A larger overlap means the timeline is unreliable.
MAX_OVERLAP_REPAIR_SEC = 0.5

_TRANSIENT_NAMES = {
    "ResourceExhausted", "TooManyRequests", "ServiceUnavailable", "DeadlineExceeded",
    "InternalServerError", "GatewayTimeout", "RetryError", "Aborted",
}
_AUTH_NAMES = {"PermissionDenied", "Unauthenticated", "Forbidden"}

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _build_prompt(duration_sec: float, language: str | None, diarize: bool) -> str:
    speaker = (
        ', "speaker": "S1"  (label each distinct speaker S1, S2, ...)'
        if diarize else ""
    )
    lang_hint = f"The spoken language is most likely '{language}'. " if language else ""
    return (
        "Transcribe the speech in this audio clip verbatim, in its original language. "
        "Do NOT translate, summarise or add anything that is not spoken. "
        f"{lang_hint}"
        "Return ONLY a JSON object, no prose, no markdown, with this exact shape:\n"
        '{"language": "<ISO 639-1 code>", "segments": [{"start": <number>, "end": <number>, '
        f'"text": "<what was said>"{speaker}}}]}}\n'
        f"Rules: start/end are SECONDS (decimal numbers, not strings, not MM:SS) measured from "
        f"the beginning of THIS clip, which is {duration_sec:.1f} seconds long. "
        "Segments are in chronological order, do not overlap, and each covers one sentence "
        "or at most about 7 seconds. Skip silence and music. "
        'If there is no speech at all, return {"language": "unknown", "segments": []}.'
    )


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def parse_json_text(raw: str):
    if raw is None:
        raise ProviderBadOutput("Gemini trả về rỗng.", raw="")
    text = raw.strip()
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ProviderBadOutput(f"Gemini trả về không phải JSON hợp lệ: {exc}", raw=raw) from exc


def validate_payload(payload, duration_sec: float, *, diarize: bool = False, raw: str | None = None):
    """Return (language, [Segment]) or raise ProviderBadOutput. Never invents
    timestamps. duration_sec is the length of the clip that was sent."""
    if isinstance(payload, list):
        payload = {"segments": payload}
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ProviderBadOutput("Kết quả thiếu danh sách 'segments'.", raw=raw)

    language = payload.get("language")
    language = language.strip() if isinstance(language, str) and language.strip() else "unknown"

    out: list[Segment] = []
    for i, seg in enumerate(payload["segments"]):
        if not isinstance(seg, dict):
            raise ProviderBadOutput(f"Segment #{i} không phải object.", raw=raw)
        start, end, text = seg.get("start"), seg.get("end"), seg.get("text")
        if not _is_number(start) or not _is_number(end):
            raise ProviderBadOutput(f"Segment #{i}: thời gian không phải số ({start!r}, {end!r}).", raw=raw)
        start, end = float(start), float(end)
        if not isinstance(text, str):
            raise ProviderBadOutput(f"Segment #{i}: thiếu 'text'.", raw=raw)
        text = text.strip()
        if not text:
            continue  # repair: drop an empty segment (nothing invented)
        if start < 0:
            raise ProviderBadOutput(f"Segment #{i}: thời gian âm ({start}).", raw=raw)
        if end <= start:
            raise ProviderBadOutput(f"Segment #{i}: end ({end}) không lớn hơn start ({start}).", raw=raw)
        if start >= duration_sec:
            raise ProviderBadOutput(
                f"Segment #{i}: start {start:.2f}s nằm ngoài độ dài đoạn âm thanh {duration_sec:.2f}s.", raw=raw
            )
        if end > duration_sec + END_TOLERANCE_SEC:
            raise ProviderBadOutput(
                f"Segment #{i}: end {end:.2f}s vượt độ dài đoạn âm thanh {duration_sec:.2f}s.", raw=raw
            )
        end = min(end, duration_sec)  # repair: clamp an overshoot within tolerance

        if out:
            prev = out[-1]
            if start < prev.start:
                raise ProviderBadOutput(
                    f"Segment #{i}: thời gian đi lùi ({start:.2f}s < {prev.start:.2f}s).", raw=raw
                )
            if start < prev.end:
                overlap = prev.end - start
                if overlap > MAX_OVERLAP_REPAIR_SEC or start <= prev.start:
                    raise ProviderBadOutput(
                        f"Segment #{i}: chồng lấn {overlap:.2f}s với segment trước.", raw=raw
                    )
                prev.end = start  # repair: trim a sub-second overlap

        speaker = None
        if diarize:
            sp = seg.get("speaker")
            if isinstance(sp, (str, int)) and str(sp).strip():
                speaker = str(sp).strip()[:32]
        out.append(Segment(start=start, end=end, text=text, speaker=speaker))

    return language, out


def _classify(exc: Exception) -> Exception:
    name = type(exc).__name__
    if name in _TRANSIENT_NAMES or isinstance(exc, TimeoutError):
        return ProviderTransientError(f"Gemini: {name}")
    if name in _AUTH_NAMES:
        return ProviderUnavailable(f"Gemini từ chối khoá API ({name}).")
    return ProviderError(f"Gemini: {name}: {str(exc)[:300]}")


class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        self._api_key = os.environ.get("GEMINI_API_KEY", "")
        if not self._api_key:
            raise ProviderUnavailable("GEMINI_API_KEY chưa được cấu hình — không thể tạo phụ đề tự động.")
        self.model = config.gemini_model()

    def estimate_cost_usd(self, duration_sec: float, diarize: bool = False) -> float:
        return round(max(0.0, duration_sec) / 60.0 * config.price_per_min(self.name), 6)

    def _sdk(self):
        try:
            import google.generativeai as genai  # type: ignore  # noqa: PLC0415
        except ImportError as exc:
            raise ProviderUnavailable("Thư viện google-generativeai chưa được cài.") from exc
        genai.configure(api_key=self._api_key)
        return genai

    def transcribe(self, audio_path, *, language=None, diarize=False, duration_sec=None) -> TranscriptResult:
        if duration_sec is None:
            from app.services.asr.chunking import probe_duration_sec  # noqa: PLC0415
            duration_sec = probe_duration_sec(audio_path)

        genai = self._sdk()
        model = genai.GenerativeModel(
            model_name=self.model,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0,
                "max_output_tokens": config.gemini_max_output_tokens(),
            },
        )
        prompt = _build_prompt(duration_sec, language, diarize)

        uploaded = None
        try:
            if os.path.getsize(audio_path) <= config.gemini_inline_max_bytes():
                with open(audio_path, "rb") as f:
                    audio_part = {"mime_type": "audio/mpeg", "data": f.read()}
            else:
                uploaded = genai.upload_file(audio_path, mime_type="audio/mpeg")
                deadline = time.monotonic() + 120
                while getattr(getattr(uploaded, "state", None), "name", "ACTIVE") == "PROCESSING":
                    if time.monotonic() > deadline:
                        raise ProviderTransientError("Gemini File API xử lý file quá lâu.")
                    time.sleep(2)
                    uploaded = genai.get_file(uploaded.name)
                audio_part = uploaded

            response = model.generate_content(
                [prompt, audio_part],
                request_options={"timeout": config.gemini_timeout_sec()},
            )
        except (ProviderTransientError, ProviderUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        finally:
            if uploaded is not None:
                try:
                    genai.delete_file(uploaded.name)
                except Exception:  # noqa: BLE001
                    pass

        finish = None
        try:
            finish = getattr(getattr(response.candidates[0], "finish_reason", None), "name", None)
        except Exception:  # noqa: BLE001
            pass
        try:
            raw = response.text
        except Exception as exc:  # noqa: BLE001 — blocked / no candidates
            raise ProviderBadOutput(f"Gemini không trả nội dung (finish_reason={finish}).") from exc
        if finish == "MAX_TOKENS":
            raise ProviderBadOutput("Kết quả Gemini bị cắt cụt (MAX_TOKENS).", raw=raw)

        payload = parse_json_text(raw)
        lang, segments = validate_payload(payload, float(duration_sec), diarize=diarize, raw=raw)
        return TranscriptResult(segments=segments, language=lang, duration_sec=float(duration_sec))
