"""
TikTok / Douyin Profile Container Expander — Phase 25
=======================================================
Upgrades from blind channel queue to preview-before-queue flow.

Current state (Phase 24): scrape_channel_task discovers videos and immediately
queues them all. Phase 25 adds: discover → preview → user selects → queue.

Strategy:
  - Reuse scrape_channel_entries_sync() from downloader.py (already used
    by scrape_channel_task).
  - Return first `preview_limit` items as a preview section.
  - User then calls /container/{id}/queue with selection.

Supports:
  - tiktok.com/@{user}      → TikTok profile
  - douyin.com/user/{uid}   → Douyin user
  - douyin.com/@{user}      → Douyin user (newer URL format)
"""

from __future__ import annotations

import re
import time
from typing import Optional

from app.services.container_discovery import (
    ContainerMeta, ContainerSection, ContainerItem, make_container_id,
)
from app.services.container_expanders import BaseExpander

_PREVIEW_LIMIT = 50   # items shown in preview (fetch eagerly)
_MAX_ITEMS = 200


def _platform_from_url(url: str) -> str:
    return "douyin" if "douyin.com" in url else "tiktok"


def _entry_to_item(entry: dict, platform: str) -> ContainerItem:
    url = entry.get("url") or entry.get("webpage_url", "")
    title = (entry.get("title") or entry.get("description") or "")[:120]
    uploader = entry.get("uploader") or entry.get("creator") or entry.get("channel", "")
    thumbnail = entry.get("thumbnail", "")
    if not thumbnail and isinstance(entry.get("thumbnails"), list):
        thumbs = entry.get("thumbnails", [])
        thumbnail = thumbs[0].get("url", "") if thumbs else ""
    duration_ms = int(entry.get("duration") or 0) * 1000
    view_count = int(entry.get("view_count") or 0)
    upload_date = entry.get("upload_date", "")
    published = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        if len(upload_date) == 8 else upload_date
    )
    # Detect pinned posts (TikWM returns is_pinned / is_top)
    is_pinned = bool(entry.get("is_pinned") or entry.get("is_top") or entry.get("pinned"))

    # Canonical ID
    vid_id = ""
    m = re.search(r"/video/(\d+)", url)
    if m:
        vid_id = f"tt:{m.group(1)}" if platform == "tiktok" else f"dy:{m.group(1)}"

    return ContainerItem(
        id=vid_id,
        url=url,
        title=title or "Video",
        author=uploader,
        thumbnail=thumbnail,
        duration_ms=duration_ms,
        media_type="video",
        views=view_count,
        published_at=published,
        extras={"is_pinned": is_pinned, "platform": platform},
    )


class TikTokExpander(BaseExpander):
    platform = "tiktok"
    supported_types = ["profile"]

    def discover(
        self,
        url: str,
        max_items: int = 50,
        include_sections: Optional[list[str]] = None,  # noqa: ARG002
    ) -> ContainerMeta:
        cid = make_container_id()
        platform = _platform_from_url(url)
        limit = min(max_items, _MAX_ITEMS)

        try:
            entries, channel_title = self._scrape_entries(url, limit)
        except Exception as e:
            return ContainerMeta(
                container_id=cid, platform=platform,
                container_type="profile", url=url,
                title="", status="failed",
                error_code="C001", error_message=str(e)[:200],
            )

        if not entries:
            return ContainerMeta(
                container_id=cid, platform=platform,
                container_type="profile", url=url,
                title=channel_title or self._handle_from_url(url),
                status="failed",
                error_code="C002",
                error_message="Không tìm thấy video. Profile có thể riêng tư hoặc trống.",
            )

        items = [_entry_to_item(e, platform) for e in entries]

        # Separate pinned from recent (dedupe: pinned may also appear in feed)
        pinned = [i for i in items if i.extras.get("is_pinned")]
        recent = [i for i in items if not i.extras.get("is_pinned")]

        sections: list[ContainerSection] = []

        if pinned:
            sections.append(ContainerSection(
                key="pinned",
                label=f"Video ghim ({len(pinned)})",
                item_count=len(pinned),
                items_loaded=True,
                items=pinned,
            ))

        sections.append(ContainerSection(
            key="recent",
            label=f"Video gần đây ({len(recent)})",
            item_count=len(recent),
            items_loaded=True,
            items=recent,
            has_more=len(entries) >= limit,
        ))

        warning = ""
        if len(entries) >= limit:
            warning = f"Đang hiển thị {limit} video đầu tiên. Profile có thể có nhiều hơn."

        return ContainerMeta(
            container_id=cid,
            platform=platform,
            container_type="profile",
            url=url,
            title=channel_title or self._handle_from_url(url),
            item_count=len(items),
            sections=sections,
            status="ready",
            discovered_at=time.time(),
            support_level="full",
            warning=warning,
        )

    def expand_section(
        self,
        url: str,
        _section_key: str,
        _child_id: str = "",
    ) -> list[ContainerItem]:
        """Full-fetch profile videos (up to 500) for lazy expansion."""
        try:
            entries, _ = self._scrape_entries(url, 500)
        except Exception:
            return []
        platform = _platform_from_url(url)
        return [_entry_to_item(e, platform) for e in entries]

    def _scrape_entries(self, url: str, limit: int) -> tuple[list[dict], str]:
        """Reuse existing scrape_channel_entries_sync from downloader.py."""
        from app.services.downloader import scrape_channel_entries_sync
        result = scrape_channel_entries_sync(url, max_videos=limit)
        entries = [{"url": e.url, "title": e.title} for e in (result.entries or [])]
        return entries, result.channel_title

    def _handle_from_url(self, url: str) -> str:
        m = re.search(r"/@([^/?#]+)", url)
        return f"@{m.group(1)}" if m else "Profile"
