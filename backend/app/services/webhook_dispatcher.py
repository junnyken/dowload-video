"""
Webhook Dispatcher
==================
Delivers HMAC-signed webhook events to registered tenant endpoints.

Security: HMAC-SHA256 signature in X-VidGrab-Signature-256 header.
Retry:    exponential backoff — 0 s, 30 s, 5 min, 30 min, 2 h (5 attempts max).
          Attempts beyond the schedule mark the delivery as 'abandoned'.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.celery_app import celery_app
from app.core.database import get_service_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RETRY_DELAYS: list[int] = [0, 30, 300, 1800, 7200]  # seconds per attempt (1-5)
TIMEOUT: float = 10.0                                  # seconds per HTTP attempt
API_VERSION: str = "2024-01"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _sign_payload(secret: str, payload_bytes: bytes) -> str:
    """
    Compute HMAC-SHA256 of *payload_bytes* keyed with *secret*.

    Returns the signature as ``sha256=<hex_digest>``.
    """
    digest = hmac.new(
        secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def _build_payload(
    event_type: str,
    data: dict[str, Any],
    tenant_id: str,
    delivery_id: str,
) -> dict[str, Any]:
    """
    Wrap *data* in the standard VidGrab event envelope.

    Shape::

        {
            "id":          "<delivery_uuid>",
            "event":       "job.completed",
            "api_version": "2024-01",
            "created_at":  "2024-01-15T10:00:00+00:00",
            "data": { ... }
        }
    """
    return {
        "id": delivery_id,
        "event": event_type,
        "api_version": API_VERSION,
        "created_at": _now_iso(),
        "data": data,
    }


# ---------------------------------------------------------------------------
# Core delivery
# ---------------------------------------------------------------------------


async def _deliver_to_endpoint(
    endpoint_row: dict[str, Any],
    payload_bytes: bytes,
    sig: str,
    attempt: int = 1,
) -> bool:
    """
    POST *payload_bytes* to ``endpoint_row["url"]`` with HMAC signature.

    Returns ``True`` on HTTP 2xx, ``False`` otherwise.
    Logs response status at INFO; non-2xx at WARNING.
    """
    delivery_id = endpoint_row.get("_delivery_id", "unknown")
    url = endpoint_row["url"]
    event_type = endpoint_row.get("_event_type", "")

    headers = {
        "Content-Type": "application/json",
        "X-VidGrab-Event": event_type,
        "X-VidGrab-Signature-256": sig,
        "X-VidGrab-Delivery": delivery_id,
        "User-Agent": "VidGrab-Webhooks/1.0",
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(url, content=payload_bytes, headers=headers)

        logger.info(
            "Webhook attempt=%d delivery=%s endpoint=%s status=%d",
            attempt, delivery_id, endpoint_row["id"], resp.status_code,
        )

        if resp.is_success:
            return True

        logger.warning(
            "Webhook non-2xx delivery=%s status=%d body=%.200s",
            delivery_id, resp.status_code, resp.text,
        )
        return False

    except httpx.TimeoutException:
        logger.warning("Webhook timeout delivery=%s url=%s", delivery_id, url)
        return False
    except Exception as exc:
        logger.error("Webhook error delivery=%s: %s", delivery_id, exc, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Main dispatch function
# ---------------------------------------------------------------------------


async def dispatch_event(
    tenant_id: str,
    event_type: str,
    payload_dict: dict[str, Any],
) -> None:
    """
    Query active webhook endpoints for *tenant_id* that subscribe to
    *event_type*, then attempt delivery to each.

    For each endpoint:
    - Create a ``webhook_deliveries`` row with status ``pending``.
    - Attempt immediate delivery (RETRY_DELAYS[0] == 0 → inline).
    - On success: mark ``delivered``, bump counters.
    - On failure: mark ``failed``, schedule Celery retry (attempt 2).
    """
    db = get_service_client()

    # 1. Fetch matching active endpoints
    resp = (
        db.table("webhook_endpoints")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("is_active", True)
        .execute()
    )
    endpoints: list[dict[str, Any]] = resp.data or []

    # Filter: endpoint must subscribe to this event type
    endpoints = [ep for ep in endpoints if event_type in (ep.get("events") or [])]

    if not endpoints:
        logger.debug(
            "No active endpoints for tenant=%s event=%s", tenant_id, event_type
        )
        return

    for endpoint in endpoints:
        delivery_id = str(uuid.uuid4())

        # Enrich endpoint dict so _deliver_to_endpoint can read context
        endpoint["_delivery_id"] = delivery_id
        endpoint["_event_type"] = event_type

        envelope = _build_payload(
            event_type=event_type,
            data=payload_dict,
            tenant_id=tenant_id,
            delivery_id=delivery_id,
        )

        import json as _json
        payload_bytes = _json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        sig = _sign_payload(endpoint["secret"], payload_bytes)

        # 2. Persist delivery row (status=pending)
        db.table("webhook_deliveries").insert(
            {
                "id": delivery_id,
                "endpoint_id": endpoint["id"],
                "tenant_id": tenant_id,
                "event_type": event_type,
                "payload": envelope,
                "attempt_count": 1,
                "last_attempt_at": _now_iso(),
                "status": "pending",
            }
        ).execute()

        # 3. Attempt immediate delivery
        success = await _deliver_to_endpoint(
            endpoint_row=endpoint,
            payload_bytes=payload_bytes,
            sig=sig,
            attempt=1,
        )

        if success:
            _mark_delivery_done(db, endpoint, delivery_id)
        else:
            _mark_delivery_failed(db, endpoint, delivery_id, attempt=1, error=None)
            # Schedule first retry (attempt 2 → delay = RETRY_DELAYS[1])
            retry_webhook_delivery.apply_async(
                args=[delivery_id],
                countdown=RETRY_DELAYS[1],
            )


# ---------------------------------------------------------------------------
# DB helper mutations
# ---------------------------------------------------------------------------


def _mark_delivery_done(
    db,
    endpoint: dict[str, Any],
    delivery_id: str,
    response_status: int = 200,
    response_body: str = "",
) -> None:
    """Update delivery row and endpoint counters on success."""
    db.table("webhook_deliveries").update(
        {
            "status": "delivered",
            "response_status": response_status,
            "response_body": response_body[:2000],
        }
    ).eq("id", delivery_id).execute()

    db.table("webhook_endpoints").update(
        {
            "last_triggered_at": _now_iso(),
            "total_deliveries": endpoint.get("total_deliveries", 0) + 1,
            "successful_deliveries": endpoint.get("successful_deliveries", 0) + 1,
        }
    ).eq("id", endpoint["id"]).execute()


def _mark_delivery_failed(
    db,
    endpoint: dict[str, Any],
    delivery_id: str,
    attempt: int,
    error: str | None,
    response_status: int | None = None,
    response_body: str = "",
    abandoned: bool = False,
) -> None:
    """Update delivery row and endpoint counters on failure."""
    next_attempt = attempt + 1
    next_retry_at = None
    if not abandoned and next_attempt <= len(RETRY_DELAYS):
        from datetime import timedelta
        delay = RETRY_DELAYS[next_attempt - 1]
        next_retry_at = (
            datetime.now(tz=timezone.utc) + timedelta(seconds=delay)
        ).isoformat()

    status = "abandoned" if abandoned else "failed"

    db.table("webhook_deliveries").update(
        {
            "status": status,
            "attempt_count": attempt,
            "last_attempt_at": _now_iso(),
            "next_retry_at": next_retry_at,
            "response_status": response_status,
            "response_body": response_body[:2000] if response_body else None,
            "error_message": (error or "")[:1000] if error else None,
        }
    ).eq("id", delivery_id).execute()

    db.table("webhook_endpoints").update(
        {
            "total_deliveries": endpoint.get("total_deliveries", 0) + 1,
            "failed_deliveries": endpoint.get("failed_deliveries", 0) + 1,
        }
    ).eq("id", endpoint["id"]).execute()


# ---------------------------------------------------------------------------
# Celery retry task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.services.webhook_dispatcher.retry_webhook_delivery",
    bind=True,
    max_retries=None,   # retry logic is self-managed via RETRY_DELAYS
    acks_late=True,
    reject_on_worker_lost=True,
)
def retry_webhook_delivery(self, delivery_id: str) -> None:
    """
    Celery task: re-attempt a failed webhook delivery.

    Reads current attempt_count from DB to determine which delay slot to use.
    Marks delivery 'abandoned' after all RETRY_DELAYS slots are exhausted.
    """
    import asyncio
    import json as _json

    db = get_service_client()

    # Fetch delivery row
    resp = (
        db.table("webhook_deliveries")
        .select("*, webhook_endpoints(*)")
        .eq("id", delivery_id)
        .single()
        .execute()
    )
    delivery: dict[str, Any] | None = resp.data
    if not delivery:
        logger.warning("retry_webhook_delivery: delivery %s not found", delivery_id)
        return

    if delivery["status"] in ("delivered", "abandoned"):
        logger.info(
            "retry_webhook_delivery: delivery %s already %s — skipping",
            delivery_id, delivery["status"],
        )
        return

    endpoint: dict[str, Any] = delivery.get("webhook_endpoints") or {}
    attempt = (delivery.get("attempt_count") or 1) + 1

    if attempt > len(RETRY_DELAYS):
        logger.warning(
            "retry_webhook_delivery: delivery %s exhausted all retries → abandoned",
            delivery_id,
        )
        _mark_delivery_failed(
            db, endpoint, delivery_id, attempt=attempt - 1,
            error="All retry attempts exhausted", abandoned=True,
        )
        return

    # Rebuild payload bytes + sig
    payload_bytes = _json.dumps(
        delivery["payload"], separators=(",", ":")
    ).encode("utf-8")
    sig = _sign_payload(endpoint["secret"], payload_bytes)

    endpoint["_delivery_id"] = delivery_id
    endpoint["_event_type"] = delivery.get("event_type", "")

    success = asyncio.run(
        _deliver_to_endpoint(
            endpoint_row=endpoint,
            payload_bytes=payload_bytes,
            sig=sig,
            attempt=attempt,
        )
    )

    # Refresh endpoint row to get latest counters
    ep_resp = (
        db.table("webhook_endpoints")
        .select("*")
        .eq("id", endpoint["id"])
        .single()
        .execute()
    )
    endpoint = ep_resp.data or endpoint

    if success:
        _mark_delivery_done(db, endpoint, delivery_id)
    else:
        abandoned = attempt >= len(RETRY_DELAYS)
        _mark_delivery_failed(
            db, endpoint, delivery_id,
            attempt=attempt,
            error="HTTP delivery failed",
            abandoned=abandoned,
        )
        if not abandoned:
            next_delay = RETRY_DELAYS[attempt]  # attempt is 1-based index into list
            retry_webhook_delivery.apply_async(
                args=[delivery_id],
                countdown=next_delay,
            )
            logger.info(
                "retry_webhook_delivery: delivery %s scheduled attempt %d in %ds",
                delivery_id, attempt + 1, next_delay,
            )


# ---------------------------------------------------------------------------
# Convenience emit helpers
# ---------------------------------------------------------------------------


async def emit_job_completed(
    job_id: str,
    tenant_id: str,
    result: dict[str, Any],
) -> None:
    """
    Dispatch a ``job.completed`` event.

    *result* should contain download URL, format, size, duration, etc.
    """
    await dispatch_event(
        tenant_id=tenant_id,
        event_type="job.completed",
        payload_dict={
            "job_id": job_id,
            "result": result,
        },
    )


async def emit_job_failed(
    job_id: str,
    tenant_id: str,
    error_code: str,
    error_message: str,
) -> None:
    """Dispatch a ``job.failed`` event."""
    await dispatch_event(
        tenant_id=tenant_id,
        event_type="job.failed",
        payload_dict={
            "job_id": job_id,
            "error_code": error_code,
            "error_message": error_message,
        },
    )


async def emit_batch_completed(
    batch_id: str,
    tenant_id: str,
    total: int,
    succeeded: int,
    failed: int,
) -> None:
    """Dispatch a ``batch.completed`` event."""
    await dispatch_event(
        tenant_id=tenant_id,
        event_type="batch.completed",
        payload_dict={
            "batch_id": batch_id,
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
        },
    )
