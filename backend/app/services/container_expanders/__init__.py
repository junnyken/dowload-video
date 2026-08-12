"""
Container Expanders — Phase 25 (PR2 updated)
=============================================
Factory that maps platform → expander instance.

Each expander implements:
    discover(url, max_items, include_sections) -> ContainerMeta
    expand_section(url, section_key, child_id) -> list[ContainerItem]  # PR2

The methods are synchronous (called from Celery worker context).
Async HTTP calls inside expanders use asyncio.run() or sync wrappers.

PR2 additions: instagram, twitter, reddit, youtube expanders.
"""

from __future__ import annotations
from typing import Optional
from app.services.container_discovery import ContainerItem, ContainerMeta


class BaseExpander:
    """Interface every expander must satisfy."""
    platform: str = ""
    supported_types: list[str] = []

    def discover(
        self,
        url: str,
        max_items: int = 200,
        include_sections: Optional[list[str]] = None,
    ) -> ContainerMeta:
        raise NotImplementedError

    def expand_section(
        self,
        url: str,
        section_key: str,
        child_id: str = "",
    ) -> list[ContainerItem]:
        """
        Expand a lazy section or child collection.
        Default: raise NotImplementedError — subclasses override selectively.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement expand_section()"
        )


def get_expander(platform: str) -> Optional[BaseExpander]:
    """Return the expander for a platform, or None if not yet supported."""
    _map = _get_map()
    return _map.get(platform)


def _get_map() -> dict[str, BaseExpander]:
    """Lazy import to avoid circular deps and speed up startup."""
    from app.services.container_expanders.spotify_expander import SpotifyExpander
    from app.services.container_expanders.soundcloud_expander import SoundCloudExpander
    from app.services.container_expanders.threads_expander import ThreadsExpander
    from app.services.container_expanders.pinterest_expander import PinterestExpander
    from app.services.container_expanders.tiktok_expander import TikTokExpander
    from app.services.container_expanders.facebook_expander import FacebookExpander
    from app.services.container_expanders.instagram_expander import InstagramExpander
    from app.services.container_expanders.twitter_expander import TwitterExpander
    from app.services.container_expanders.reddit_expander import RedditExpander
    from app.services.container_expanders.youtube_expander import YouTubeExpander

    return {
        "spotify":    SpotifyExpander(),
        "soundcloud": SoundCloudExpander(),
        "threads":    ThreadsExpander(),
        "pinterest":  PinterestExpander(),
        "tiktok":     TikTokExpander(),
        "douyin":     TikTokExpander(),
        "facebook":   FacebookExpander(),
        "instagram":  InstagramExpander(),
        "twitter":    TwitterExpander(),
        "reddit":     RedditExpander(),
        "youtube":    YouTubeExpander(),
    }
