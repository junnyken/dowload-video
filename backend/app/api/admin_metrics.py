"""
Admin — "Đo lường" (Phase 34C, task #6334). Mounted under /api/v1/admin,
every route requires verify_admin. Figures come from app.services.unit_metrics
(which reads the existing download/cost stores); a figure that the current
data cannot give is returned as null with available=false, never as 0.

  GET  /admin/metrics/success?days=7|30    attempts / successes / success rate /
                                           p50·p95 time to result, per platform per UTC day
  GET  /admin/metrics/costs?days=7|30      cost per platform per day by source,
                                           cost per successful download, top 10
                                           accounts, unattributed bucket, ceiling use
  GET  /admin/metrics/adoption?days=7|30   registrations/day, D1/D7 return, watch
                                           sources per account, inbox, app machines,
                                           extension versions (not recorded)
  GET  /admin/metrics/settings             {weekly_report_enabled}
  POST /admin/metrics/settings             {weekly_report_enabled: bool}   (audited)
  POST /admin/metrics/weekly-report/send   compose + send the weekly Telegram report
                                           once now, whatever the flag     (audited)
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.api.admin import verify_admin
from app.core.audit import log_admin_action
from app.services import unit_metrics

router = APIRouter(prefix="/metrics")


def _days(days: int) -> int:
    if days not in unit_metrics.ALLOWED_DAYS:
        raise HTTPException(status_code=422, detail="days must be 7 or 30")
    return days


@router.get("/success")
async def metrics_success(days: int = Query(7), _=Depends(verify_admin)):
    try:
        return unit_metrics.success_report(_days(days))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"metrics unavailable: {type(exc).__name__}")


@router.get("/costs")
async def metrics_costs(days: int = Query(7), _=Depends(verify_admin)):
    try:
        return unit_metrics.cost_report(_days(days))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"metrics unavailable: {type(exc).__name__}")


@router.get("/adoption")
async def metrics_adoption(days: int = Query(30), _=Depends(verify_admin)):
    return unit_metrics.adoption_report(_days(days))


@router.get("/settings")
async def metrics_settings(_=Depends(verify_admin)):
    return {"weekly_report_enabled": unit_metrics.weekly_report_enabled(),
            "weekly_report_schedule": "Thứ Hai 08:00 (Asia/Ho_Chi_Minh) = 01:00 UTC"}


@router.post("/settings")
async def metrics_settings_set(request: Request, body: dict = Body(...), _=Depends(verify_admin)):
    v = body.get("weekly_report_enabled")
    if not isinstance(v, bool):
        raise HTTPException(status_code=422, detail="weekly_report_enabled must be true or false")
    try:
        unit_metrics.set_weekly_report_enabled(v)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"settings unavailable: {type(exc).__name__}")
    log_admin_action(request, "admin.metrics.weekly_report", resource_type="config",
                     resource_id=unit_metrics.WEEKLY_FLAG, metadata={"enabled": v})
    return {"weekly_report_enabled": v}


@router.post("/weekly-report/send")
async def metrics_weekly_send(request: Request, _=Depends(verify_admin)):
    res = unit_metrics.send_weekly_report(force=True)
    log_admin_action(request, "admin.metrics.weekly_report_sent", resource_type="report",
                     resource_id="weekly", metadata={"sent": bool(res.get("sent"))})
    return res
