"""
Podcast RSS / Apple Podcasts / Spotify Podcast Extractor
==========================================================
Downloads audio from podcast RSS feeds and resolves Apple Podcasts URLs.

Supported inputs:
  - Direct RSS feed URL (any valid podcast RSS)
  - Apple Podcasts URL → extract RSS → download episode
  - Spotify Podcast URL → reuse Spotify service
  - Direct episode MP3/audio URL

Strategy:
  1. Detect input type (RSS, Apple, Spotify, direct).
  2. For Apple: fetch Apple API to resolve RSS feed URL.
  3. Parse RSS XML: extract episodes list or specific episode.
  4. Return episode audio URL (direct download, no yt-dlp needed).
  5. For Spotify podcast: delegate to spotify_service.

Error codes: podcast_rss_invalid, podcast_episode_not_found
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_PLATFORM = "Podcast"

_URL_PATTERNS = [
    r"podcasts\.apple\.com/",
    r"/feed(?:\.xml|\.rss)?",
    r"/podcast\.xml",
    r"/rss(?:\.xml)?",
    r"feeds\.(soundcloud|buzzsprout|libsyn|anchor|captivate|podbean|simplecast|transistor)\.com",
    r"open\.spotify\.com/(?:show|episode)/",
    r"anchor\.fm/",
]

_APPLE_PODCAST_RE = re.compile(
    r"podcasts\.apple\.com/[a-z]{2}/podcast/[^/]+/id(\d+)",
    re.IGNORECASE,
)
_APPLE_EPISODE_RE = re.compile(r"[?&]i=(\d+)", re.IGNORECASE)
_SPOTIFY_PODCAST_RE = re.compile(
    r"open\.spotify\.com/(show|episode)/([\w]+)",
    re.IGNORECASE,
)

_ITUNES_API = "https://itunes.apple.com/lookup"
_TIMEOUT = 20.0

_RSS_NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
}


# ── Apple Podcasts ────────────────────────────────────────────────────────

def _resolve_apple_rss(podcast_id: str) -> Optional[str]:
    """Use iTunes API to get RSS feed URL for an Apple Podcast."""
    try:
        resp = httpx.get(
            _ITUNES_API,
            params={"id": podcast_id, "entity": "podcast"},
            timeout=_TIMEOUT,
            follow_redirects=True,
        )
        data = resp.json()
        results = data.get("results", [])
        if results:
            return results[0].get("feedUrl")
    except Exception as e:
        logger.warning("[Podcast] Apple API error: %s", e)
    return None


# ── RSS parsing ────────────────────────────────────────────────────────────

def _fetch_rss(feed_url: str) -> Optional[str]:
    """Fetch RSS XML, return text or None."""
    try:
        resp = httpx.get(
            feed_url,
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": "VidGrab/2.0 Podcast Downloader",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
        )
        if resp.status_code != 200:
            return None
        ct = resp.headers.get("content-type", "")
        if "html" in ct and "<rss" not in resp.text[:500]:
            return None
        return resp.text
    except Exception as e:
        logger.warning("[Podcast] Fetch RSS error for %s: %s", feed_url, e)
        return None


def _parse_rss_episodes(xml_text: str, episode_id: Optional[str] = None) -> list[dict]:
    """
    Parse RSS XML and return episode list.
    Each episode: {title, url, description, duration_s, pub_date, thumbnail, guid}
    If episode_id given, filter to just that episode.
    """
    episodes = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("[Podcast] RSS XML parse error: %s", e)
        return []

    channel = root.find("channel")
    if channel is None:
        channel = root  # Some feeds put items at root

    show_img = ""
    img_elem = channel.find("image")
    if img_elem is not None:
        show_img = img_elem.findtext("url") or ""
    itunes_img = channel.find("itunes:image", _RSS_NS)
    if itunes_img is not None:
        show_img = itunes_img.get("href", show_img)

    for item in channel.findall("item"):
        # Get audio URL from <enclosure>
        enclosure = item.find("enclosure")
        audio_url = ""
        if enclosure is not None:
            enc_type = enclosure.get("type", "")
            if "audio" in enc_type or enclosure.get("url", "").endswith((".mp3", ".m4a", ".ogg", ".aac", ".opus")):
                audio_url = enclosure.get("url", "")

        # Fallback: <media:content>
        if not audio_url:
            for mc in item.findall("media:content", _RSS_NS):
                if "audio" in mc.get("type", ""):
                    audio_url = mc.get("url", "")
                    break

        if not audio_url:
            continue

        guid = item.findtext("guid") or ""
        title = item.findtext("title") or ""
        desc = item.findtext("description") or item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or ""
        pub_date = item.findtext("pubDate") or ""
        duration_s = 0
        dur_text = item.findtext("itunes:duration", namespaces=_RSS_NS) or ""
        if dur_text:
            parts = dur_text.split(":")
            try:
                if len(parts) == 3:
                    duration_s = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    duration_s = int(parts[0]) * 60 + int(parts[1])
                else:
                    duration_s = int(parts[0])
            except ValueError:
                pass

        ep_img = show_img
        ep_img_elem = item.find("itunes:image", _RSS_NS)
        if ep_img_elem is not None:
            ep_img = ep_img_elem.get("href", show_img)

        ep = {
            "title": title,
            "url": audio_url,
            "description": desc[:500] if desc else "",
            "duration_s": duration_s,
            "duration_ms": duration_s * 1000,
            "pub_date": pub_date,
            "thumbnail": ep_img,
            "guid": guid,
        }

        if episode_id and episode_id not in (guid, title):
            continue

        episodes.append(ep)
        if episode_id and episodes:
            break

    return episodes


# ── Main extraction ────────────────────────────────────────────────────────

def extract_podcast(url: str, episode_index: int = 0) -> dict:
    """
    Extract a podcast episode audio URL.

    Parameters:
      url: RSS feed, Apple Podcasts, Spotify, or direct audio URL.
      episode_index: 0 = latest episode (default).

    Returns standard result dict.
    """
    url = url.strip()

    # Spotify podcast → delegate
    spotify_m = _SPOTIFY_PODCAST_RE.search(url)
    if spotify_m:
        return _handle_spotify_podcast(url, spotify_m.group(1), spotify_m.group(2))

    # Direct audio file
    if re.search(r"\.(mp3|m4a|ogg|aac|opus|flac|wav)(\?|$)", url, re.IGNORECASE):
        fname = url.split("/")[-1].split("?")[0]
        return {
            "platform": _PLATFORM,
            "title": fname,
            "author": "",
            "thumbnail": "",
            "duration_ms": 0,
            "formats": [{"quality": "audio", "codec": "mp3", "bitrate": 0, "url": url, "ext": "mp3"}],
            "direct_url": url,
            "source_type": "audio",
            "error_code": None,
            "error_message": None,
        }

    # Resolve Apple Podcasts → RSS
    feed_url = url
    episode_id = None
    apple_m = _APPLE_PODCAST_RE.search(url)
    if apple_m:
        podcast_id = apple_m.group(1)
        ep_m = _APPLE_EPISODE_RE.search(url)
        if ep_m:
            episode_id = ep_m.group(1)
        resolved = _resolve_apple_rss(podcast_id)
        if not resolved:
            return {
                "error_code": "podcast_rss_invalid",
                "error_message": "Không thể tìm thấy RSS feed từ Apple Podcasts. Thử dùng URL RSS trực tiếp.",
            }
        feed_url = resolved
        logger.info("[Podcast] Apple podcast %s → RSS: %s", podcast_id, feed_url)

    # Fetch RSS
    xml_text = _fetch_rss(feed_url)
    if not xml_text:
        return {
            "error_code": "podcast_rss_invalid",
            "error_message": f"Không thể tải RSS feed từ: {feed_url[:100]}",
        }

    episodes = _parse_rss_episodes(xml_text, episode_id)
    if not episodes:
        return {
            "error_code": "podcast_episode_not_found",
            "error_message": "Không tìm thấy episode audio trong RSS feed này.",
        }

    # Pick episode by index (0 = latest)
    ep = episodes[min(episode_index, len(episodes) - 1)]
    ext = "mp3"
    url_lower = ep["url"].lower()
    for candidate_ext in ("m4a", "ogg", "aac", "opus"):
        if candidate_ext in url_lower:
            ext = candidate_ext
            break

    return {
        "platform": _PLATFORM,
        "title": ep["title"],
        "author": "",
        "thumbnail": ep["thumbnail"],
        "duration_ms": ep["duration_ms"],
        "description": ep["description"],
        "pub_date": ep["pub_date"],
        "formats": [{"quality": "audio", "codec": ext, "bitrate": 0, "url": ep["url"], "ext": ext}],
        "direct_url": ep["url"],
        "source_type": "audio",
        "error_code": None,
        "error_message": None,
    }


def list_podcast_episodes(url: str, limit: int = 20) -> dict:
    """
    List episodes from a podcast RSS or Apple Podcasts URL.
    Returns bulk-channel compatible result.
    """
    feed_url = url
    apple_m = _APPLE_PODCAST_RE.search(url)
    if apple_m:
        resolved = _resolve_apple_rss(apple_m.group(1))
        if not resolved:
            return {
                "channel_title": "",
                "entries": [],
                "total_found": 0,
                "total_queued": 0,
                "error_code": "podcast_rss_invalid",
                "error_message": "Không tìm thấy RSS feed từ Apple Podcasts.",
            }
        feed_url = resolved

    xml_text = _fetch_rss(feed_url)
    if not xml_text:
        return {
            "channel_title": "",
            "entries": [],
            "total_found": 0,
            "total_queued": 0,
            "error_code": "podcast_rss_invalid",
            "error_message": "Không thể tải RSS feed.",
        }

    episodes = _parse_rss_episodes(xml_text)
    episodes = episodes[:limit]

    entries = [{"url": ep["url"], "title": ep["title"]} for ep in episodes]

    # Extract show title from XML
    show_title = ""
    try:
        root = ET.fromstring(xml_text)
        ch = root.find("channel")
        if ch is not None:
            show_title = ch.findtext("title") or ""
    except Exception:
        pass

    return {
        "channel_title": show_title,
        "entries": entries,
        "total_found": len(entries),
        "total_queued": len(entries),
        "error_code": None,
        "error_message": None,
    }


def _handle_spotify_podcast(url: str, _ep_type: str, _ep_id: str) -> dict:
    """Delegate Spotify podcast to spotify_service if available."""
    try:
        from app.services.spotify_service import extract_spotify_track
        result = extract_spotify_track(url)
        if result:
            return result
    except Exception as e:
        logger.warning("[Podcast] Spotify delegate failed: %s", e)
    return {
        "error_code": "podcast_episode_not_found",
        "error_message": "Spotify Podcast: không thể tải. Thử dùng URL RSS trực tiếp của podcast.",
    }


# ── BaseExtractor wrapper ──────────────────────────────────────────────────

from app.services.base_extractor import (
    BaseExtractor, BatchEntry, BatchResult, ExtractResult, FormatInfo,
)


class PodcastExtractor(BaseExtractor):
    platform_name = _PLATFORM
    supported_features = frozenset({"audio", "batch"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = False

    _URL_PATTERNS = _URL_PATTERNS
    _test_fixtures = [
        "https://feeds.simplecast.com/54nAGcIl",
    ]

    def detect_url(self, url: str) -> bool:
        lower = url.lower()
        if "podcasts.apple.com" in lower:
            return True
        if "open.spotify.com/show" in lower or "open.spotify.com/episode" in lower:
            return True
        if re.search(r"/(feed|rss|podcast)(?:\.xml|\.rss)?(?:\?|$)", lower):
            return True
        if re.search(r"\.(mp3|m4a|ogg|aac|opus)(\?|$)", lower):
            return True
        if "anchor.fm" in lower:
            return True
        # Known podcast feed hosts (feeds.simplecast.com, feeds.buzzsprout.com, etc.)
        if re.search(r"feeds\.(simplecast|buzzsprout|libsyn|captivate|podbean|transistor|megaphone|spreaker)\.com", lower):
            return True
        return False

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        episode_index = kwargs.get("episode_index", 0)
        try:
            raw = extract_podcast(url, episode_index)
        except Exception as e:
            return self._make_error_result("processing_failed", str(e))

        if raw.get("error_code"):
            return ExtractResult.error(self.platform_name, raw["error_code"], raw.get("error_message", ""))

        formats = [
            FormatInfo(
                quality=f.get("quality", "audio"),
                codec=f.get("codec", "mp3"),
                url=f.get("url", ""),
                ext=f.get("ext", "mp3"),
            )
            for f in raw.get("formats", [])
        ]
        return ExtractResult(
            platform=self.platform_name,
            source_type="audio",
            title=raw.get("title", ""),
            author=raw.get("author", ""),
            thumbnail=raw.get("thumbnail", ""),
            duration_ms=raw.get("duration_ms", 0),
            formats=formats,
            direct_url=raw.get("direct_url", ""),
            extraction_method="custom",
        )

    def extract_batch(self, url: str, limit: int = 20, **kwargs) -> BatchResult:
        try:
            raw = list_podcast_episodes(url, limit)
        except Exception as e:
            return self._make_error_batch("podcast_rss_invalid", str(e))

        if raw.get("error_code"):
            return BatchResult.error(self.platform_name, raw["error_code"], raw.get("error_message", ""))

        return BatchResult(
            platform=self.platform_name,
            channel_title=raw.get("channel_title", ""),
            entries=[BatchEntry(e["url"], e.get("title", "")) for e in raw.get("entries", [])],
            total_found=raw.get("total_found", 0),
            total_queued=raw.get("total_queued", 0),
        )


try:
    from app.services.extractor_registry import REGISTRY
    REGISTRY.register(PodcastExtractor())
except Exception as _e:
    logger.warning("Could not register PodcastExtractor: %s", _e)
