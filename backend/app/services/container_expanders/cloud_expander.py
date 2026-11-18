"""
Cloud Drive & Telegram Container Expander — Phase A/B Folder & Channel Support
===============================================================================
Bridges folder/batch extractors (`gdrive`, `onedrive`, `mega`, `terabox`,
`dropbox`, `pikpak`, `telegram`) into the Phase 25 Container Discovery pipeline
(`/api/v1/container/discover`).
"""

from __future__ import annotations

import logging
from typing import Optional

from app.services.container_discovery import (
    ContainerItem,
    ContainerMeta,
    ContainerSection,
    make_container_id,
)
from app.services.container_expanders import BaseExpander
from app.services.extractor_registry import REGISTRY

logger = logging.getLogger(__name__)

_PLATFORM_TITLES = {
    "gdrive": "Thư mục Google Drive",
    "onedrive": "Thư mục OneDrive / SharePoint",
    "mega": "Thư mục Mega.nz",
    "terabox": "Thư mục Terabox",
    "dropbox": "Thư mục Dropbox",
    "pikpak": "Thư mục PikPak Cloud",
    "telegram": "Kênh Telegram",
}


class CloudExpander(BaseExpander):
    """Generic container expander delegating to `extractor.extract_batch`."""

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.supported_types = ["folder", "channel"]

    def discover(
        self,
        url: str,
        max_items: int = 200,
        include_sections: Optional[list[str]] = None,  # noqa: ARG002
    ) -> ContainerMeta:
        cid = make_container_id()
        default_title = _PLATFORM_TITLES.get(self.platform, f"Thư mục {self.platform.capitalize()}")
        extractor = REGISTRY.get_by_name(self.platform)
        if extractor is None:
            return ContainerMeta(
                container_id=cid,
                platform=self.platform,
                container_type="folder",
                url=url,
                title=default_title,
                status="failed",
                error_code="C030",
                error_message=f"Không tìm thấy bộ giải mã cho {self.platform}.",
            )

        try:
            batch = extractor.extract_batch(url, limit=min(int(max_items or 200), 200))
        except Exception as exc:
            logger.warning("cloud_expander_failed platform=%s url=%.80s err=%s", self.platform, url, exc)
            return ContainerMeta(
                container_id=cid,
                platform=self.platform,
                container_type="folder",
                url=url,
                title=default_title,
                status="failed",
                error_code="C020",
                error_message=str(exc) or f"Không đọc được danh sách tệp từ {default_title}.",
            )

        if not batch.get("success") and not batch.get("entries"):
            err_msg = batch.get("error") or f"Thư mục trống hoặc cần quyền truy cập ({default_title})."
            return ContainerMeta(
                container_id=cid,
                platform=self.platform,
                container_type="folder",
                url=url,
                title=batch.get("channel") or default_title,
                status="failed",
                error_code="C021",
                error_message=err_msg,
            )

        entries = batch.get("entries") or []
        items: list[ContainerItem] = []
        for idx, entry in enumerate(entries):
            item_url = entry.get("url") or url
            title = entry.get("title") or f"Tệp #{idx + 1}"
            duration_sec = int(entry.get("duration") or 0)
            items.append(
                ContainerItem(
                    id=f"{self.platform}:{idx}:{hash(item_url) & 0xFFFFFF:06x}",
                    url=item_url,
                    title=title,
                    author=batch.get("channel") or default_title,
                    thumbnail=entry.get("thumbnail") or "",
                    duration_ms=duration_sec * 1000 if duration_sec > 0 else 0,
                    media_type="video",
                    published_at=entry.get("upload_date") or "",
                    extras={"index": idx},
                )
            )

        section = ContainerSection(
            key="files",
            label="Danh sách tệp trong thư mục" if self.platform != "telegram" else "Danh sách media",
            total_estimated=len(items),
            items_loaded=True,
            items=items,
        )

        return ContainerMeta(
            container_id=cid,
            platform=self.platform,
            container_type="folder" if self.platform != "telegram" else "channel",
            url=url,
            title=batch.get("channel") or default_title,
            description=f"{len(items)} tệp sẵn sàng tải xuống",
            item_count=len(items),
            status="ready",
            support_level="full",
            sections=[section],
        )
