"""
The one URL → platform slug function.

Used for download stats (download_outcomes), job rows (download_jobs.platform)
and, since 2026-10-06, the per-platform daily download allowance
(app.core.quotas). It lived in app/api/routes.py as _get_platform_key; it moved
here so the Celery worker can use the same answer without importing the API
module (routes imports the worker — the reverse import would be circular).
routes.py still exposes it as _get_platform_key.

Anything not recognised is "other" — one bucket, deliberately: grouping by
registrable domain would make the number of quota counters unbounded.
"""

from urllib.parse import urlsplit

from app.core.twitter_host import is_twitter_url

# Every slug platform_key() can return, in display order, with the name shown
# to users ("… 20 lượt/ngày cho TikTok").
PLATFORM_LABELS = {
    "youtube":   "YouTube",
    "tiktok":    "TikTok",
    "instagram": "Instagram",
    "facebook":  "Facebook",
    "douyin":    "Douyin",
    "threads":   "Threads",
    "spotify":   "Spotify",
    "twitter":   "X (Twitter)",
    "linkedin":  "LinkedIn",
    "kuaishou":  "Kuaishou",
    "xiaohongshu": "Xiaohongshu",
    "bilibili":  "Bilibili",
    "iqiyi":     "iQIYI",
    "youku":     "Youku",
    "mgtv":      "Mango TV",
    "other":     "các trang khác",
}


# Matched on the real host (host == domain or a subdomain of it), never as a
# substring: "iq.com" or "b23.tv" inside another host or a query string must
# not count. Owner 2026-10-06: the per-platform allowance applies to every
# platform, so these must not share the single "other" bucket.
_HOST_PLATFORMS = (
    ("kuaishou", ("kuaishou.com", "kuaishou.cn", "chenzhongtech.com", "gifshow.com")),
    ("xiaohongshu", ("xiaohongshu.com", "xhslink.com", "xhslink.cn", "rednote.com")),
    ("bilibili", ("bilibili.com", "b23.tv", "bilibili.tv")),
    ("iqiyi", ("iq.com", "iqiyi.com")),
    ("youku", ("youku.com",)),
    ("mgtv", ("mgtv.com",)),
)


def _host(url: str) -> str:
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        return (urlsplit(raw).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def platform_key(url: str) -> str:
    u = (url or "").lower()
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "tiktok.com" in u:    return "tiktok"
    if "instagram.com" in u: return "instagram"
    if "facebook.com" in u or "fb.watch" in u: return "facebook"
    if "douyin.com" in u:    return "douyin"
    if "threads.net" in u or "threads.com" in u: return "threads"
    if "spotify.com" in u:   return "spotify"
    if is_twitter_url(u): return "twitter"
    if "linkedin.com" in u:  return "linkedin"
    host = _host(url)
    for slug, domains in _HOST_PLATFORMS:
        if any(host == d or host.endswith("." + d) for d in domains):
            return slug
    return "other"


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform or "other", PLATFORM_LABELS["other"])
