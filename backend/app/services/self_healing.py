"""
AI Self-Healing Extractor
==========================
Automatically detects when a platform's extraction starts failing, diagnoses
the root cause via AI, and reroutes traffic to fallback extractors.

Components:
  1. Error Collector — aggregates extraction errors by platform in a sliding window
  2. Health Monitor — triggers when error rate exceeds threshold
  3. AI Diagnostician — sends error samples to Gemini for root cause analysis
  4. Auto-Rerouter — switches platform to fallback extraction method
  5. Alert System — sends Telegram notification to admin

Env vars:
  SELF_HEAL_ENABLED          — master switch (default true)
  SELF_HEAL_WINDOW_MINUTES   — sliding window size (default 60)
  SELF_HEAL_ERROR_THRESHOLD  — error rate % to trigger (default 30)
  SELF_HEAL_MIN_SAMPLES      — minimum errors before triggering (default 5)
  SELF_HEAL_COOLDOWN_MINUTES — cooldown between diagnoses per platform (default 30)
  SELF_HEAL_TELEGRAM_ENABLED — send Telegram alerts (default true)
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock, Thread
from typing import Optional

from app.core.structured_log import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENABLED = os.getenv("SELF_HEAL_ENABLED", "true").lower() in ("true", "1", "yes")
WINDOW_MINUTES = int(os.getenv("SELF_HEAL_WINDOW_MINUTES", "60"))
ERROR_THRESHOLD = float(os.getenv("SELF_HEAL_ERROR_THRESHOLD", "30"))  # percent
MIN_SAMPLES = int(os.getenv("SELF_HEAL_MIN_SAMPLES", "5"))
COOLDOWN_MINUTES = int(os.getenv("SELF_HEAL_COOLDOWN_MINUTES", "30"))
TELEGRAM_ENABLED = os.getenv("SELF_HEAL_TELEGRAM_ENABLED", "true").lower() in ("true", "1")

# Fallback routes per platform
FALLBACK_ROUTES: dict[str, list[str]] = {
    "youtube":      ["cobalt", "invidious", "proxy_pool"],
    "tiktok":       ["cobalt", "tikwm", "browser_direct"],
    "instagram":    ["cobalt", "igram", "browser_direct"],
    "facebook":     ["cobalt", "fbdown", "browser_direct"],
    "twitter":      ["cobalt", "fxtwitter", "browser_direct"],
    "douyin":       ["browser_direct", "proxy_cn"],
    "xiaohongshu":  ["browser_direct", "proxy_cn"],
    "bilibili":     ["browser_direct", "proxy_cn"],
    "kuaishou":     ["browser_direct", "proxy_cn"],
    "threads":      ["cobalt", "browser_direct"],
    "pinterest":    ["cobalt", "browser_direct"],
    "reddit":       ["cobalt", "browser_direct"],
    "vimeo":        ["direct", "proxy_pool"],
}


# ---------------------------------------------------------------------------
# Error tracking (in-memory, thread-safe)
# ---------------------------------------------------------------------------

@dataclass
class ErrorSample:
    timestamp: float
    error_code: str
    error_message: str
    url_hash: str  # hash of URL, not the URL itself (privacy)
    http_status: int = 0


@dataclass
class PlatformHealth:
    total_requests: int = 0
    total_errors: int = 0
    errors: list[ErrorSample] = field(default_factory=list)
    successes: int = 0
    last_diagnosis_time: float = 0.0
    active_reroute: Optional[str] = None  # current fallback method
    reroute_until: float = 0.0  # timestamp when reroute expires


class HealthMonitor:
    """Thread-safe health monitor for all platforms."""

    def __init__(self):
        self._lock = Lock()
        self._platforms: dict[str, PlatformHealth] = defaultdict(PlatformHealth)

    def record_success(self, platform: str) -> None:
        """Record a successful extraction."""
        if not ENABLED:
            return
        with self._lock:
            health = self._platforms[platform]
            health.total_requests += 1
            health.successes += 1

    def record_error(
        self, platform: str, error_code: str, error_message: str,
        url_hash: str = "", http_status: int = 0,
        async_diagnose: bool = True,
    ) -> Optional[dict]:
        """
        Record a failed extraction. When threshold is crossed, triggers AI
        diagnosis on a background daemon thread (async_diagnose=True) so
        request/extraction callers are never blocked, or synchronously when
        async_diagnose=False.
        """
        if not ENABLED:
            return None

        now = time.time()
        sample = ErrorSample(
            timestamp=now,
            error_code=error_code,
            error_message=error_message[:500],
            url_hash=url_hash[:16],
            http_status=http_status,
        )

        with self._lock:
            health = self._platforms[platform]
            health.total_requests += 1
            health.total_errors += 1
            health.errors.append(sample)

            # Prune old errors outside the window
            cutoff = now - (WINDOW_MINUTES * 60)
            health.errors = [e for e in health.errors if e.timestamp > cutoff]

            # Check if threshold is crossed
            window_errors = len(health.errors)
            window_total = health.successes + window_errors
            if window_total < MIN_SAMPLES:
                return None

            error_rate = (window_errors / window_total) * 100
            if error_rate < ERROR_THRESHOLD:
                return None

            # Cooldown check
            if now - health.last_diagnosis_time < COOLDOWN_MINUTES * 60:
                return None

            health.last_diagnosis_time = now
            samples_snapshot = list(health.errors[-10:])

        # Threshold crossed — run diagnosis outside lock
        if async_diagnose:
            Thread(
                target=self._trigger_diagnosis,
                args=(platform, samples_snapshot, error_rate),
                daemon=True,
            ).start()
            return None

        return self._trigger_diagnosis(platform, samples_snapshot, error_rate)

    def get_active_reroute(self, platform: str) -> Optional[str]:
        """Return the active fallback method for a platform, or None."""
        with self._lock:
            health = self._platforms.get(platform)
            if not health:
                return None
            if health.active_reroute and time.time() < health.reroute_until:
                return health.active_reroute
            if health.active_reroute:
                # Reroute expired — clear it
                health.active_reroute = None
                health.reroute_until = 0
            return None

    def set_reroute(self, platform: str, method: str, duration_minutes: int = 60) -> None:
        """Activate a reroute for a platform."""
        with self._lock:
            health = self._platforms[platform]
            health.active_reroute = method
            health.reroute_until = time.time() + (duration_minutes * 60)
            logger.info(
                "self_heal.reroute_activated",
                extra={"platform": platform, "method": method, "duration_min": duration_minutes},
            )
        if method == "cobalt":
            try:
                from app.core import platform_health as ph
                ph.set_prefer_cobalt(platform, True, reason="self_healing")
            except Exception:
                pass

    def clear_reroute(self, platform: str) -> None:
        """Clear an active reroute."""
        with self._lock:
            health = self._platforms.get(platform)
            if health:
                health.active_reroute = None
                health.reroute_until = 0
        try:
            from app.core import platform_health as ph
            ph.set_prefer_cobalt(platform, False)
        except Exception:
            pass

    def get_status(self) -> dict:
        """Return health status for all platforms."""
        with self._lock:
            now = time.time()
            cutoff = now - (WINDOW_MINUTES * 60)
            result = {}
            for platform, health in self._platforms.items():
                window_errors = [e for e in health.errors if e.timestamp > cutoff]
                window_total = health.successes + len(window_errors)
                error_rate = (len(window_errors) / window_total * 100) if window_total > 0 else 0

                result[platform] = {
                    "total_requests": health.total_requests,
                    "total_errors": health.total_errors,
                    "window_errors": len(window_errors),
                    "window_total": window_total,
                    "error_rate_pct": round(error_rate, 1),
                    "active_reroute": health.active_reroute,
                    "reroute_expires": (
                        datetime.fromtimestamp(health.reroute_until, tz=timezone.utc).isoformat()
                        if health.active_reroute else None
                    ),
                    "status": (
                        "rerouted" if health.active_reroute
                        else "degraded" if error_rate >= ERROR_THRESHOLD
                        else "healthy"
                    ),
                }
            return result

    def _trigger_diagnosis(
        self, platform: str, error_samples: list[ErrorSample], error_rate: float,
    ) -> Optional[dict]:
        """Run AI diagnosis on error samples."""
        logger.warning(
            "self_heal.threshold_crossed",
            extra={"platform": platform, "error_rate": f"{error_rate:.1f}%", "samples": len(error_samples)},
        )

        # Build error summary for AI
        error_summary = "\n".join(
            f"- [{s.error_code}] HTTP {s.http_status}: {s.error_message[:200]}"
            for s in error_samples[:8]
        )

        diagnosis = _diagnose_with_ai(platform, error_summary, error_rate)
        if not diagnosis:
            diagnosis = _diagnose_heuristic(platform, error_samples)

        # Phase C2: Force cobalt fallback route for instagram & twitter when
        # cookie_expired or rate_limited error rate exceeds threshold (30% in 1h).
        if platform in ("instagram", "twitter") and any(
            s.error_code in _COOKIE_OR_RATE_CODES for s in error_samples
        ):
            diagnosis["should_reroute"] = True
            diagnosis["suggested_route"] = "cobalt"

        # Auto-reroute if diagnosis suggests it
        if diagnosis.get("should_reroute"):
            fallbacks = FALLBACK_ROUTES.get(platform, [])
            if fallbacks:
                method = diagnosis.get("suggested_route", fallbacks[0])
                if method not in fallbacks:
                    method = fallbacks[0]
                self.set_reroute(platform, method, duration_minutes=60)
                diagnosis["rerouted_to"] = method

        # Send Telegram alert
        if TELEGRAM_ENABLED:
            _send_telegram_alert(platform, diagnosis, error_rate)

        return diagnosis


# Singleton
monitor = HealthMonitor()


# ---------------------------------------------------------------------------
# AI diagnosis
# ---------------------------------------------------------------------------

def _diagnose_with_ai(platform: str, error_summary: str, error_rate: float) -> Optional[dict]:
    """Use Gemini/OpenAI to analyze error patterns."""
    try:
        from app.services.llm_client import call_llm

        prompt = f"""Bạn là kỹ sư backend chuyên về video extraction. Phân tích lỗi sau:

