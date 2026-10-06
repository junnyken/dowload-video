"""
Bilibili adapter — SKELETON. Registered but NOT routed in wave 1 (owner
correction #5): the native flow (app/services/bilibili_extractor.py) works
(probe ok) and must not be affected (plan §21.12).
"""
from __future__ import annotations

import re

from app.services.china_platforms.adapters.base import PlatformAdapter


class BilibiliAdapter(PlatformAdapter):
    platform = "bilibili"
    host_pattern = re.compile(r"(?:^|[/.])(?:bilibili\.com|b23\.tv)(?:[/:?#]|$)", re.IGNORECASE)
