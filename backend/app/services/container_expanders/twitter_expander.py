"""
Twitter/X Container Expander — Phase 25 PR2 (stub)
=====================================================
Twitter requires authenticated cookies or API credentials.
This expander handles the cookie_required case and provides the
framework hook for PR3 to wire real extraction.

Supported source types (when credentials available):
  - profile → user timeline media
  - thread  → thread expansion

Current status: cookie_required
"""

from __future__ import annotations

import re
import time
from typing import Optional

from app.services.container_discovery import (
    ContainerMeta, ContainerItem, make_container_id,
)
from app.services.container_expanders import BaseExpander


class TwitterExpander(BaseExpander):
    platform = "twitter"
    supported_types = ["profile", "thread"]

    def discover(
        self,
        url: str,
        max_items: int = 50,
        include_sections: Optional[list[str]] = None,  # noqa: ARG002
    ) -> ContainerMeta:
        cid = make_container_id()
        username = self._extract_username(url)

        if not self._has_cookies():
            return ContainerMeta(
                container_id=cid,
                platform="twitter",
                container_type="profile",
                url=url,
                title=f"@{username}" if username else "Twitter/X Profile",
                status="failed",
                error_code="C010",
                error_message=(
                    "Twitter/X yêu cầu cookie đăng nhập. "
                    "Cấu hình cookie trong Cài đặt → Cookie → Twitter."
                ),
                support_level="cookie_required",
                discovered_at=time.time(),
            )

        try:
            return self._discover_with_cookies(url, username, max_items)
        except Exception as e:
            return ContainerMeta(
                container_id=cid,
                platform="twitter",
                container_type="profile",
                url=url,
                title=f"@{username}" if username else "Twitter/X Profile",
                status="failed",
                error_code="C001",
                error_message=str(e)[:200],
                support_level="cookie_required",
                discovered_at=time.time(),
            )

    def expand_section(
        self,
        url: str,
        _section_key: str,
        _child_id: str = "",
    ) -> list[ContainerItem]:
        if not self._has_cookies():
            return []
        try:
            return self._fetch_media(url, 200)
        except Exception:
            return []

    def _extract_username(self, url: str) -> str:
        m = re.search(r"(?:twitter|x)\.com/([^/?#/]+)", url, re.IGNORECASE)
        if m and m.group(1) not in ("i", "home", "search", "explore", "notifications"):
            return m.group(1)
        return ""

    def _has_cookies(self) -> bool:
        try:
            from app.core.cookie_manager import has_cookies_for
            return has_cookies_for("twitter")
        except Exception:
            return False

    def _get_cookie_opts(self) -> dict:
        try:
            from app.core.cookie_manager import get_cookie_file
            cf = get_cookie_file("twitter")
            return {"cookiefile": cf} if cf else {}
        except Exception:
            return {}

    def _discover_with_cookies(
        self, url: str, username: str, max_items: int
    ) -> ContainerMeta:
        from app.services.container_discovery import ContainerSection
        items = self._fetch_media(url, max_items)
        cid = make_container_id()

        section = ContainerSection(
            key="media",
            label=f"Media ({len(items)})",
            item_count=len(items),
            items_loaded=True,
            items=items,
        )

        return ContainerMeta(
            container_id=cid,
            platform="twitter",
            container_type="profile",
            url=url,
            title=f"@{username}" if username else "Twitter/X",
            item_count=len(items),
            sections=[section] if items else [],
            status="ready" if items else "partial",
            discovered_at=time.time(),
            support_level="cookie_required",
            warning="Twitter/X — chỉ khả dụng khi có cookie đăng nhập.",
        )

    def _fetch_media(self, url: str, limit: int) -> list[ContainerItem]:
        import yt_dlp
        opts = {
            **self._get_cookie_opts(),
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "ignoreerrors": True,
            "playlistend": limit,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return []
        username = info.get("uploader", "")
        entries = [e for e in (info.get("entries") or []) if e and e.get("url")]
        return [
            ContainerItem(
                id=f"tw:{e.get('id', '')}",
                url=e.get("url") or e.get("webpage_url", ""),
                title=(e.get("title") or "")[:100] or "Tweet media",
                author=username,
                thumbnail=e.get("thumbnail", ""),
                duration_ms=int(e.get("duration") or 0) * 1000,
                media_type="video",
            )
            for e in entries
        ]
