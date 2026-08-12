"""
SoundCloud Container Expander — Phase 25
==========================================
Discovers SoundCloud artist profile / playlist as ContainerMeta.

Uses yt-dlp extract_flat (same as existing scrape_channel_entries_sync)
to enumerate tracks. SoundCloud is Tier-1: public, no cookie, stable.

Supported URLs:
  soundcloud.com/{user}                 → artist profile
  soundcloud.com/{user}/sets/{playlist} → playlist
  soundcloud.com/{user}/likes           → likes (public, if visible)
"""

from __future__ import annotations

import re
import time
from typing import Optional

from app.services.container_discovery import (
    ContainerMeta, ContainerSection, ContainerItem, make_container_id,
)
from app.services.container_expanders import BaseExpander

# Max items to scrape in one discover pass (yt-dlp paginates internally)
_MAX_SC_ITEMS = 300


def _sc_container_type(url: str) -> str:
    if "/sets/" in url:
        return "playlist"
    if "/likes" in url:
        return "media_tab"
    return "profile"  # artist profile


def _entry_to_item(entry: dict) -> ContainerItem:
    url = entry.get("url") or entry.get("webpage_url", "")
    title = entry.get("title", "Unknown")
    uploader = entry.get("uploader") or entry.get("artist", "")
    thumbnail = entry.get("thumbnail", "")
    duration_ms = int(entry.get("duration") or 0) * 1000
    view_count = int(entry.get("view_count") or 0)
    upload_date = entry.get("upload_date", "")  # YYYYMMDD
    published = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        if len(upload_date) == 8 else upload_date
    )

    # Derive stable ID from URL slug
    slug = url.rstrip("/").split("/")[-1]
    item_id = f"sc:{slug}" if slug else ""

    return ContainerItem(
        id=item_id,
        url=url,
        title=title,
        author=uploader,
        thumbnail=thumbnail,
        duration_ms=duration_ms,
        media_type="audio",
        views=view_count,
        published_at=published,
    )


class SoundCloudExpander(BaseExpander):
    platform = "soundcloud"
    supported_types = ["profile", "playlist", "media_tab"]

    def discover(
        self,
        url: str,
        max_items: int = 200,
        include_sections: Optional[list[str]] = None,
    ) -> ContainerMeta:
        container_type = _sc_container_type(url)
        cid = make_container_id()
        limit = min(max_items, _MAX_SC_ITEMS)

        try:
            entries = self._scrape_flat(url, limit)
        except Exception as e:
            return ContainerMeta(
                container_id=cid, platform="soundcloud",
                container_type=container_type, url=url,
                title="", status="failed",
                error_code="C001", error_message=str(e)[:200],
            )

        if not entries:
            return ContainerMeta(
                container_id=cid, platform="soundcloud",
                container_type=container_type, url=url,
                title="", status="failed",
                error_code="C002",
                error_message="Không tìm thấy nội dung. Profile/playlist có thể riêng tư hoặc trống.",
            )

        # Extract uploader info from first entry
        first = entries[0]
        channel_title = first.get("uploader") or first.get("channel", "")
        avatar = first.get("uploader_url", "")

        items = [_entry_to_item(e) for e in entries]

        if container_type == "playlist":
            section_label = f"Tracks ({len(items)})"
            section_key = "tracks"
        else:
            section_label = f"Nhạc đã đăng ({len(items)})"
            section_key = "uploads"

        section = ContainerSection(
            key=section_key,
            label=section_label,
            item_count=len(items),
            items_loaded=True,
            items=items,
            has_more=len(entries) >= limit,
        )

        # If artist profile: try to detect if there are more items
        warning = ""
        if len(entries) >= limit:
            warning = f"Chỉ hiển thị {limit} mục đầu tiên. Có thể còn nhiều hơn."

        return ContainerMeta(
            container_id=cid,
            platform="soundcloud",
            container_type=container_type,
            url=url,
            title=channel_title or _title_from_url(url),
            avatar=avatar,
            item_count=len(items),
            sections=[section],
            status="ready",
            discovered_at=time.time(),
            warning=warning,
        )

    def expand_section(
        self,
        url: str,
        _section_key: str,
        _child_id: str = "",
    ) -> list[ContainerItem]:
        """Expand a SoundCloud section (re-fetch with higher limit)."""
        entries = self._scrape_flat(url, 500)
        return [_entry_to_item(e) for e in entries]

    def _scrape_flat(self, url: str, limit: int) -> list[dict]:
        """Use yt-dlp extract_flat to enumerate entries without downloading."""
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
            return []

        # Playlist/channel result
        if info.get("_type") in ("playlist", "url_transparent"):
            return [e for e in (info.get("entries") or []) if e and e.get("url")]

        # Single track result (user passed a single track URL accidentally)
        if info.get("url") or info.get("webpage_url"):
            return [info]

        return []


def _title_from_url(url: str) -> str:
    parts = url.rstrip("/").split("/")
    return parts[-1] if parts else "SoundCloud"
