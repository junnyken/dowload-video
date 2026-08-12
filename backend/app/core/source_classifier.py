"""
Source Classifier — Phase 24 Universal Capture
===============================================
Classifies a canonical URL into (platform, source_type, normalized_id).

Source types:
  single_video  — one video file
  single_audio  — one audio/track
  single_post   — image / carousel / text post (may embed video)
  playlist      — ordered track/video list
  album         — music album
  artist        — music artist page
  channel       — video channel / uploads tab
  profile       — social profile / user page
  board         — Pinterest board
  subreddit     — Reddit community
  podcast_feed  — RSS/podcast feed
  unknown       — no pattern matched

Classification is pure regex, no network calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ClassifyResult:
    platform:      str            # e.g. "tiktok", "youtube"
    source_type:   str            # see module docstring
    normalized_id: Optional[str]  # video ID / playlist ID / username etc.
    confidence:    float          # 0.0–1.0


_UNKNOWN = ClassifyResult(
    platform="unknown", source_type="unknown", normalized_id=None, confidence=0.0
)

# ── Classification rules ──────────────────────────────────────────────────────
# Each entry: (platform_slug, compiled_regex, source_type, id_group_or_None)
# Order: most-specific first within each platform block.

_RULES: list[tuple[str, re.Pattern, str, Optional[int]]] = []

def _r(platform: str, pattern: str, source_type: str, id_group: Optional[int] = None) -> None:
    _RULES.append((platform, re.compile(pattern, re.I), source_type, id_group))


# ── YouTube ──────────────────────────────────────────────────────────────────
_r("youtube", r"(?:youtube\.com/watch\?.*v=|youtube\.com/embed/)([A-Za-z0-9_-]{11})", "single_video", 1)
_r("youtube", r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",                              "single_video", 1)
_r("youtube", r"youtube\.com/(?:playlist|watch)\?.*list=([A-Za-z0-9_-]+)",             "playlist",     1)
_r("youtube", r"youtube\.com/@([^/?#\s]+)",                                              "channel",      1)
_r("youtube", r"youtube\.com/(?:channel|c|user)/([^/?#\s]+)",                           "channel",      1)
_r("youtube", r"music\.youtube\.com/playlist\?list=([A-Za-z0-9_-]+)",                   "playlist",     1)
_r("youtube", r"music\.youtube\.com",                                                    "playlist",     None)

# ── TikTok ───────────────────────────────────────────────────────────────────
_r("tiktok",  r"tiktok\.com/@([^/?#\s]+)/video/(\d+)",     "single_video", 2)
_r("tiktok",  r"(?:vm|vt)\.tiktok\.com/([A-Za-z0-9]+)",    "single_video", 1)
_r("tiktok",  r"tiktok\.com/@([^/?#\s]+)/?(?:\?|$)",        "profile",      1)

# ── Douyin ───────────────────────────────────────────────────────────────────
_r("douyin",  r"douyin\.com/video/(\d{10,25})",             "single_video", 1)
_r("douyin",  r"douyin\.com/user/([A-Za-z0-9_-]+)",         "profile",      1)
_r("douyin",  r"v\.douyin\.com/([A-Za-z0-9]+)",             "single_video", 1)

# ── Instagram ─────────────────────────────────────────────────────────────────
_r("instagram", r"instagram\.com/p/([A-Za-z0-9_-]+)",                         "single_post",  1)
_r("instagram", r"instagram\.com/reels?/([A-Za-z0-9_-]+)",                    "single_video", 1)
_r("instagram", r"instagram\.com/stories/([^/?#\s]+)/(\d+)",                   "single_post",  1)
_r("instagram", r"instagram\.com/([^/?#\s]+)/saved",                           "profile",      1)
_r("instagram", r"instagram\.com/([^/?#\s]+)/?(?:\?|$)",                       "profile",      1)

# ── Facebook ─────────────────────────────────────────────────────────────────
_r("facebook", r"(?:facebook|fb)\.com/watch\?.*v=(\d+)",                       "single_video", 1)
_r("facebook", r"(?:facebook|fb)\.com/video\.php\?.*v=(\d+)",                  "single_video", 1)
_r("facebook", r"(?:facebook|fb)\.com/reels?/(\d+)",                           "single_video", 1)
_r("facebook", r"fb\.watch/([A-Za-z0-9]+)",                                     "single_video", 1)
_r("facebook", r"facebook\.com/[^/?#\s]+/videos/(\d+)",                        "single_video", 1)
_r("facebook", r"facebook\.com/[^/?#\s]+/videos/?(?:\?|$)",                    "channel",      None)
_r("facebook", r"facebook\.com/permalink\.php\?.*video_id=(\d+)",              "single_video", 1)

# ── Threads ──────────────────────────────────────────────────────────────────
_r("threads",  r"threads\.(?:net|com)/p/([A-Za-z0-9_-]+)",                     "single_post",  1)
_r("threads",  r"threads\.(?:net|com)/@([^/?#\s]+)/?(?:\?|$)",                  "profile",      1)

# ── Pinterest ─────────────────────────────────────────────────────────────────
_r("pinterest", r"pinterest\.[a-z]+/pin/(\d+)",                                 "single_post",  1)
_r("pinterest", r"pinterest\.[a-z]+/([^/?#\s]+)/([^/?#\s]+)/?(?:\?|$)",        "board",        None)
_r("pinterest", r"pinterest\.[a-z]+/([^/?#\s]+)/?(?:\?|$)",                    "profile",      1)

# ── SoundCloud ───────────────────────────────────────────────────────────────
_r("soundcloud", r"soundcloud\.com/([^/?#\s]+)/sets/([^/?#\s]+)",              "playlist",     None)
_r("soundcloud", r"soundcloud\.com/([^/?#\s]+)/([^/?#\s]+)(?:\?|$)",           "single_audio", None)
_r("soundcloud", r"soundcloud\.com/([^/?#\s]+)/?(?:\?|$)",                     "artist",       1)

# ── Spotify ──────────────────────────────────────────────────────────────────
_r("spotify",  r"open\.spotify\.com/(?:[a-z]{2}(-[A-Z]{2})?/)?track/([A-Za-z0-9]+)",    "single_audio", 2)
_r("spotify",  r"open\.spotify\.com/(?:[a-z]{2}(-[A-Z]{2})?/)?playlist/([A-Za-z0-9]+)", "playlist",     2)
_r("spotify",  r"open\.spotify\.com/(?:[a-z]{2}(-[A-Z]{2})?/)?album/([A-Za-z0-9]+)",    "album",        2)
_r("spotify",  r"open\.spotify\.com/(?:[a-z]{2}(-[A-Z]{2})?/)?artist/([A-Za-z0-9]+)",   "artist",       2)

# ── Reddit ────────────────────────────────────────────────────────────────────
_r("reddit",   r"(?:www\.)?reddit\.com/r/([^/?#\s]+)/comments/([A-Za-z0-9]+)", "single_post",  None)
_r("reddit",   r"redd\.it/([A-Za-z0-9]+)",                                      "single_post",  1)
_r("reddit",   r"(?:www\.)?reddit\.com/r/([^/?#\s]+)/?(?:\?|$)",                "subreddit",    1)
_r("reddit",   r"(?:www\.)?reddit\.com/u(?:ser)?/([^/?#\s]+)/?(?:\?|$)",        "profile",      1)

# ── Twitter / X ──────────────────────────────────────────────────────────────
_r("twitter",  r"(?:twitter|x)\.com/([^/?#\s]+)/status/(\d+)",                 "single_post",  None)
_r("twitter",  r"(?:twitter|x)\.com/([^/?#\s]+)/media/?(?:\?|$)",              "profile",      1)
_r("twitter",  r"(?:twitter|x)\.com/([^/?#\s]+)/?(?:\?|$)",                    "profile",      1)

# ── Bilibili ──────────────────────────────────────────────────────────────────
_r("bilibili", r"bilibili\.com/video/([A-Za-z]{2}\d+)",                         "single_video", 1)
_r("bilibili", r"space\.bilibili\.com/(\d+)",                                    "profile",      1)
_r("bilibili", r"bilibili\.com/list/([A-Za-z0-9]+)",                             "playlist",     1)
_r("bilibili", r"bilibili\.com/bangumi/play/([A-Za-z0-9]+)",                    "single_video", 1)
_r("bilibili", r"b23\.tv/([A-Za-z0-9]+)",                                        "single_video", 1)

# ── Xiaohongshu / RedNote ─────────────────────────────────────────────────────
_r("xiaohongshu", r"xiaohongshu\.com/explore/([A-Za-z0-9]+)",                  "single_post",  1)
_r("xiaohongshu", r"xiaohongshu\.com/user/profile/([A-Za-z0-9]+)",             "profile",      1)
_r("xiaohongshu", r"xhslink\.com/([A-Za-z0-9]+)",                               "single_post",  1)

# ── Lemon8 ───────────────────────────────────────────────────────────────────
_r("lemon8",   r"lemon8-app\.com/([^/?#\s]+)/(\d+)",                            "single_post",  2)
_r("lemon8",   r"lemon8-app\.com/([^/?#\s]+)/?(?:\?|$)",                        "profile",      1)

# ── Snapchat ─────────────────────────────────────────────────────────────────
_r("snapchat", r"snapchat\.com/spotlight/([A-Za-z0-9_-]+)",                     "single_video", 1)
_r("snapchat", r"story\.snapchat\.com/s/([A-Za-z0-9_-]+)",                      "single_video", 1)
_r("snapchat", r"snapchat\.com",                                                  "single_post",  None)

# ── VK ───────────────────────────────────────────────────────────────────────
_r("vk",       r"vk\.com/video-?\d+_\d+",                                       "single_video", None)
_r("vk",       r"vkvideo\.ru/video-?\d+_\d+",                                   "single_video", None)
_r("vk",       r"vk\.com/([^/?#\s]+).*\?z=video",                               "single_video", None)
_r("vk",       r"vk\.com/video/?(?:\?|$)",                                       "channel",      None)
_r("vk",       r"vk\.com/([^/?#\s]+)/?(?:\?|$)",                                "profile",      1)

# ── Twitch ───────────────────────────────────────────────────────────────────
_r("twitch",   r"twitch\.tv/videos/(\d+)",                                       "single_video", 1)
_r("twitch",   r"clips\.twitch\.tv/([A-Za-z0-9_-]+)",                           "single_video", 1)
_r("twitch",   r"twitch\.tv/([^/?#\s]+)/clip/([A-Za-z0-9_-]+)",                 "single_video", None)
_r("twitch",   r"twitch\.tv/([^/?#\s]+)/?(?:\?|$)",                              "channel",      1)

# ── Rumble ───────────────────────────────────────────────────────────────────
_r("rumble",   r"rumble\.com/v([A-Za-z0-9-]+)\.html",                           "single_video", 1)
_r("rumble",   r"rumble\.com/c/([^/?#\s]+)",                                     "channel",      1)

# ── Odysee / LBRY ────────────────────────────────────────────────────────────
_r("odysee",   r"odysee\.com/@([^/?#\s]+):[a-z0-9]+/([^/?#\s]+):[a-z0-9]+",   "single_video", None)
_r("odysee",   r"odysee\.com/@([^/?#\s]+)(?::[a-z0-9]+)?/?(?:\?|$)",           "channel",      1)

# ── Dailymotion ──────────────────────────────────────────────────────────────
_r("dailymotion", r"dailymotion\.com/video/([A-Za-z0-9]+)",                     "single_video", 1)
_r("dailymotion", r"dai\.ly/([A-Za-z0-9]+)",                                     "single_video", 1)
_r("dailymotion", r"dailymotion\.com/[^/?#\s]+/playlist/([^/?#\s]+)",           "playlist",     1)
_r("dailymotion", r"dailymotion\.com/[^/?#\s]+",                                "channel",      None)

# ── Podcast RSS ──────────────────────────────────────────────────────────────
_r("podcast",  r"podcasts\.apple\.com",                                           "podcast_feed", None)
_r("podcast",  r"feeds?\.[^/?#\s]+",                                             "podcast_feed", None)
_r("podcast",  r"/(?:rss|feed)(?:/|\.xml|\?|$)",                                 "podcast_feed", None)
_r("podcast",  r"\.xml(?:\?|$)",                                                  "podcast_feed", None)


def classify(url: str) -> ClassifyResult:
    """
    Classify a URL into (platform, source_type, normalized_id).
    Uses first-match-wins; returns UNKNOWN if nothing matches.
    """
    for platform, pattern, source_type, id_group in _RULES:
        m = pattern.search(url)
        if m:
            try:
                nid = m.group(id_group) if id_group is not None else None
            except IndexError:
                nid = None
            return ClassifyResult(
                platform=platform,
                source_type=source_type,
                normalized_id=nid,
                confidence=1.0,
            )
    return _UNKNOWN
