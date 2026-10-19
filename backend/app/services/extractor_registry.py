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
            if not ext.is_enabled():
                continue
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
            if not ext.is_enabled() or ext.platform_name in seen:
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
        Returns {status: "valid"|"unknown"|"unsupported"|"invalid", platform,
        source_type, support_level, message}.

        Decided by the same classifier as /resolve-input (source_classifier +
        container_registry), the one the download path follows. This registry
        alone only holds the secondary extractors (Bilibili, Instagram,
        Xiaohongshu…): YouTube, TikTok, Douyin, Facebook and X — the bulk of
        traffic, handled by the main downloader — all came back "unsupported"
        while a job for the very same link downloaded fine.
        """
        if not url or not url.startswith(("http://", "https://")):
            return {"status": "invalid", "platform": None, "source_type": None,
                    "support_level": None, "message": "URL không hợp lệ"}
        from app.core.source_classifier import classify  # noqa: PLC0415
        from app.core.url_normalizer import extract_share_url, normalize  # noqa: PLC0415
        from app.schemas.container import SUPPORT_LEVEL_LABELS  # noqa: PLC0415
        from app.services.container_registry import get_capability  # noqa: PLC0415

        canonical = normalize(extract_share_url(url) or url).canonical_url
        clf = classify(canonical)
        ext = self.detect_safe(url)
        known = clf.platform not in ("unknown", "generic")
        platform = clf.platform if known else (ext.platform_name if ext else None)
        level = get_capability(clf.platform, clf.source_type).support_level.value
        base = {"platform": platform, "source_type": clf.source_type, "support_level": level}

        if level in ("unsupported", "temporarily_disabled") and not ext:
            # wording: BA review
            return {**base, "status": "unsupported",
                    "message": f"{SUPPORT_LEVEL_LABELS.get(level, level)}: {url[:80]}"}
        if platform is None:
            # A site we do not recognise may still download (generic
            # extractor); say so instead of refusing it.
            # wording: BA review
            return {**base, "status": "unknown",
                    "message": "Chưa nhận diện được nền tảng; VidGrab vẫn sẽ thử tải khi tạo job."}
        return {**base, "status": "valid",
                "message": SUPPORT_LEVEL_LABELS.get(level, "OK")}

    def validate_batch(self, urls: list[str]) -> dict:
        """
        Validate a list of URLs for bulk download.
        Returns summary + per-url results with deduplication.
        """
        seen: set[str] = set()
        results = []
        counts = {"valid": 0, "duplicate": 0, "unknown": 0, "unsupported": 0, "invalid": 0}

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
        if counts["unknown"]:
            summary_parts.append(f"{counts['unknown']} link chưa nhận diện được nền tảng")
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
        # Registers itself ONLY when KUAISHOU_ENABLED is on (default off).
        "app.services.kuaishou_extractor",
    ]
    for mod in modules:
        try:
            __import__(mod)
        except ImportError as e:
            logger.warning("Could not load extractor module %s: %s", mod, e)
        except Exception as e:
            logger.error("Error loading extractor %s: %s", mod, e)


_load_extractors()
