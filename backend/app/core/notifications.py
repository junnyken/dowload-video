import os
import httpx
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

async def send_telegram_alert(message: str) -> bool:
    """Send Telegram alert message to admin."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
        
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient() as client:
            await client.post(
                url, 
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
            )
        return True
    except Exception as e:
        print(f"[Alert] Telegram push failed: {e}")
        return False
