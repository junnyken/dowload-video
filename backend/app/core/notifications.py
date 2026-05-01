"""
Telegram Notification Service
===============================
Full-featured Telegram notifications for VidGrab operations:
  • Batch completion alerts (ZIP ready)
  • Job failure alerts
  • API credits low-balance warnings
  • Daily summary reports
  • System health alerts

Both async (for FastAPI) and sync (for Celery tasks) interfaces are provided.

Usage:
  # In async context (FastAPI routes):
  from app.core.notifications import notify_batch_complete
  await notify_batch_complete(batch_id, total_files, zip_size_mb)

  # In sync context (Celery tasks):
  from app.core.notifications import notify_batch_complete_sync
  notify_batch_complete_sync(batch_id, total_files, zip_size_mb)
"""

import os
import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# Thresholds for credit alerts
CREDITS_WARNING_THRESHOLD: int = 50    # Cảnh báo khi credits < 50
CREDITS_CRITICAL_THRESHOLD: int = 10   # Cảnh báo nghiêm trọng khi credits < 10

# Rate limiting: prevent spamming the same alert
_last_credit_alert_time: dict = {}
CREDIT_ALERT_COOLDOWN_SECONDS: int = 3600  # 1 giờ giữa các lần cảnh báo credit


def _is_configured() -> bool:
    """Check if Telegram credentials are configured."""
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def _get_timestamp() -> str:
    """Get current timestamp formatted for Vietnamese timezone (UTC+7)."""
    from datetime import timedelta
    vn_time = datetime.now(timezone.utc) + timedelta(hours=7)
    return vn_time.strftime("%d/%m/%Y %H:%M:%S")


# ═════════════════════════════════════════════════════════════════════
# CORE: Send Message (Async + Sync)
# ═════════════════════════════════════════════════════════════════════

async def send_telegram_message(message: str, parse_mode: str = "HTML") -> bool:
    """
    Send a message to the configured Telegram chat.

    Args:
        message:    The message text (supports HTML or Markdown formatting)
        parse_mode: 'HTML' or 'Markdown' (default: HTML for safer formatting)

    Returns:
        True if sent successfully, False otherwise
    """
    if not _is_configured():
        print("[Telegram] ⚠ Bot token or chat ID not configured, skipping.")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            else:
                print(f"[Telegram] ⚠ API error: {resp.status_code} — {resp.text[:200]}")
                return False

    except httpx.TimeoutException:
        print("[Telegram] ⚠ Request timed out")
        return False
    except Exception as e:
        print(f"[Telegram] ⚠ Send failed: {e}")
        return False


def send_telegram_message_sync(message: str, parse_mode: str = "HTML") -> bool:
    """
    Synchronous wrapper for Celery tasks and other sync contexts.
    Uses httpx synchronous client instead of asyncio.run() to avoid
    event loop conflicts in Celery workers.
    """
    if not _is_configured():
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            else:
                print(f"[Telegram] ⚠ Sync API error: {resp.status_code}")
                return False

    except Exception as e:
        print(f"[Telegram] ⚠ Sync send failed: {e}")
        return False


# ── Legacy alias (backward compatibility) ────────────────────────────
async def send_telegram_alert(message: str) -> bool:
    """Legacy alias — kept for backward compatibility."""
    return await send_telegram_message(message, parse_mode="Markdown")


# ═════════════════════════════════════════════════════════════════════
# NOTIFICATION TYPES: Batch Events
# ═════════════════════════════════════════════════════════════════════

async def notify_batch_complete(
    batch_id: str,
    total_files: int,
    zip_size_mb: float,
    success_count: int = 0,
    failed_count: int = 0,
) -> bool:
    """
    Notify admin when a batch ZIP is ready for download.

    Args:
        batch_id:      The batch UUID
        total_files:   Number of files in the ZIP
        zip_size_mb:   Size of the ZIP in MB
        success_count: Number of successful downloads
        failed_count:  Number of failed downloads
    """
    timestamp = _get_timestamp()
    short_id = batch_id[:8]

    message = (
        f"📦 <b>Batch Hoàn Tất</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Batch: <code>{short_id}</code>\n"
        f"📁 Tổng files: <b>{total_files}</b>\n"
        f"💾 Kích thước ZIP: <b>{zip_size_mb:.1f} MB</b>\n"
        f"✅ Thành công: {success_count}\n"
        f"❌ Thất bại: {failed_count}\n"
        f"🕐 {timestamp}"
    )

    return await send_telegram_message(message)


