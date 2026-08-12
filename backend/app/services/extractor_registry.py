"""
Extractor Registry
==================
Central registry that maps URLs to the correct extractor.

Usage:
    from app.services.extractor_registry import REGISTRY

    extractor = REGISTRY.detect(url)  # returns BaseExtractor or raises
    result    = extractor.extract_single(url)

    # Batch
    batch_extractor = REGISTRY.detect(url, batch=True)
    batch_result    = batch_extractor.extract_batch(url, limit=50)

Registration:
    REGISTRY.register(MyExtractor())
    # Or use the @register decorator:
    @REGISTRY.register_class
    class MyExtractor(BaseExtractor): ...

The registry is populated at module import time — all extractors import this
module and call register().  Order matters: first match wins, so put more
specific extractors before catch-alls.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.services.base_extractor import BaseExtractor

logger = logging.getLogger(__name__)


class PlatformNotSupported(ValueError):
    """Raised when no extractor matches a given URL."""

    def __init__(self, url: str):
        self.url = url
        super().__init__(f"No extractor found for URL: {url}")


class ExtractorRegistry:
    def __init__(self):
        self._extractors: list[BaseExtractor] = []

    def register(self, extractor: BaseExtractor) -> "ExtractorRegistry":
        """Register an extractor instance. First-match wins on detect()."""
        self._extractors.append(extractor)
        logger.debug("Registered extractor: %s", extractor.platform_name)
        return self

    def register_class(self, cls: type) -> type:
        """Decorator: instantiate and register a BaseExtractor subclass."""
        self.register(cls())
        return cls

    def detect(self, url: str, batch: bool = False) -> BaseExtractor:
        """
        Return the first extractor that matches url.

        If batch=True, only consider extractors that support batch extraction
        (have 'batch' or 'channel' in supported_features).

        Raises PlatformNotSupported if no extractor matches.
        """
        for ext in self._extractors:
            if ext.detect_url(url):
                if batch and not (
                    "batch" in ext.supported_features
                    or "channel" in ext.supported_features
                ):
                    continue
                return ext
        raise PlatformNotSupported(url)

    def detect_safe(self, url: str, batch: bool = False) -> Optional[BaseExtractor]:
        """Like detect() but returns None instead of raising."""
        try:
            return self.detect(url, batch=batch)
        except PlatformNotSupported:
            return None

    def platform_for(self, url: str) -> str:
        """Return platform name string or 'unknown'."""
        ext = self.detect_safe(url)
        return ext.platform_name if ext else "unknown"

    def all_platforms(self) -> list[dict]:
        """Return metadata for all registered extractors (for /platforms page)."""
        seen: set[str] = set()
        result = []
        for ext in self._extractors:
            if ext.platform_name in seen:
                continue
            seen.add(ext.platform_name)
            result.append({
                "platform": ext.platform_name,
                "features": sorted(ext.supported_features),
                "cost_tier": ext.cost_tier,
                "requires_cookie": ext.requires_cookie,
                "supports_proxy": ext.supports_proxy,
            })
        return result

    def validate_batch_url(self, url: str) -> dict:
        """
        Validate a single URL for batch submission.
        Returns {status: "valid"|"unsupported"|"invalid", platform, message}.
        """
        if not url or not url.startswith(("http://", "https://")):
            return {"status": "invalid", "platform": None, "message": "URL không hợp lệ"}
        ext = self.detect_safe(url)
        if not ext:
            return {
                "status": "unsupported",
                "platform": None,
                "message": f"Nền tảng chưa được hỗ trợ: {url[:80]}",
            }
        return {
            "status": "valid",
            "platform": ext.platform_name,
            "message": "OK",
        }

    def validate_batch(self, urls: list[str]) -> dict:
        """
        Validate a list of URLs for bulk download.
        Returns summary + per-url results with deduplication.
        """
        seen: set[str] = set()
        results = []
        counts = {"valid": 0, "duplicate": 0, "unsupported": 0, "invalid": 0}

        for url in urls:
            url = url.strip()
            if not url:
                continue

            if url in seen:
                results.append({"url": url, "status": "duplicate", "platform": None})
                counts["duplicate"] += 1
                continue
            seen.add(url)

            info = self.validate_batch_url(url)
            results.append({"url": url, **info})
            counts[info["status"]] += 1

        total = len(results)
        summary_parts = [f"{counts['valid']} hợp lệ"]
        if counts["duplicate"]:
            summary_parts.append(f"{counts['duplicate']} trùng lặp")
        if counts["unsupported"]:
            summary_parts.append(f"{counts['unsupported']} không hỗ trợ")
        if counts["invalid"]:
            summary_parts.append(f"{counts['invalid']} không hợp lệ")

        return {
            "total": total,
            "counts": counts,
            "summary": ", ".join(summary_parts),
            "urls": results,
        }

    def __len__(self) -> int:
        return len(self._extractors)

    def __repr__(self) -> str:
        names = [e.platform_name for e in self._extractors]
        return f"<ExtractorRegistry [{', '.join(names)}]>"


# ── Singleton ──────────────────────────────────────────────────────────────

REGISTRY = ExtractorRegistry()


# ── Lazy population ────────────────────────────────────────────────────────
# Import all extractor modules here. Each module calls REGISTRY.register()
# at import time.  Wrapped in try/except so a broken extractor doesn't
# crash the entire backend.

def _load_extractors():
    """Populate REGISTRY by importing all extractor modules."""
    modules = [
        "app.services.bilibili_extractor",
        "app.services.alt_platforms_extractor",
        "app.services.snapchat_extractor",
        "app.services.instagram_extractor",
        "app.services.xiaohongshu_extractor",
        "app.services.lemon8_extractor",
        "app.services.podcast_extractor",
    ]
    for mod in modules:
        try:
            __import__(mod)
        except ImportError as e:
            logger.warning("Could not load extractor module %s: %s", mod, e)
        except Exception as e:
            logger.error("Error loading extractor %s: %s", mod, e)


_load_extractors()
