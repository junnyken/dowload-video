"""
YouTube Container Expander — Phase 26-A (proxy-safe)
======================================================
YouTube requires a residential proxy to bypass bot detection.

Proxy health is checked via youtube_proxy_health.get_youtube_proxy_health()
before attempting any network call so failures surface a specific error code
rather than a generic exception.

Error codes:
  C020  — YTDL_PROXY not configured (no_proxy)
  C021  — YTDL_PROXY configured but probe failed (unhealthy)
  C001  — scrape succeeded but returned empty results (no entries)
  C002  — entry list empty after filtering

Supported source types (when proxy healthy):
  - channel   → channel videos (flat extraction)
  - playlist  → playlist items (flat extraction)
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from app.services.container_discovery import (
    ContainerMeta, ContainerSection, ContainerItem, make_container_id,
)
from app.services.container_expanders import BaseExpander

logger = logging.getLogger(__name__)

_MAX_YT = 200


def _entry_to_item(entry: dict) -> ContainerItem:
    url = entry.get("url") or entry.get("webpage_url") or ""
    if url and not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={url}"
    title = (entry.get("title") or "")[:120]
    uploader = entry.get("uploader") or entry.get("channel") or ""
    thumbnail = entry.get("thumbnail", "")
    duration_ms = int(entry.get("duration") or 0) * 1000
    view_count = int(entry.get("view_count") or 0)
    vid_id = entry.get("id", "")
    upload_date = entry.get("upload_date", "")
    published = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        if len(upload_date) == 8 else upload_date
    )
    return ContainerItem(
        id=f"yt:{vid_id}" if vid_id else "",
        url=url,
        title=title or "YouTube video",
        author=uploader,
        thumbnail=thumbnail,
        duration_ms=duration_ms,
        media_type="video",
        views=view_count,
        published_at=published,
        extras={"video_id": vid_id},
    )


class YouTubeExpander(BaseExpander):
    platform = "youtube"
    supported_types = ["channel", "playlist"]

    def discover(
        self,
        url: str,
        max_items: int = 50,
        include_sections: Optional[list[str]] = None,  # noqa: ARG002
    ) -> ContainerMeta:
        from app.services.youtube_proxy_health import get_youtube_proxy_health

        cid = make_container_id()
        container_type = "playlist" if "/playlist" in url else "channel"
        title = self._title_from_url(url)

        # ── Proxy health gate ─────────────────────────────────────────────────
        health = get_youtube_proxy_health()
        logger.info(
            "yt_expander_discover url=%.60s container_type=%s proxy_state=%s",
            url, container_type, health.state,
        )

        if health.state == "no_proxy":
            logger.warning("yt_expander proxy_absent url=%.60s", url)
            return ContainerMeta(
                container_id=cid,
                platform="youtube",
                container_type=container_type,
                url=url,
                title=title,
                status="failed",
                error_code="C020",
                error_message=(
                    "YouTube yêu cầu proxy residential để tránh bot-detect. "
                    "Cấu hình YTDL_PROXY trong biến môi trường."
                ),
                support_level="proxy_required",
                discovered_at=time.time(),
            )

        if health.state == "unhealthy":
            logger.warning(
                "yt_expander proxy_unhealthy url=%.60s error=%.80s",
                url, health.error,
            )
            return ContainerMeta(
                container_id=cid,
                platform="youtube",
                container_type=container_type,
                url=url,
                title=title,
                status="failed",
                error_code="C021",
                error_message=(
                    f"YouTube proxy không hoạt động: {health.error[:100]}. "
                    "Thử lại sau hoặc liên hệ admin."
                ),
                support_level="proxy_required",
                discovered_at=time.time(),
            )

        proxy = self._get_proxy()
        limit = min(max_items, _MAX_YT)
        try:
            entries, channel_title = self._scrape_flat(url, limit, proxy)
        except Exception as exc:
            err_msg = str(exc)[:200]
            logger.error("yt_expander scrape_failed url=%.60s exc=%.200s", url, err_msg)
            # If the scrape fails and it looks like a proxy error, surface C021
            _proxy_keywords = ("407", "403", "tunnel", "proxy", "connection", "timeout")
            err_lower = err_msg.lower()
            if any(kw in err_lower for kw in _proxy_keywords):
                error_code = "C021"
                error_message = f"YouTube proxy lỗi trong quá trình scrape: {err_msg}"
            else:
                error_code = "C001"
                error_message = err_msg
            return ContainerMeta(
                container_id=cid, platform="youtube",
                container_type=container_type, url=url,
                title=title, status="failed",
                error_code=error_code, error_message=error_message,
                support_level="proxy_required",
                discovered_at=time.time(),
            )

        if not entries:
            return ContainerMeta(
                container_id=cid, platform="youtube",
                container_type=container_type, url=url,
                title=channel_title or title, status="partial",
                error_code="C002",
                error_message="Không lấy được danh sách video. Thử lại sau.",
                support_level="proxy_required",
                discovered_at=time.time(),
            )

        items = [_entry_to_item(e) for e in entries]

        section = ContainerSection(
            key="videos",
            label=f"Video ({len(items)})",
            item_count=len(items),
            items_loaded=True,
            items=items,
            has_more=len(entries) >= limit,
        )

        warning = ""
        if len(entries) >= limit:
            warning = f"Hiển thị {limit} video đầu tiên."

        return ContainerMeta(
            container_id=cid,
            platform="youtube",
            container_type=container_type,
            url=url,
            title=channel_title or title,
            item_count=len(items),
            sections=[section],
            status="ready",
            discovered_at=time.time(),
            support_level="proxy_required",
            warning=warning,
        )

    def expand_section(
        self,
        url: str,
        _section_key: str,
        _child_id: str = "",
    ) -> list[ContainerItem]:
        from app.services.youtube_proxy_health import get_youtube_proxy_health

        health = get_youtube_proxy_health()
        logger.info(
            "yt_expander_expand url=%.60s section=%s proxy_state=%s",
            url, _section_key, health.state,
        )
        if not health.is_usable():
            logger.warning(
                "yt_expander expand blocked proxy_state=%s error=%.80s",
                health.state, health.error,
            )
            return []

        proxy = self._get_proxy()
        try:
            entries, _ = self._scrape_flat(url, 500, proxy)
        except Exception as exc:
            logger.error("yt_expander expand_scrape_failed url=%.60s exc=%.200s", url, str(exc))
            return []
        return [_entry_to_item(e) for e in entries]

    def _get_proxy(self) -> Optional[str]:
        import os
        return os.environ.get("YTDL_PROXY") or os.environ.get("RESIDENTIAL_PROXY_URL")

    def _scrape_flat(
        self, url: str, limit: int, proxy: str
    ) -> tuple[list[dict], str]:
        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "ignoreerrors": True,
            "playlistend": limit,
            "proxy": proxy,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return [], ""

        channel_title = info.get("title") or info.get("uploader") or ""
        entries = [
            e for e in (info.get("entries") or [])
            if e and (e.get("url") or e.get("id"))
        ]
        return entries, channel_title

    def _title_from_url(self, url: str) -> str:
        m = re.search(r"/@([^/?#/]+)", url)
        if m:
            return f"@{m.group(1)}"
        m = re.search(r"/channel/([^/?#/]+)", url)
        if m:
            return m.group(1)
        return "YouTube Channel"