def notify_batch_complete_sync(
    batch_id: str,
    total_files: int,
    zip_size_mb: float,
    success_count: int = 0,
    failed_count: int = 0,
) -> bool:
    """Sync version of notify_batch_complete for Celery tasks."""
    timestamp = _get_timestamp()
    short_id = batch_id[:8]

    message = (
        f"📦 <b>Batch Hoàn Tất</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Batch: <code>{short_id}</code>\n"
        f"📁 Tổng files: <b>{total_files}</b>\n"
        f"💾 Kích thước ZIP: <b>{zip_size_mb:.1f} MB</b>\n"
        f"✅ Thành công: {success_count}\n"
        f"❌ Thất bại: {failed_count}\n"
        f"🕐 {timestamp}"
    )

    return send_telegram_message_sync(message)


# ═════════════════════════════════════════════════════════════════════
# NOTIFICATION TYPES: Job Failures
# ═════════════════════════════════════════════════════════════════════

async def notify_job_failed(
    job_id: str,
    url: str,
    error_message: str,
) -> bool:
    """Notify admin when a download job fails."""
    timestamp = _get_timestamp()

    # Truncate URL and error for readability
    short_url = url[:80] + "..." if len(url) > 80 else url
    short_error = error_message[:200] if error_message else "Unknown error"

    message = (
        f"🚨 <b>Job Thất Bại</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 URL: {short_url}\n"
        f"💥 Lỗi: {short_error}\n"
        f"🕐 {timestamp}"
    )

    return await send_telegram_message(message)


def notify_job_failed_sync(
    job_id: str,
    url: str,
    error_message: str,
) -> bool:
    """Sync version of notify_job_failed for Celery tasks."""
    timestamp = _get_timestamp()

    short_url = url[:80] + "..." if len(url) > 80 else url
    short_error = error_message[:200] if error_message else "Unknown error"

    message = (
        f"🚨 <b>Job Thất Bại</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 URL: {short_url}\n"
        f"💥 Lỗi: {short_error}\n"
        f"🕐 {timestamp}"
    )

    return send_telegram_message_sync(message)


# ═════════════════════════════════════════════════════════════════════
# NOTIFICATION TYPES: API Credits Warning
# ═════════════════════════════════════════════════════════════════════

def notify_credits_low_sync(
    provider: str,
    remaining: int,
) -> bool:
    """
    Alert admin when API credits fall below threshold.
    Includes cooldown to prevent spam (max 1 alert per hour per provider).
    """
    global _last_credit_alert_time

    # Cooldown check: don't spam
    now = datetime.now(timezone.utc).timestamp()
    last_sent = _last_credit_alert_time.get(provider, 0)
    if now - last_sent < CREDIT_ALERT_COOLDOWN_SECONDS:
        return False  # Within cooldown, skip

    timestamp = _get_timestamp()

    # Determine severity
    if remaining <= CREDITS_CRITICAL_THRESHOLD:
        severity = "🔴 NGHIÊM TRỌNG"
        emoji = "🆘"
    else:
        severity = "🟡 CẢNH BÁO"
        emoji = "⚠️"

    message = (
        f"{emoji} <b>Credits {provider} Sắp Hết</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Mức độ: {severity}\n"
        f"💳 Còn lại: <b>{remaining}</b> credits\n"
        f"🔧 Provider: {provider}\n"
        f"📝 Hành động: Nạp thêm credits hoặc đổi API key\n"
        f"🕐 {timestamp}"
    )

    result = send_telegram_message_sync(message)
    if result:
        _last_credit_alert_time[provider] = now

    return result


async def notify_credits_low(
    provider: str,
    remaining: int,
) -> bool:
    """Async version of credits warning."""
    global _last_credit_alert_time

    now = datetime.now(timezone.utc).timestamp()
    last_sent = _last_credit_alert_time.get(provider, 0)
    if now - last_sent < CREDIT_ALERT_COOLDOWN_SECONDS:
        return False

    timestamp = _get_timestamp()

    if remaining <= CREDITS_CRITICAL_THRESHOLD:
        severity = "🔴 NGHIÊM TRỌNG"
        emoji = "🆘"
    else:
        severity = "🟡 CẢNH BÁO"
        emoji = "⚠️"

    message = (
        f"{emoji} <b>Credits {provider} Sắp Hết</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Mức độ: {severity}\n"
        f"💳 Còn lại: <b>{remaining}</b> credits\n"
        f"🔧 Provider: {provider}\n"
        f"📝 Hành động: Nạp thêm credits hoặc đổi API key\n"
        f"🕐 {timestamp}"
    )

    result = await send_telegram_message(message)
    if result:
        _last_credit_alert_time[provider] = now

    return result


# ═════════════════════════════════════════════════════════════════════
# NOTIFICATION TYPES: Daily Summary Report
# ═════════════════════════════════════════════════════════════════════