Platform: {platform}
Error rate: {error_rate:.1f}% (trong {WINDOW_MINUTES} phút gần nhất)

Các mẫu lỗi:
{error_summary}

Trả lời ngắn gọn theo JSON:
{{
  "root_cause": "nguyên nhân gốc (1-2 câu)",
  "severity": "low|medium|high|critical",
  "should_reroute": true/false,
  "suggested_route": "cobalt|browser_direct|proxy_pool|proxy_cn",
  "fix_suggestion": "gợi ý sửa (1-2 câu)",
  "auto_recoverable": true/false
}}"""

        result = call_llm(prompt, max_output_tokens=512)
        if not result:
            return None

        # Parse JSON from LLM response
        import json
        import re
        json_match = re.search(r'\{[^{}]+\}', result, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            parsed["source"] = "ai"
            return parsed

    except Exception as e:
        logger.warning("self_heal.ai_diagnosis_failed: %s", e)

    return None


_COOKIE_OR_RATE_CODES = (
    "cookie_expired",
    "session_expired",
    "cookie_required",
    "login_required",
    "private_or_login_required",
    "rate_limited",
    "too_many_requests",
    "rate_limited_429",
    "soft_block",
)


def _diagnose_heuristic(platform: str, errors: list[ErrorSample]) -> dict:
    """Fallback heuristic diagnosis when AI is unavailable."""
    error_codes = [e.error_code for e in errors]
    http_statuses = [e.http_status for e in errors]

    # Pattern matching
    if any(c in error_codes for c in ("geo_restricted", "country_blocked")):
        return {
            "source": "heuristic",
            "root_cause": f"{platform} đang chặn geo/IP",
            "severity": "high",
            "should_reroute": True,
            "suggested_route": "proxy_pool",
            "fix_suggestion": "Chuyển sang proxy pool hoặc VPN",
            "auto_recoverable": True,
        }

    if any(c in error_codes for c in _COOKIE_OR_RATE_CODES):
        has_cookie_err = any(
            c in error_codes
            for c in ("cookie_expired", "session_expired", "cookie_required", "login_required", "private_or_login_required")
        )
        preferred = (
            "cobalt"
            if (platform in ("instagram", "twitter") or "cobalt" in FALLBACK_ROUTES.get(platform, []))
            else FALLBACK_ROUTES.get(platform, ["proxy_pool"])[0]
        )
        return {
            "source": "heuristic",
            "root_cause": (
                f"{platform} hết hạn cookie hoặc cần làm mới phiên đăng nhập"
                if has_cookie_err
                else f"{platform} đang rate-limit server"
            ),
            "severity": "high" if has_cookie_err else "medium",
            "should_reroute": True,
            "suggested_route": preferred,
            "fix_suggestion": "Làm mới cookie trong Cookie Pool hoặc chuyển sang Cobalt fallback",
            "auto_recoverable": True,
        }

    if any(s == 403 for s in http_statuses):
        return {
            "source": "heuristic",
            "root_cause": f"{platform} trả 403 Forbidden — có thể đổi API/token",
            "severity": "high",
            "should_reroute": True,
            "suggested_route": "browser_direct",
            "fix_suggestion": "Kiểm tra cookies/token, thử browser direct extraction",
            "auto_recoverable": False,
        }

    if any(s >= 500 for s in http_statuses):
        return {
            "source": "heuristic",
            "root_cause": f"{platform} server đang lỗi (5xx)",
            "severity": "medium",
            "should_reroute": True,
            "suggested_route": FALLBACK_ROUTES.get(platform, ["proxy_pool"])[0],
            "fix_suggestion": "Server nguồn đang có vấn đề, chờ tự phục hồi",
            "auto_recoverable": True,
        }

    return {
        "source": "heuristic",
        "root_cause": f"Lỗi không xác định trên {platform}",
        "severity": "medium",
        "should_reroute": len(errors) >= MIN_SAMPLES * 2,
        "suggested_route": FALLBACK_ROUTES.get(platform, ["proxy_pool"])[0],
        "fix_suggestion": "Cần kiểm tra thủ công",
        "auto_recoverable": False,
    }


# ---------------------------------------------------------------------------
# Telegram alerts
# ---------------------------------------------------------------------------

def _send_telegram_alert(platform: str, diagnosis: dict, error_rate: float) -> None:
    """Send diagnosis alert to admin Telegram."""
    try:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            return

        severity_emoji = {
            "low": "🟡", "medium": "🟠", "high": "🔴", "critical": "🚨",
        }
        emoji = severity_emoji.get(diagnosis.get("severity", "medium"), "🟠")
        rerouted = f"\n🔄 Đã chuyển sang: {diagnosis['rerouted_to']}" if diagnosis.get("rerouted_to") else ""

        message = (
            f"{emoji} *Self-Healing Alert*\n\n"
            f"📡 Platform: `{platform}`\n"
            f"📊 Error rate: `{error_rate:.1f}%`\n"
            f"🔍 Nguyên nhân: {diagnosis.get('root_cause', 'N/A')}\n"
            f"💡 Gợi ý: {diagnosis.get('fix_suggestion', 'N/A')}\n"
            f"🤖 Phân tích bởi: {diagnosis.get('source', 'unknown')}"
            f"{rerouted}"
        )

        import httpx
        httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logger.warning("self_heal.telegram_alert_failed: %s", e)
