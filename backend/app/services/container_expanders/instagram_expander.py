"""
Instagram Container Expander — Phase 25 PR2 (stub)
=====================================================
Instagram requires authenticated cookies for profile scraping.
This stub handles the cookie_required case gracefully and provides
the framework hook for PR3 to wire real extraction.

Supported source types (when cookies available):
  - profile    → user feed / media tab
  - media_tab  → specific tab (reels, photos, videos)

Current status: cookie_required — returns a partial meta with a
clear error so the UI can prompt the user to configure cookies.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from app.services.container_discovery import (
    ContainerMeta, ContainerItem, make_container_id,
)
from app.services.container_expanders import BaseExpander


class InstagramExpander(BaseExpander):
    platform = "instagram"
    supported_types = ["profile", "media_tab"]

    def discover(
        self,
        url: str,
        max_items: int = 50,
        include_sections: Optional[list[str]] = None,  # noqa: ARG002
    ) -> ContainerMeta:
        cid = make_container_id()
        username = self._extract_username(url)

        # Check if cookies are configured
        if not self._has_cookies():
            return ContainerMeta(
                container_id=cid,
                platform="instagram",
                container_type="profile",
                url=url,
                title=f"@{username}" if username else "Instagram Profile",
                status="failed",
                error_code="C010",
                error_message=(
                    "Instagram yêu cầu cookie đăng nhập. "
                    "Cấu hình cookie trong Cài đặt → Cookie → Instagram."
                ),
                support_level="cookie_required",
                discovered_at=time.time(),
            )

        # Cookie available — attempt extraction
        try:
            return self._discover_with_cookies(url, username, max_items)
        except Exception as e:
            return ContainerMeta(
                container_id=cid,
                platform="instagram",
                container_type="profile",
                url=url,
                title=f"@{username}" if username else "Instagram Profile",
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
        """Expand Instagram section — requires cookies."""
        if not self._has_cookies():
            return []
        try:
            return self._expand_with_cookies(url, 200)
        except Exception:
            return []

    def _extract_username(self, url: str) -> str:
        m = re.search(r"instagram\.com/([^/?#/]+)", url, re.IGNORECASE)
        if m and m.group(1) not in ("p", "reel", "stories", "explore", "tv"):
            return m.group(1)
        return ""

    def _has_cookies(self) -> bool:
        """Return True if Instagram cookies are configured in settings."""
        try:
            from app.core.cookie_manager import has_cookies_for
            return has_cookies_for("instagram")
        except Exception:
            return False

    def _discover_with_cookies(
        self, url: str, username: str, max_items: int
    ) -> ContainerMeta:
        """PR3 will implement full extraction here."""
        import yt_dlp
        from app.services.container_discovery import ContainerSection

        cid = make_container_id()
        cookie_opts = self._get_cookie_opts()

        ydl_opts = {
            **cookie_opts,
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "ignoreerrors": True,
            "playlistend": max_items,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise RuntimeError("Không tải được dữ liệu Instagram.")

        entries = [e for e in (info.get("entries") or []) if e and e.get("url")]
        items = [self._entry_to_item(e, username) for e in entries]

        section = ContainerSection(
            key="posts",
            label=f"Bài đăng ({len(items)})",
            item_count=len(items),
            items_loaded=True,
            items=items,
            has_more=len(entries) >= max_items,
        )

        return ContainerMeta(
            container_id=cid,
            platform="instagram",
            container_type="profile",
            url=url,
            title=f"@{username}" if username else info.get("uploader", ""),
            item_count=len(items),
            sections=[section],
            status="ready",
            discovered_at=time.time(),
            support_level="cookie_required",
            warning="Instagram — chỉ khả dụng khi có cookie đăng nhập.",
        )

    def _expand_with_cookies(self, url: str, limit: int) -> list[ContainerItem]:
        import yt_dlp
        cookie_opts = self._get_cookie_opts()
        ydl_opts = {**cookie_opts, "quiet": True, "no_warnings": True,
                    "extract_flat": True, "ignoreerrors": True, "playlistend": limit}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return []
        username = info.get("uploader", "")
        return [self._entry_to_item(e, username)
                for e in (info.get("entries") or []) if e and e.get("url")]

    def _get_cookie_opts(self) -> dict:
        try:
            from app.core.cookie_manager import get_cookie_file
            cf = get_cookie_file("instagram")
            return {"cookiefile": cf} if cf else {}
        except Exception:
            return {}

    def _entry_to_item(self, entry: dict, username: str) -> ContainerItem:
        url = entry.get("url") or entry.get("webpage_url", "")
        title = (entry.get("title") or entry.get("description") or "")[:100]
        thumbnail = entry.get("thumbnail", "")
        duration_ms = int(entry.get("duration") or 0) * 1000
        return ContainerItem(
            id=f"ig:{entry.get('id', '')}",
            url=url,
            title=title or "Instagram post",
            author=username,
            thumbnail=thumbnail,
            duration_ms=duration_ms,
            media_type="video" if duration_ms > 0 else "image",
        )
