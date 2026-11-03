"""
Payments Auto — công tắc thanh toán tự động.

Khi TẮT: khách không thấy checkout tự động; endpoint công khai mới 404;
          webhook vẫn nhận để xử lý đơn đã tạo.
Khi BẬT: khách thấy VietQR/PayPal checkout, tạo đơn, nhận QR, tự nâng gói.
"""
from __future__ import annotations

import os

SWITCH_KEY = "payments_auto"


def enabled() -> bool:
    """Công tắc thanh toán tự động đang BẬT?"""
    from app.core.admin_switches import resolve  # noqa: PLC0415

    env = os.getenv("PAYMENTS_AUTO_ENABLED", "").strip().lower() in ("1", "true", "on")
    return resolve(SWITCH_KEY, env)


def visible(request=None) -> bool:
    """Hiện checkout cho user? = enabled() HOẶC admin đang xem thử."""
    if enabled():
        return True
    if request is None:
        return False
    # Admin preview: X-Admin-Token or admin session
    try:
        from app.core.quotas import is_admin_request  # noqa: PLC0415
        return is_admin_request(request)
    except Exception:  # noqa: BLE001
        return False
