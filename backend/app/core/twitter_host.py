"""
Is this URL on Twitter/X?

Every caller used to test `"twitter.com" in url or "x.com" in url`. The second
half is a substring match, so dropbox.com, netflix.com, xbox.com... were all
treated as Twitter — counted under "twitter" in the stats and, in
downloader.py, sent down the Twitter-specific download branches. Found
2026-10-06 while splitting the admin "other" bucket by domain.
"""
from urllib.parse import urlsplit

_TWITTER_HOSTS = ("twitter.com", "x.com")


def is_twitter_url(url: str) -> bool:
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw  # bare "x.com/a/status/1" pasted by a user
    try:
        host = (urlsplit(raw).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _TWITTER_HOSTS)
