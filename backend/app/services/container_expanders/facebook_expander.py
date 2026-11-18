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
_MAX_FB_TOTAL_VIDEOS = 200


def _parse_fb_cursor(cursor: str = "", offset: int = 0) -> int:
    """Parse cursor/offset string (e.g. '50', 'offset:50', 'cursor:100') into 0-based offset."""
    if cursor:
        raw = str(cursor).strip()
        for prefix in ("offset:", "cursor:", "page_offset:"):
            if raw.lower().startswith(prefix):
                raw = raw[len(prefix):].strip()
        if raw.isdigit():
            return max(0, min(int(raw), _MAX_FB_TOTAL_VIDEOS))
    return max(0, min(int(offset or 0), _MAX_FB_TOTAL_VIDEOS))


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
        cursor: str = "",
        offset: int = 0,
    ) -> ContainerMeta:
        cid = make_container_id()
        start_offset = _parse_fb_cursor(cursor, offset)
        page_cap = _MAX_FB_VIDEOS if max_items <= _MAX_FB_VIDEOS else min(max_items, _MAX_FB_TOTAL_VIDEOS)
        limit = max(1, min(page_cap, _MAX_FB_TOTAL_VIDEOS - start_offset))

        # Normalize: ensure URL points to /videos/ tab
        video_url = self._ensure_videos_url(url)

        try:
            if start_offset > 0:
                entries, page_title = self._scrape_flat(video_url, limit, offset=start_offset)
            else:
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
        has_more = len(entries) >= limit and (start_offset + len(items)) < _MAX_FB_TOTAL_VIDEOS
        next_cursor = str(start_offset + len(items)) if has_more else ""
        section = ContainerSection(
            key="videos",
            label=f"Video công khai ({len(items)})",
            item_count=len(items),
            items_loaded=True,
            items=items,
            has_more=has_more,
            next_cursor=next_cursor,
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

    def expand_page(
        self,
        url: str,
        cursor: str = "",
        offset: int = 0,
        limit: int = _MAX_FB_VIDEOS,
    ) -> dict:
        """Phase C5: Cursor/offset paginated expansion up to 200 videos across pages."""
        start_offset = _parse_fb_cursor(cursor, offset)
        if start_offset >= _MAX_FB_TOTAL_VIDEOS:
            return {
                "items": [],
                "offset": start_offset,
                "limit": 0,
                "has_more": False,
                "next_cursor": "",
            }
        effective_limit = max(1, min(int(limit or _MAX_FB_VIDEOS), _MAX_FB_TOTAL_VIDEOS - start_offset))
        try:
            if start_offset > 0:
                entries, _ = self._scrape_flat(self._ensure_videos_url(url), effective_limit, offset=start_offset)
            else:
                entries, _ = self._scrape_flat(self._ensure_videos_url(url), effective_limit)
        except Exception:
            entries = []
        items = [_entry_to_item(e) for e in entries]
        next_off = start_offset + len(items)
        has_more = len(items) >= effective_limit and next_off < _MAX_FB_TOTAL_VIDEOS
        return {
            "items": items,
            "offset": start_offset,
            "limit": effective_limit,
            "has_more": has_more,
            "next_cursor": str(next_off) if has_more else "",
        }

    def expand_section(
        self,
        url: str,
        _section_key: str,
        _child_id: str = "",
        cursor: str = "",
        offset: int = 0,
        limit: int = _MAX_FB_TOTAL_VIDEOS,
    ) -> list[ContainerItem]:
        """Re-fetch videos tab with higher limit or cursor/offset pagination (up to 200)."""
        start_offset = _parse_fb_cursor(cursor or _child_id, offset)
        if start_offset >= _MAX_FB_TOTAL_VIDEOS:
            return []
        effective_limit = max(1, min(int(limit or _MAX_FB_TOTAL_VIDEOS), _MAX_FB_TOTAL_VIDEOS - start_offset))
        try:
            if start_offset > 0:
                entries, _ = self._scrape_flat(self._ensure_videos_url(url), effective_limit, offset=start_offset)
            else:
                entries, _ = self._scrape_flat(self._ensure_videos_url(url), effective_limit)
        except Exception:
            return []
        return [_entry_to_item(e) for e in entries]

    def _scrape_flat(self, url: str, limit: int, offset: int = 0) -> tuple[list[dict], str]:
        import yt_dlp

        end_idx = min(max(0, int(offset)) + max(1, int(limit)), _MAX_FB_TOTAL_VIDEOS)
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "ignoreerrors": True,
            "playlistend": end_idx,
        }
        if offset > 0:
            ydl_opts["playliststart"] = int(offset) + 1

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return [], ""

        page_title = info.get("title") or info.get("uploader") or ""
        raw_entries = [
            e for e in (info.get("entries") or [])
            if e and (e.get("url") or e.get("webpage_url"))
        ]
        # When yt-dlp returns full slice from index 0 despite playliststart (e.g. in mocks),
        # slice to [offset : offset + limit] if len(raw_entries) > limit and offset > 0.
        if offset > 0 and len(raw_entries) > limit:
            raw_entries = raw_entries[offset : offset + limit]
        else:
            raw_entries = raw_entries[:limit]
        return raw_entries, page_title

    def _ensure_videos_url(self, url: str) -> str:
        """Append /videos/ if not already present."""
        clean = url.rstrip("/")
        if "/videos" not in clean:
            clean += "/videos/"
        return clean

    def _page_name_from_url(self, url: str) -> str:
        parts = [p for p in url.rstrip("/").split("/") if p and "facebook" not in p and p not in ("videos", "groups")]
        return parts[0].replace(".", " ").title() if parts else "Facebook Page"
