"""
Celery tasks for webhook delivery with retry.
"""

import hashlib
import hmac
import json
import time

import requests

from app.core.celery_app import celery_app


@celery_app.task(name="deliver_webhook_task", bind=True, max_retries=3)
def deliver_webhook_task(self, user_id: str, payload: dict):
    """POST payload to user's webhook URL with HMAC-SHA256 signature. Retry 3x."""
    from app.core.database import get_service_client
    from app.core.redis_client import get_redis

    supabase = get_service_client()

    # Get webhook config
    try:
        res = (
            supabase.table("user_webhooks")
            .select("webhook_url, secret_hash, is_active")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        webhook = res.data
    except Exception:
        return  # No webhook registered, skip silently

    if not webhook or not webhook.get("is_active"):
        return

    webhook_url = webhook["webhook_url"]
    secret_hash = webhook["secret_hash"]  # SHA-256 hash stored at registration

    # Sign payload using the stored hash as the HMAC key (documented to users)
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode()
    signature = hmac.new(
        secret_hash.encode(), payload_bytes, hashlib.sha256
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-VidGrab-Signature": f"sha256={signature}",
        "X-VidGrab-Event": "job.completed",
        "User-Agent": "VidGrab-Webhook/1.0",
    }

    status_code = 0
    success = False
    error_message = None

    try:
        resp = requests.post(
            webhook_url, data=payload_bytes, headers=headers, timeout=15
        )
        status_code = resp.status_code
        success = 200 <= status_code < 300
        if not success:
            error_message = f"HTTP {status_code}"
    except Exception as e:
        error_message = str(e)[:200]

    # Log delivery to Redis (keep last 50 entries)
    log_entry = json.dumps(
        {
            "job_id": payload.get("job_id"),
            "status_code": status_code,
            "success": success,
            "error": error_message,
            "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "webhook_url": webhook_url[:60],
        }
    )

    try:
        rc = get_redis()
        list_key = f"webhook:logs:{user_id}"
        rc.lpush(list_key, log_entry)
        rc.ltrim(list_key, 0, 49)          # Keep last 50
        rc.expire(list_key, 86400 * 30)    # TTL: 30 days
    except Exception:
        pass

    # Retry on failure with exponential backoff: 30s, 60s, 120s
    if not success:
        backoff = (2 ** self.request.retries) * 30
        print(
            f"[Webhook] Delivery failed ({error_message}). "
            f"Retry {self.request.retries + 1}/3 in {backoff}s"
        )
        raise self.retry(
            countdown=backoff,
            exc=Exception(error_message or "Webhook delivery failed"),
        )
