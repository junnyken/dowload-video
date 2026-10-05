"""
Shared types and errors for the ASR provider layer (Phase 32A).

Every failure a provider or the pipeline can produce is an AsrError subclass
carrying a stable machine `code`. The task stores that code on the job row
(as an "[code] message" prefix of error_message, since migration 031 has no
error_code column) and the API surfaces it as `error_code`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: Optional[str] = None


@dataclass
class TranscriptResult:
    segments: list[Segment] = field(default_factory=list)
    language: str = "unknown"
    duration_sec: float = 0.0


# User-facing (Vietnamese) messages per code. The API returns these as
# `detail` so the existing frontend, which renders `detail` as a string,
# keeps working; the code itself travels separately as `error_code`.
ERROR_MESSAGES_VI: dict[str, str] = {
    "asr_disabled": "Tính năng tạo phụ đề tự động đang tạm tắt.",
    "asr_paused": "Tính năng tạo phụ đề tự động đang tạm dừng bởi quản trị viên.",
    "spend_ceiling_reached": "Hệ thống đã dùng hết ngân sách tạo phụ đề hôm nay. Vui lòng thử lại vào ngày mai.",
    "budget_unavailable": "Không kiểm tra được ngân sách hệ thống, vui lòng thử lại sau.",
    "provider_unavailable": "Dịch vụ nhận dạng giọng nói chưa sẵn sàng, vui lòng thử lại sau.",
    "provider_bad_output": "Dịch vụ nhận dạng giọng nói trả kết quả không hợp lệ, chưa thể tạo phụ đề.",
    "provider_transient": "Dịch vụ nhận dạng giọng nói đang quá tải, vui lòng thử lại sau.",
    "provider_error": "Dịch vụ nhận dạng giọng nói gặp lỗi.",
    "no_speech": "Không nhận diện được lời thoại nào trong video (có thể video không có tiếng nói).",
    "queue_unavailable": "Không xếp được job vào hàng đợi, vui lòng thử lại sau. Hạn mức đã được hoàn lại.",
    "timeout": "Xử lý quá thời gian cho phép.",
    "interrupted": "Job bị gián đoạn giữa chừng (máy chủ khởi động lại). Hạn mức đã được hoàn lại.",
    "stuck_timeout": "Job bị treo quá thời gian cho phép. Hạn mức đã được hoàn lại.",
    "source_missing": "Video đã hết hạn hoặc bị xoá khỏi server. Vui lòng tải lại video.",
    "too_long": "Video vượt giới hạn thời lượng cho tạo phụ đề tự động.",
    "audio_extract_failed": "Không tách được âm thanh từ video.",
    "internal_error": "Lỗi hệ thống khi tạo phụ đề.",
    "cancelled": "Đã huỷ bởi người dùng.",
}


class AsrError(Exception):
    code = "internal_error"

    def __init__(self, message: str = "", *, code: str | None = None, raw: str | None = None):
        if code:
            self.code = code
        self.message = message or ERROR_MESSAGES_VI.get(self.code, self.code)
        # Truncated raw provider output — only ever shown to admins (selftest).
        self.raw = raw[:2000] if raw else None
        super().__init__(self.message)


class ProviderUnavailable(AsrError):
    code = "provider_unavailable"


class ProviderBadOutput(AsrError):
    code = "provider_bad_output"


class ProviderTransientError(AsrError):
    code = "provider_transient"


class ProviderError(AsrError):
    code = "provider_error"


class NoSpeechError(AsrError):
    code = "no_speech"


class BudgetError(AsrError):
    """Global spend guard refusals: asr_paused / spend_ceiling_reached / budget_unavailable."""
    code = "spend_ceiling_reached"


@runtime_checkable
class AsrProvider(Protocol):
    name: str

    def transcribe(
        self,
        audio_path: str,
        *,
        language: str | None = None,
        diarize: bool = False,
        duration_sec: float | None = None,
    ) -> TranscriptResult: ...

    def estimate_cost_usd(self, duration_sec: float, diarize: bool = False) -> float: ...
