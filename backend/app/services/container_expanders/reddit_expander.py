"""
Reddit Container Expander — Phase 25 PR2
==========================================
Discovers public Reddit media posts from a subreddit.
Uses yt-dlp (has native Reddit support) — no auth required for public content.

Supported:
  - reddit.com/r/{subreddit}/  → subreddit top media posts
  - reddit.com/r/{subreddit}/top/
  - reddit.com/r/{subreddit}/hot/
  - reddit.com/user/{user}/submitted/ → user posts

Status: partial (public content only, video focus)
"""

from __future__ import annotations

import re
import time
from typing import Optional

from app.services.container_discovery import (
    ContainerMeta, ContainerSection, ContainerItem, make_container_id,
)
from app.services.container_expanders import BaseExpander

_MAX_REDDIT = 100


def _entry_to_item(entry: dict) -> ContainerItem:
    url = entry.get("url") or entry.get("webpage_url") or ""
    title = (entry.get("title") or "")[:120]
    uploader = entry.get("uploader") or entry.get("channel") or "reddit"
    thumbnail = entry.get("thumbnail", "")
    duration_ms = int(entry.get("duration") or 0) * 1000
    view_count = int(entry.get("view_count") or 0)
    upload_date = entry.get("upload_date", "")
    published = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        if len(upload_date) == 8 else upload_date
    )
    post_id = entry.get("id", "") or re.search(r"/comments/([a-z0-9]+)", url or "")
    if hasattr(post_id, "group"):
        post_id = post_id.group(1)
    return ContainerItem(
        id=f"rd:{post_id}" if post_id else "",
        url=url,
        title=title or "Reddit post",
        author=uploader,
        thumbnail=thumbnail,
        duration_ms=duration_ms,
        media_type="video" if duration_ms > 0 else "mixed",
        views=view_count,
        published_at=published,
        extras={"subreddit": entry.get("channel", "")},
    )


class RedditExpander(BaseExpander):
    platform = "reddit"
    supported_types = ["subreddit", "profile"]

    def discover(
        self,
        url: str,
        max_items: int = 50,
        include_sections: Optional[list[str]] = None,  # noqa: ARG002
    ) -> ContainerMeta:
        cid = make_container_id()
        container_type = "subreddit" if "/r/" in url else "profile"
        sub_name = self._extract_name(url, container_type)
        limit = min(max_items, _MAX_REDDIT)

        try:
            entries, page_title = self._scrape_flat(url, limit)
        except Exception as e:
            return ContainerMeta(
                container_id=cid, platform="reddit",
                container_type=container_type, url=url,
                title=sub_name, status="failed",
                error_code="C001", error_message=str(e)[:200],
            )

        if not entries:
            return ContainerMeta(
                container_id=cid, platform="reddit",
                container_type=container_type, url=url,
                title=sub_name, status="failed",
                error_code="C002",
                error_message="Không tìm thấy media. Subreddit có thể không có video/hình.",
            )

        items = [_entry_to_item(e) for e in entries]
        video_items = [i for i in items if i.media_type == "video"]
        other_items = [i for i in items if i.media_type != "video"]

        sections: list[ContainerSection] = []
        if video_items:
            sections.append(ContainerSection(
                key="videos",
                label=f"Video ({len(video_items)})",
                item_count=len(video_items),
                items_loaded=True,
                items=video_items,
                has_more=len(entries) >= limit,
            ))
        if other_items:
            sections.append(ContainerSection(
                key="posts",
                label=f"Bài đăng khác ({len(other_items)})",
                item_count=len(other_items),
                items_loaded=True,
                items=other_items,
            ))

        warning = ""
        if len(entries) >= limit:
            warning = f"Hiển thị {limit} bài đầu tiên. Còn nhiều hơn trên Reddit."

        return ContainerMeta(
            container_id=cid,
            platform="reddit",
            container_type=container_type,
            url=url,
            title=page_title or sub_name,
            item_count=len(items),
            sections=sections,
            status="ready",
            discovered_at=time.time(),
            support_level="partial",
            warning=warning,
        )

    def expand_section(
        self,
        url: str,
        _section_key: str,
        _child_id: str = "",
    ) -> list[ContainerItem]:
        try:
            entries, _ = self._scrape_flat(url, 300)
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

        page_title = info.get("title") or info.get("channel") or ""
        entries = [e for e in (info.get("entries") or []) if e and (e.get("url") or e.get("webpage_url"))]
        return entries, page_title

    def _extract_name(self, url: str, container_type: str) -> str:
        if container_type == "subreddit":
            m = re.search(r"/r/([^/?#/]+)", url)
            return f"r/{m.group(1)}" if m else "Reddit"
        m = re.search(r"/user/([^/?#/]+)", url)
        return f"u/{m.group(1)}" if m else "Reddit User"
