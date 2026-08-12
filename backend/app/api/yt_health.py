"""YouTube Extraction Health API — Fix 2, 3, 6 admin endpoints."""
import json
import time
from fastapi import APIRouter, Depends
from app.core.redis_client import get_redis
from app.api.admin import verify_admin

router = APIRouter()


@router.get("/youtube-health")
async def get_youtube_health(_=Depends(verify_admin)):
    """Return YouTube extraction health snapshot for admin dashboard."""
    rc = get_redis()

    # Health snapshot from youtube_extraction_health_probe
    snapshot = {}
    try:
        raw = rc.get("yt:health:snapshot")
        if raw:
            snapshot = json.loads(raw)
    except Exception:
        pass

    # bgutil health
    from app.core.po_token_cache import get_bgutil_health_status
    bgutil = get_bgutil_health_status()

    # Oracle circuit breaker
    from app.core.oracle_circuit_breaker import oracle_circuit
    circuit = oracle_circuit.get_status()

    # android_vr flag
    avr_raw = rc.get("yt_android_vr_healthy")
    avr_healthy = avr_raw not in (b"0", "0") if avr_raw is not None else None

    # Cobalt flag
    cobalt_raw = rc.get("cobalt_healthy")
    cobalt_healthy = cobalt_raw not in (b"0", "0") if cobalt_raw is not None else None

    now = time.time()

    def _ago(ts):
        if not ts:
            return None
        diff = int(now - float(ts))
        if diff < 60:
            return f"{diff}s ago"
        if diff < 3600:
            return f"{diff // 60}m ago"
        return f"{diff // 3600}h ago"

    snap_ts = snapshot.get("timestamp", 0)

    return {
        "layers": [
            {
                "name": "android_vr",
                "healthy": avr_healthy if avr_healthy is not None else snapshot.get("android_vr"),
                "last_checked": _ago(snap_ts) if snap_ts else "never",
            },
            {
                "name": "bgutil-pot",
                "healthy": bgutil.get("healthy"),
                "last_checked": _ago(bgutil.get("last_healthy_at")),
            },
            {
                "name": "Cobalt API",
                "healthy": cobalt_healthy if cobalt_healthy is not None else snapshot.get("cobalt"),
                "last_checked": _ago(snap_ts) if snap_ts else "never",
            },
        ],
        "oracle_circuit": {
            "state": circuit.get("state", "unknown"),
            "fail_count": circuit.get("fail_count", 0),
            "seconds_until_half": circuit.get("seconds_until_half", 0),
        },
        "snapshot_age_s": int(now - snap_ts) if snap_ts else None,
        "all_healthy": (
            bgutil.get("healthy", True)
            and (avr_healthy is not False)
            and (cobalt_healthy is not False)
        ),
    }
