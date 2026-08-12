"""
Threads Profile Container Expander — Phase 25
================================================
Upgrades Threads from single-post only to full profile preview flow.

Uses the existing threads_extractor.py scrape_profile() function.
Returns ContainerMeta with a "recent_posts" section (media-only filter supported).

Limitations (honest):
  - Threads anti-bot blocks repeated fetches; TTL in Redis prevents re-scraping.
  - Only public profiles supported.
  - Post count limited by what threads_extractor.py can reliably fetch (~20 posts).
"""

from __future__ import annotations

import re
import time
from typing import Optional

from app.services.container_discovery import (
    ContainerMeta, ContainerSection, ContainerItem, make_container_id,
)
from app.services.container_expanders import BaseExpander


def _post_to_items(post: dict, author: str) -> list[ContainerItem]:
    """Convert a Threads post dict to one or more ContainerItems (carousel support)."""
    post_url = post.get("url", "")
    post_id = post.get("post_id", "")
    caption = (post.get("caption_snippet") or "")[:80]
    timestamp = post.get("timestamp", "")
    has_media = post.get("has_media", False)

    if not has_media:
        return []  # skip text-only posts in container view

    media_items = post.get("media_items", [])

    if not media_items:
        # No expanded media, treat post itself as one item
        return [ContainerItem(
            id=f"th:{post_id}" if post_id else "",
            url=post_url,
            title=caption or "Threads post",
            author=author,
            thumbnail=post.get("thumbnail", ""),
            duration_ms=0,
            media_type="mixed",
            published_at=timestamp,
            extras={"post_id": post_id},
        )]

    items = []
    for idx, mi in enumerate(media_items):
        mtype = mi.get("type") or mi.get("media_type", "image")
        thumb = mi.get("thumbnail") or mi.get("url", "")
        item_url = mi.get("url", post_url)
        items.append(ContainerItem(
            id=f"th:{post_id}_{idx}" if post_id else "",
            url=item_url,
            title=f"{caption} [{idx+1}/{len(media_items)}]" if len(media_items) > 1 else caption or "Threads media",
            author=author,
            thumbnail=thumb if mtype == "image" else "",
            duration_ms=0,
            media_type="video" if mtype == "video" else "image",
            published_at=timestamp,
            extras={"post_id": post_id, "carousel_index": idx},
        ))
    return items


class ThreadsExpander(BaseExpander):
    platform = "threads"
    supported_types = ["profile"]

    def discover(
        self,
        url: str,
        max_items: int = 50,
        include_sections: Optional[list[str]] = None,  # noqa: ARG002 — future section filter
    ) -> ContainerMeta:
        cid = make_container_id()
        username = self._extract_username(url)

        if not username:
            return ContainerMeta(
                container_id=cid, platform="threads",
                container_type="profile", url=url,
                title="", status="failed",
                error_code="C001", error_message="Không nhận diện được username Threads từ URL.",
            )

        try:
            result = self._scrape_profile(url)
        except Exception as e:
            return ContainerMeta(
                container_id=cid, platform="threads",
                container_type="profile", url=url,
                title=f"@{username}", status="failed",
                error_code="C001", error_message=str(e)[:200],
            )

        error_code = result.get("error_code")
        if error_code:
            # Map threads_extractor error codes → container error codes
            _map = {
                "threads_private_or_login_required": ("C010", "🔒 Profile này ở chế độ riêng tư."),
                "threads_extraction_blocked": ("C040", "Threads tạm thời chặn yêu cầu. Thử lại sau vài phút."),
            }
            ccode, cmsg = _map.get(error_code, ("C001", result.get("error_message", "Không tải được profile.")))
            return ContainerMeta(
                container_id=cid, platform="threads",
                container_type="profile", url=url,
                title=f"@{username}", status="failed",
                error_code=ccode, error_message=cmsg,
                support_level="public_only" if "private" in error_code else "partial",
            )

        posts = result.get("posts", [])
        author = result.get("author_handle") or username

        # Flatten posts → ContainerItems (media only)
        all_items: list[ContainerItem] = []
        for post in posts:
            all_items.extend(_post_to_items(post, author))
            if len(all_items) >= max_items:
                break

        has_more = len(all_items) >= max_items

        sections: list[ContainerSection] = []

        if all_items:
            sections.append(ContainerSection(
                key="recent_posts",
                label=f"Bài gần đây có media ({len(all_items)})",
                item_count=len(all_items),
                items_loaded=True,
                items=all_items[:max_items],
                has_more=has_more,
            ))
        else:
            # Profile exists but no media posts
            return ContainerMeta(
                container_id=cid, platform="threads",
                container_type="profile", url=url,
                title=f"@{username}",
                item_count=0, sections=[],
                status="ready",
                discovered_at=time.time(),
                warning="Profile công khai nhưng không có bài đăng có media.",
                support_level="full",
            )

        return ContainerMeta(
            container_id=cid,
            platform="threads",
            container_type="profile",
            url=url,
            title=f"@{username}",
            item_count=len(all_items),
            sections=sections,
            status="ready",
            discovered_at=time.time(),
            support_level="full",
            warning="Chỉ hiển thị profile công khai. Anti-bot giới hạn số lượng bài.",
        )

    def expand_section(
        self,
        url: str,
        _section_key: str,
        _child_id: str = "",
    ) -> list[ContainerItem]:
        """Re-fetch profile with higher post limit for full expansion."""
        try:
            result = self._scrape_profile_extended(url, max_posts=100)
        except Exception:
            return []
        posts = result.get("posts", [])
        author = result.get("author_handle", "")
        items: list[ContainerItem] = []
        for post in posts:
            items.extend(_post_to_items(post, author))
        return items

    def _scrape_profile_extended(self, url: str, max_posts: int) -> dict:
        from app.services.threads_extractor import scrape_threads_profile_sync
        return scrape_threads_profile_sync(url, max_posts=max_posts)

    def _extract_username(self, url: str) -> str:
        m = re.search(r"threads\.(?:net|com)/@([^/?#]+)", url, re.IGNORECASE)
        return m.group(1) if m else ""

    def _scrape_profile(self, url: str) -> dict:
        """Call existing threads_extractor sync wrapper."""
        from app.services.threads_extractor import scrape_threads_profile_sync
        return scrape_threads_profile_sync(url, max_posts=30)
