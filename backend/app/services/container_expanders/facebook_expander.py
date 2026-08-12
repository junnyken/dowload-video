"""
Facebook Public Video Listing Expander — Phase 25
===================================================
Discovers public videos from a Facebook page/group video tab.

Strategy: yt-dlp extract_flat on the /videos/ URL.
Only public videos are reachable — private/login-required videos are skipped.

Supported:
  - facebook.com/{page}/videos/
  - facebook.com/groups/{group}/videos/ (public groups only)
  - facebook.com/{page}/videos/{playlist_id}/  (video albums)

Limitation:
  - Facebook's anti-bot is moderate; large pages may timeout or return partial.
  - Max 50 videos per discover (tunable via max_items).
"""

from __future__ import annotations

import re
import time
from typing import Optional

from app.services.container_discovery import (
    ContainerMeta, ContainerSection, ContainerItem, make_container_id,
)
from app.services.container_expanders import BaseExpander

_MAX_FB_VIDEOS = 50


def _entry_to_item(entry: dict) -> ContainerItem:
    url = entry.get("url") or entry.get("webpage_url") or ""
    title = (entry.get("title") or "")[:120]
    uploader = entry.get("uploader") or entry.get("channel") or ""
    thumbnail = entry.get("thumbnail") or ""
    if not thumbnail and isinstance(entry.get("thumbnails"), list):
        thumbs = entry.get("thumbnails") or []
        thumbnail = thumbs[0].get("url", "") if thumbs else ""
    duration_ms = int(entry.get("duration") or 0) * 1000
    view_count = int(entry.get("view_count") or 0)
    upload_date = entry.get("upload_date", "")
    published = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        if len(upload_date) == 8 else upload_date
    )

    # Canonical ID
    vid_id = ""
    m = re.search(r"(?:videos?|v)[=/](\d+)", url)
    if m:
        vid_id = f"fb:{m.group(1)}"

    return ContainerItem(
        id=vid_id,
        url=url,
        title=title or "Facebook video",
        author=uploader,
        thumbnail=thumbnail,
        duration_ms=duration_ms,
        media_type="video",
        views=view_count,
        published_at=published,
    )


class FacebookExpander(BaseExpander):
    platform = "facebook"
    supported_types = ["media_tab", "channel"]

    def discover(
        self,
        url: str,
        max_items: int = 50,
        include_sections: Optional[list[str]] = None,  # noqa: ARG002
    ) -> ContainerMeta:
        cid = make_container_id()
        limit = min(max_items, _MAX_FB_VIDEOS)

        # Normalize: ensure URL points to /videos/ tab
        video_url = self._ensure_videos_url(url)

        try:
            entries, page_title = self._scrape_flat(video_url, limit)
        except Exception as e:
            return ContainerMeta(
                container_id=cid, platform="facebook",
                container_type="media_tab", url=url,
                title="", status="failed",
                error_code="C001", error_message=str(e)[:200],
            )

        if not entries:
            return ContainerMeta(
                container_id=cid, platform="facebook",
                container_type="media_tab", url=url,
                title=page_title or self._page_name_from_url(url),
                status="failed",
                error_code="C002",
                error_message="Không tìm thấy video công khai từ trang này.",
            )

        items = [_entry_to_item(e) for e in entries]
        section = ContainerSection(
            key="videos",
            label=f"Video công khai ({len(items)})",
            item_count=len(items),
            items_loaded=True,
            items=items,
            has_more=len(entries) >= limit,
        )

        return ContainerMeta(
            container_id=cid,
            platform="facebook",
            container_type="media_tab",
            url=url,
            title=page_title or self._page_name_from_url(url),
            item_count=len(items),
            sections=[section],
            status="ready" if items else "partial",
            discovered_at=time.time(),
            support_level="partial",
            warning="Chỉ hiển thị video công khai. Video riêng tư yêu cầu đăng nhập.",
        )

    def expand_section(
        self,
        url: str,
        _section_key: str,
        _child_id: str = "",
    ) -> list[ContainerItem]:
        """Re-fetch videos tab with higher limit for full expansion."""
        try:
            entries, _ = self._scrape_flat(self._ensure_videos_url(url), 200)
        except Exception:
            return []
        return [_entry_to_item(e) for e in entries]

    def _scrape_flat(self, url: str, limit: int) -> tuple[list[dict], str]:
        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "ignoreerrors": True,
            "playlistend": limit,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return [], ""

        page_title = info.get("title") or info.get("uploader") or ""
        entries = [
            e for e in (info.get("entries") or [])
            if e and (e.get("url") or e.get("webpage_url"))
        ]
        return entries, page_title

    def _ensure_videos_url(self, url: str) -> str:
        """Append /videos/ if not already present."""
        clean = url.rstrip("/")
        if "/videos" not in clean:
            clean += "/videos/"
        return clean

    def _page_name_from_url(self, url: str) -> str:
        parts = [p for p in url.rstrip("/").split("/") if p and "facebook" not in p and p not in ("videos", "groups")]
        return parts[0].replace(".", " ").title() if parts else "Facebook Page"
