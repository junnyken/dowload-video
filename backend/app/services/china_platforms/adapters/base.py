"""
Platform adapter base: knows URLs and how to build that platform's providers.

Adapters wrap existing extractors (compatibility wrappers); they do not
re-implement extraction (plan §4).
"""
from __future__ import annotations

import re
from typing import Dict


class PlatformAdapter:
    platform: str = ""
    host_pattern: re.Pattern = re.compile(r"$^")

    def matches(self, url: str) -> bool:
        return bool(self.host_pattern.search(url or ""))

    def canonicalize(self, url: str) -> str:
        """Sync, no network. Drops the fragment; keeps the query (it may carry
        the media id). Platforms override with an id-based form."""
        return (url or "").strip().split("#", 1)[0]

    async def resolve_canonical(self, url: str) -> str:
        """May follow a short-link redirect (free). Default: canonicalize()."""
        return self.canonicalize(url)

    def build_providers(self) -> Dict[str, object]:
        """Fresh provider instances (one set per resolve, so per-call run
        records never race between concurrent requests)."""
        return {}
