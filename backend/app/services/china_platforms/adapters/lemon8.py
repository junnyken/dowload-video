"""
Lemon8 adapter — SKELETON (registry only in wave 1, not routed).

Native extraction stays in app/services/lemon8_extractor.py.
"""
from __future__ import annotations

import re

from app.services.china_platforms.adapters.base import PlatformAdapter


class Lemon8Adapter(PlatformAdapter):
    platform = "lemon8"
    host_pattern = re.compile(r"(?:^|[/.])lemon8-app\.com(?:[/:?#]|$)", re.IGNORECASE)
