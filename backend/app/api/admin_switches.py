"""
Admin — emergency switches ("Công tắc khẩn cấp", task #6332 follow-up)
======================================================================
Mounted under /api/v1/admin, every route requires verify_admin, every POST is
audited with log_admin_action (who/what/old/new).

  GET  /admin/switches          every switch: key, label, effective state,
                                source (admin/env/default), last change at/by
  POST /admin/switches/{key}    {"state": "on"|"off"|"clear"}

Keys
  watch_kill_switch      on = Channel Watch stopped (override of WATCH_KILL_SWITCH)
  desktop_updater        on = in-app updates offered (override of DESKTOP_UPDATER_ENABLED)
  china_global           on = every China platform closed (china:killswitch:global;
                         env CHINA_ACCESS_GLOBAL_KILL_SWITCH forces on, cannot be overridden)
  china_<platform>       on = that platform closed (china:killswitch:platform:<p>)

For the china_* keys "off" and "clear" are the same (delete the Redis key):
that store has no separate "admin said off" value.
"""
from __future__ import annotations

import os
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.admin import verify_admin
from app.core import admin_switches as sw
from app.core import desktop_release, watch_config
from app.core.audit import log_admin_action
from app.services.china_platforms import budget_guard, settings as china_settings
from app.services.china_platforms.policy import CHINA_PLATFORM_POLICIES

router = APIRouter()

CHINA_PREFIX = "china_"


class SwitchBody(BaseModel):
    state: Literal["on", "off", "clear"]


def _who(request: Request) -> str:
    from app.core.client_ip import get_client_ip  # noqa: PLC0415
    try:
        ip = get_client_ip(request)
    except Exception:  # noqa: BLE001
        ip = None
    return f"admin ({ip})" if ip else "admin"


# ── per-switch views ────────────────────────────────────────────────────────

def _overridable_view(key: str) -> dict:
    if key == sw.WATCH_KILL:
        env_val = watch_config.env_kill_switch()
        env_set = bool((os.getenv("WATCH_KILL_SWITCH") or "").strip())
        meta = {
            "label": "Dừng khẩn cấp Theo dõi kênh",
            "on_label": "Đang DỪNG", "off_label": "Đang chạy",
            "danger_state": "on",
            "description": ("Bật = ngừng mọi lượt quét kênh, ngừng gửi thông báo video mới và không cho thêm kênh mới. "
                            "Danh sách kênh đã theo dõi vẫn xem và xoá được."),
            "user_impact": ("Người dùng thấy Theo dõi kênh tạm ngừng, nút thêm kênh bị khoá; "
                            "không nhận thông báo video mới cho tới khi tắt công tắc."),
            "env_name": "WATCH_KILL_SWITCH",
        }
    else:
        env_val = desktop_release.env_updater_enabled()
        env_set = bool((os.getenv("DESKTOP_UPDATER_ENABLED") or "").strip())
        meta = {
            "label": "Tự cập nhật app Windows",
            "on_label": "Đang mời cập nhật", "off_label": "Đã NGỪNG mời cập nhật",
            "danger_state": "off",
            "description": ("Tắt = máy chủ không mời app Windows cập nhật lên bản mới nữa. "
                            "Dùng khi vừa phát hành một bản lỗi và cần chặn lan rộng."),
            "user_impact": ("App đang chạy giữ nguyên bản hiện tại, không hiện lời mời cập nhật. "
                            "Máy đã cập nhật lên bản lỗi rồi thì không tự quay về."),
            "env_name": "DESKTOP_UPDATER_ENABLED",
        }
    try:
        ov = sw.override_strict(key)
        readable = True
    except Exception:  # noqa: BLE001
        ov, readable = None, False
    effective = env_val if ov is None else ov
    source = "admin" if ov is not None else ("env" if env_set else "default")
    return {"key": key, **meta, "kind": "override", "state": "on" if effective else "off",
            "source": source, "env_state": "on" if env_val else "off",
            "override": None if ov is None else ("on" if ov else "off"),
            "readable": readable, "env_forced": False, "last_change": sw.read_meta(key)}


