"""
Canonical job state model for VidGrab.
Single source of truth for all surfaces: web, extension, Telegram, admin.

Import VALID_JOB_STATES, STATE_TRANSITIONS, and STATE_UI from here;
never hardcode status strings elsewhere.
"""

# ── All valid job states ───────────────────────────────────────────────
VALID_JOB_STATES: frozenset = frozenset({
    "pending",
    "queued",
    "processing",
    "success",
    "failed",
    "cancelled",
    "expired",
    "metadata_only",
    "archived",
    "abandoned",
    # Stability phase additions
    "stale",      # Detected stuck by scanner; pending retry or abandon decision
    "retrying",   # Auto-retry in progress (brief intermediate before pending re-queue)
    "partial",    # Bulk/container completed with some items missing
})

# ── Terminal states — no further automated transitions ─────────────────
TERMINAL_STATES: frozenset = frozenset({
    "success",
    "cancelled",
    "expired",
    "metadata_only",
    "abandoned",
})

# ── States that block further transitions until manually cleared ───────
RETRYABLE_STATES: frozenset = frozenset({"failed", "abandoned", "stale"})

# ── Active (non-terminal, non-success) states ─────────────────────────
ACTIVE_STATES: frozenset = frozenset({"pending", "queued", "processing", "retrying"})

# ── Non-terminal states the scanner should watch for staleness ─────────
SCANNABLE_STATES: frozenset = frozenset({"pending", "queued", "processing", "retrying"})

# ── Allowed state transitions: from_state → set of allowed next states ─
STATE_TRANSITIONS: dict = {
    "pending":       {"queued", "processing", "failed", "cancelled", "stale"},
    "queued":        {"processing", "failed", "cancelled", "stale"},
    "processing":    {"success", "failed", "cancelled", "abandoned", "stale", "partial"},
    "retrying":      {"processing", "failed", "cancelled", "stale"},
    "stale":         {"pending", "retrying", "failed", "abandoned"},
    "partial":       {"pending", "failed", "abandoned", "success"},
    "success":       {"archived", "expired"},
    "failed":        {"pending", "retrying", "cancelled"},
    "abandoned":     {"pending", "retrying"},
    "cancelled":     set(),
    "expired":       {"metadata_only"},
    "metadata_only": set(),
    "archived":      {"expired"},
}


def is_valid_transition(from_state: str, to_state: str) -> bool:
    """Return True if the from_state → to_state transition is allowed."""
    allowed = STATE_TRANSITIONS.get(from_state, set())
    return to_state in allowed


# ── Retry policy ──────────────────────────────────────────────────────
MAX_MANUAL_RETRIES = 5      # cap on manual user retries per job
MAX_AUTO_RETRIES   = 3      # mirrors recovery.py MAX_AUTO_RECOVERY

# Retry backoff multipliers (seconds between attempts: 0, 5, 10, 20, 40)
RETRY_BACKOFF_SECONDS = [0, 5, 10, 20, 40]


def should_allow_retry(job: dict) -> tuple:
    """
    Return (allowed: bool, reason: str) for whether a job can be retried.
    job is a dict with keys: status, recovery_attempts (optional).
    """
    status = job.get("status", "")
    if status not in RETRYABLE_STATES:
        return False, f"Status '{status}' is not retryable."
    attempts = job.get("recovery_attempts") or 0
    if attempts >= MAX_MANUAL_RETRIES:
        return False, f"Max retries ({MAX_MANUAL_RETRIES}) reached."
    return True, ""


# ── Per-state UI copy (Vietnamese) ───────────────────────────────────
STATE_UI: dict = {
    "pending":       {"label": "Chờ xử lý",         "color": "muted",   "icon": "Clock"},
    "queued":        {"label": "Trong hàng đợi",     "color": "muted",   "icon": "Clock"},
    "processing":    {"label": "Đang xử lý",         "color": "warning", "icon": "Loader2"},
    "retrying":      {"label": "Đang thử lại",       "color": "warning", "icon": "RefreshCw"},
    "stale":         {"label": "Không phản hồi",     "color": "warning", "icon": "AlertTriangle"},
    "partial":       {"label": "Hoàn thành một phần","color": "warning", "icon": "AlertCircle"},
    "success":       {"label": "Thành công",          "color": "success", "icon": "CheckCircle2"},
    "failed":        {"label": "Thất bại",            "color": "error",   "icon": "XCircle"},
    "cancelled":     {"label": "Đã hủy",              "color": "muted",   "icon": "XCircle"},
    "expired":       {"label": "Hết hạn",             "color": "warning", "icon": "Clock"},
    "metadata_only": {"label": "Chỉ metadata",        "color": "muted",   "icon": "FileVideo"},
    "archived":      {"label": "Đã lưu trữ",         "color": "accent",  "icon": "Archive"},
    "abandoned":     {"label": "Đã từ bỏ",           "color": "error",   "icon": "XCircle"},
}


def get_ui(status: str) -> dict:
    """Return UI metadata for a job status with a safe fallback."""
    return STATE_UI.get(status, {"label": status or "Không rõ", "color": "muted", "icon": "Clock"})
