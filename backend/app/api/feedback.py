"""Feedback API — receives user feedback and notifies via Telegram + optional email."""
import os
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["Feedback"])


class FeedbackRequest(BaseModel):
    name: Optional[str] = ""
    email: Optional[str] = ""
    content: str
    page: Optional[str] = ""


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest, request: Request):
    """Accept user feedback and notify via Telegram (+ SMTP if configured)."""
    import httpx

    ip = request.client.host if request.client else "unknown"
    name_str = req.name or "Ẩn danh"
    email_str = req.email or ""

    lines = [
        "📬 *Góp ý mới — VidGrab*",
        f"👤 {name_str}" + (f"  |  📧 {email_str}" if email_str else ""),
        "",
        f"{req.content}",
    ]
    if req.page:
        lines.append(f"\n📄 Trang: `{req.page}`")
    lines.append(f"🌐 IP: `{ip}`")
    tg_msg = "\n".join(lines)

    # ── Telegram ────────────────────────────────────────────────────────
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID", "")
    if bot_token and chat_id:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": tg_msg, "parse_mode": "Markdown"},
                )
        except Exception as e:
            print(f"[Feedback] Telegram error: {e}")

    # ── SMTP email (optional — set FEEDBACK_SMTP_USER + FEEDBACK_SMTP_PASS) ──
    smtp_user = os.getenv("FEEDBACK_SMTP_USER", "")
    smtp_pass = os.getenv("FEEDBACK_SMTP_PASS", "")
    to_email  = os.getenv("FEEDBACK_EMAIL_TO", "thientrieu753@gmail.com")
    if smtp_user and smtp_pass:
        try:
            import smtplib
            from email.mime.text import MIMEText
            plain = "\n".join(lines).replace("*", "").replace("`", "")
            msg = MIMEText(plain, "plain", "utf-8")
            msg["Subject"] = f"[VidGrab] Góp ý từ {name_str}"
            msg["From"]    = smtp_user
            msg["To"]      = to_email
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                smtp.login(smtp_user, smtp_pass)
                smtp.send_message(msg)
        except Exception as e:
            print(f"[Feedback] SMTP error: {e}")

    return {"success": True, "message": "Cảm ơn bạn đã góp ý!"}