def send_daily_summary_sync() -> bool:
    """
    Send a daily summary of system operations to Telegram.
    Called by Celery Beat scheduled task.

    Fetches data from Supabase:
      - Total downloads today
      - Total users
      - Failed job count
      - API credit balances
    """
    try:
        from app.core.database import get_supabase_client
        supabase = get_supabase_client()

        timestamp = _get_timestamp()

        # ── Downloads today ──────────────────────────────
        usage_res = supabase.table("user_usage").select("downloads_today").execute()
        total_downloads = 0
        total_users = 0
        if usage_res.data:
            total_downloads = sum(r.get("downloads_today", 0) for r in usage_res.data)
            total_users = len(usage_res.data)

        # ── Failed jobs today ────────────────────────────
        from datetime import timedelta
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()

        failed_res = (
            supabase.table("download_jobs")
            .select("id")
            .eq("status", "failed")
            .gte("created_at", today_start)
            .execute()
        )
        failed_count = len(failed_res.data) if failed_res.data else 0

        # ── Success jobs today ───────────────────────────
        success_res = (
            supabase.table("download_jobs")
            .select("id")
            .eq("status", "success")
            .gte("created_at", today_start)
            .execute()
        )
        success_count = len(success_res.data) if success_res.data else 0

        # ── Total jobs today ─────────────────────────────
        total_jobs = success_count + failed_count

        # ── Success rate ─────────────────────────────────
        success_rate = (
            round(success_count / total_jobs * 100, 1) if total_jobs > 0 else 100.0
        )

        # ── API Credits ──────────────────────────────────
        credits_info = []
        try:
            import httpx as _httpx

            scraper_key = os.getenv("SCRAPERAPI_API_KEY", os.getenv("SCRAPERAPI_KEY", ""))
            if scraper_key:
                resp = _httpx.get(
                    f"http://api.scraperapi.com/account?api_key={scraper_key}",
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    remaining = data.get("requestLimit", 0) - data.get("requestCount", 0)
                    credits_info.append(f"  • ScraperAPI: <b>{remaining}</b> credits")

                    # Trigger credit alert if low
                    if remaining < CREDITS_WARNING_THRESHOLD:
                        notify_credits_low_sync("ScraperAPI", remaining)
        except Exception:
            credits_info.append("  • ScraperAPI: ❓ Không thể kiểm tra")

        credits_text = "\n".join(credits_info) if credits_info else "  • Không có dữ liệu"

        # ── Health status ────────────────────────────────
        if success_rate >= 90:
            health = "🟢 Tốt"
        elif success_rate >= 70:
            health = "🟡 Trung bình"
        else:
            health = "🔴 Cần kiểm tra"

        # ── Build message ────────────────────────────────
        message = (
            f"📊 <b>Báo Cáo Ngày — VidGrab</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 <b>Người dùng:</b> {total_users}\n"
            f"📥 <b>Tổng downloads:</b> {total_downloads}\n\n"
            f"📋 <b>Jobs hôm nay:</b>\n"
            f"  ✅ Thành công: {success_count}\n"
            f"  ❌ Thất bại: {failed_count}\n"
            f"  📈 Tỷ lệ: {success_rate}%\n\n"
            f"💳 <b>API Credits:</b>\n"
            f"{credits_text}\n\n"
            f"🏥 <b>Sức khỏe hệ thống:</b> {health}\n"
            f"🕐 {timestamp}"
        )

        return send_telegram_message_sync(message)

    except Exception as e:
        print(f"[Telegram] ⚠ Daily summary failed: {e}")
        # Try to send error notification
        try:
            send_telegram_message_sync(
                f"⚠️ <b>Daily Summary Error</b>\n"
                f"Không thể tạo báo cáo ngày:\n"
                f"<code>{str(e)[:200]}</code>"
            )
        except Exception:
            pass
        return False


# ═════════════════════════════════════════════════════════════════════
# NOTIFICATION TYPES: System Startup
# ═════════════════════════════════════════════════════════════════════

async def notify_system_startup() -> bool:
    """Send a notification when the system starts up."""
    timestamp = _get_timestamp()

    message = (
        f"🚀 <b>VidGrab Server Khởi Động</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ FastAPI backend đã sẵn sàng\n"
        f"🕐 {timestamp}"
    )

    return await send_telegram_message(message)


def notify_system_startup_sync() -> bool:
    """Sync version for startup notification."""
    timestamp = _get_timestamp()

    message = (
        f"🚀 <b>VidGrab Server Khởi Động</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ FastAPI backend đã sẵn sàng\n"
        f"🕐 {timestamp}"
    )

    return send_telegram_message_sync(message)
