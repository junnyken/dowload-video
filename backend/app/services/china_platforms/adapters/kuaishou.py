"""
Kuaishou adapter — SKELETON (registry only in wave 1, not routed).

The existing extractor (app/services/kuaishou_extractor.py) stays behind its
own KUAISHOU_ENABLED flag and is not touched. A managed Kuaishou provider is
wave 2, after actor research.
"""
from __future__ import annotations

import re

from app.services.china_platforms.adapters.base import PlatformAdapter


class KuaishouAdapter(PlatformAdapter):
    platform = "kuaishou"
    host_pattern = re.compile(
        r"(?:^|[/.])(?:kuaishou\.(?:com|cn)|gifshow\.com|chenzhongtech\.(?:com|cn))(?:[/:?#]|$)",
        re.IGNORECASE,
    )