def _china_scope(key: str) -> Optional[str]:
    rest = key[len(CHINA_PREFIX):]
    if rest == "global":
        return "global"
    return rest if rest in CHINA_PLATFORM_POLICIES else None


def _china_view(key: str) -> dict:
    scope = _china_scope(key)
    env_forced = scope == "global" and china_settings.env_global_killswitch()
    try:
        if scope == "global":
            redis_on = bool(budget_guard._r().get(budget_guard.GLOBAL_KILL_KEY))
        else:
            redis_on = budget_guard.platform_killswitch_on(scope)
        readable = True
    except Exception:  # noqa: BLE001
        redis_on, readable = False, False
    effective = env_forced or redis_on
    source = "env" if env_forced else ("admin" if redis_on else "default")
    if scope == "global":
        label = "Đóng toàn bộ tải từ nền tảng Trung Quốc"
        desc = ("Bật = ngừng mọi lượt tải trả phí qua lớp truy cập Trung Quốc (Douyin, Kuaishou, "
                "Xiaohongshu, Bilibili). Yêu cầu quay về đường tải miễn phí cũ, không tốn tiền Apify.")
    else:
        label = f"Đóng tải {scope.capitalize()}"
        desc = (f"Bật = ngừng tải {scope.capitalize()} qua lớp truy cập Trung Quốc; "
                "các nền tảng khác không bị ảnh hưởng.")
    return {"key": key, "label": label, "on_label": "Đang ĐÓNG", "off_label": "Đang mở",
            "danger_state": "on", "description": desc,
            "user_impact": "Link của nền tảng này có thể tải chậm hơn hoặc báo không tải được.",
            "env_name": "CHINA_ACCESS_GLOBAL_KILL_SWITCH" if scope == "global" else None,
            "kind": "china", "state": "on" if effective else "off", "source": source,
            "env_state": ("on" if env_forced else "off") if scope == "global" else None,
            "override": "on" if redis_on else None, "readable": readable,
            "env_forced": env_forced, "last_change": sw.read_meta(key)}


def _view(key: str) -> dict:
    if key in sw.OVERRIDABLE:
        return _overridable_view(key)
    if key.startswith(CHINA_PREFIX) and _china_scope(key):
        return _china_view(key)
    raise HTTPException(status_code=404, detail="unknown switch")


def all_keys() -> list[str]:
    return [sw.WATCH_KILL, sw.DESKTOP_UPDATER, "china_global",
            *(f"{CHINA_PREFIX}{p}" for p in CHINA_PLATFORM_POLICIES)]


# ── routes ──────────────────────────────────────────────────────────────────

@router.get("/switches")
async def list_switches(_=Depends(verify_admin)) -> dict:
    return {"switches": [_view(k) for k in all_keys()]}


@router.post("/switches/{key}")
async def set_switch(key: str, body: SwitchBody, request: Request, _=Depends(verify_admin)) -> dict:
    prior = _view(key)  # 404 on unknown key
    who = _who(request)
    try:
        if prior["kind"] == "override":
            sw.set_override(key, body.state, who, prior["state"], None)
        else:
            scope = _china_scope(key)
            budget_guard.set_killswitch(scope, body.state == "on")
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="redis unavailable")
    new = _view(key)
    try:
        sw.write_meta(key, body.state, who, prior["state"], new["state"])
    except Exception:  # noqa: BLE001
        pass
    new["last_change"] = sw.read_meta(key)
    log_admin_action(request, "admin.switch.set", resource_type="switch", resource_id=key,
                     metadata={"by": who, "requested": body.state,
                               "old": {"state": prior["state"], "source": prior["source"]},
                               "new": {"state": new["state"], "source": new["source"]}})
    return {"switch": new, "prior_state": prior["state"]}
