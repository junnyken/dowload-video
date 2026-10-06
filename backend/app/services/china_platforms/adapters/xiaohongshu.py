"""
Xiaohongshu adapter — SKELETON (registry only in wave 1, not routed).

Native extraction stays in app/services/xiaohongshu_extractor.py. Profile /
container work will need an owner-managed session (plan §11.6) — wave 2.
"""
from __future__ import annotations

import re

from app.services.china_platforms.adapters.base import PlatformAdapter


class XiaohongshuAdapter(PlatformAdapter):
    platform = "xiaohongshu"
    host_pattern = re.compile(r"(?:^|[/.])(?:xiaohongshu\.com|xhslink\.com)(?:[/:?#]|$)", re.IGNORECASE)
