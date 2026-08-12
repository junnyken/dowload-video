"""
Pinterest Board Container Expander — Phase 25
================================================
Discovers a Pinterest board and returns pins as ContainerItems.

Strategy:
  1. Use yt-dlp extract_flat — handles public boards natively.
  2. Detect pin media_type (video vs image) from extension/URL pattern.
  3. Return two sections: "videos" and "images" (or "all_pins" if mixed).

Limitations:
  - Public boards only.
  - yt-dlp may rate-limit on very large boards (>500 pins); cap at 300.
  - Image pins return direct image URL (not a video download); these use
    direct HTTP download via existing proxy-download endpoint or future
    image downloader.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from app.services.container_discovery import (
    ContainerMeta, ContainerSection, ContainerItem, make_container_id,
)
from app.services.container_expanders import BaseExpander

_MAX_PINS = 300


def _is_video_pin(entry: dict) -> bool:
    url = (entry.get("url") or entry.get("webpage_url") or "").lower()
    ext = entry.get("ext", "")
    vcodec = entry.get("vcodec", "none")
    if ext in ("mp4", "mov", "webm") or vcodec not in ("none", "", None):
        return True
    # Pinterest video pin URLs contain /videos/ or v.pinimg.com
    if "v.pinimg.com" in url or "/videos/" in url:
        return True
    return False


def _pin_id(entry: dict) -> str:
    url = entry.get("url") or entry.get("webpage_url") or ""
    m = re.search(r"/pin/(\d+)", url)
    return f"pin:{m.group(1)}" if m else ""


def _entry_to_item(entry: dict) -> ContainerItem:
    url = entry.get("url") or entry.get("webpage_url") or ""
    # For image pins, the direct URL is the download target
    direct = entry.get("direct_url") or entry.get("url") or url
    title = entry.get("title") or entry.get("description") or "Pinterest pin"
    thumbnail = entry.get("thumbnail") or entry.get("thumbnails", [{}])[0].get("url", "") if isinstance(entry.get("thumbnails"), list) else ""
    is_video = _is_video_pin(entry)
    duration_ms = int(entry.get("duration") or 0) * 1000
    uploader = entry.get("uploader") or entry.get("channel", "")
    upload_date = entry.get("upload_date", "")
    published = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        if len(upload_date) == 8 else upload_date
    )
    return ContainerItem(
        id=_pin_id(entry),
        url=direct or url,
        title=title[:120],
        author=uploader,
        thumbnail=thumbnail,
        duration_ms=duration_ms,
        media_type="video" if is_video else "image",
        views=int(entry.get("view_count") or 0),
        published_at=published,
        extras={"pin_url": url, "is_video": is_video},
    )


class PinterestExpander(BaseExpander):
    platform = "pinterest"
    supported_types = ["board", "profile"]

    def discover(
        self,
        url: str,
        max_items: int = 200,
        include_sections: Optional[list[str]] = None,
    ) -> ContainerMeta:
        cid = make_container_id()
        limit = min(max_items, _MAX_PINS)
        container_type = "board" if self._is_board_url(url) else "profile"

        try:
            entries, board_title, uploader = self._scrape_flat(url, limit)
        except Exception as e:
            return ContainerMeta(
                container_id=cid, platform="pinterest",
                container_type=container_type, url=url,
                title="", status="failed",
                error_code="C001", error_message=str(e)[:200],
            )

        if not entries:
            return ContainerMeta(
                container_id=cid, platform="pinterest",
                container_type=container_type, url=url,
                title=board_title or self._title_from_url(url),
                status="failed",
                error_code="C002",
                error_message="Board trống hoặc riêng tư.",
            )

        all_items = [_entry_to_item(e) for e in entries]
        video_items = [i for i in all_items if i.media_type == "video"]
        image_items = [i for i in all_items if i.media_type == "image"]

        sections: list[ContainerSection] = []

        # Show videos first if any (higher download value)
        want = include_sections or ["all_pins"]

        if video_items and ("videos" in want or "all_pins" in want):
            sections.append(ContainerSection(
                key="videos",
                label=f"Videos ({len(video_items)})",
                item_count=len(video_items),
                items_loaded=True,
                items=video_items,
            ))

        if image_items and ("images" in want or "all_pins" in want):
            sections.append(ContainerSection(
                key="images",
                label=f"Ảnh ({len(image_items)})",
                item_count=len(image_items),
                items_loaded=True,
                items=image_items,
            ))

        warning = ""
        if len(entries) >= limit:
            warning = f"Chỉ hiển thị {limit} pin đầu tiên. Board có thể còn nhiều hơn."

        return ContainerMeta(
            container_id=cid,
            platform="pinterest",
            container_type=container_type,
            url=url,
            title=board_title or self._title_from_url(url),
            description=f"by {uploader}" if uploader else "",
            item_count=len(all_items),
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
        """Full-fetch board pins (up to 500) for lazy expansion."""
        try:
            entries, _, _ = self._scrape_flat(url, 500)
        except Exception:
            return []
        return [_entry_to_item(e) for e in entries]

    def _scrape_flat(self, url: str, limit: int) -> tuple[list[dict], str, str]:
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
            return [], "", ""

        board_title = info.get("title") or info.get("playlist_title") or ""
        uploader = info.get("uploader") or info.get("channel") or ""
        entries = [e for e in (info.get("entries") or []) if e and (e.get("url") or e.get("webpage_url"))]
        return entries, board_title, uploader

    def _is_board_url(self, url: str) -> bool:
        # pinterest.com/{user}/{board}/ → board
        # pinterest.com/{user}/ → profile
        parts = [p for p in url.rstrip("/").split("/") if p and p not in ("http:", "https:", "www.pinterest.com", "pinterest.com")]
        return len(parts) >= 2

    def _title_from_url(self, url: str) -> str:
        parts = [p for p in url.rstrip("/").split("/") if p]
        return parts[-1].replace("-", " ").title() if parts else "Pinterest Board"
