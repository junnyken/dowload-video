"""
Active platform probes — "does this platform still work at all?"
================================================================
lane_observer already answers a different question: is our capacity under
pressure (cookies blocked, circuit open, throttle near limit). It derives that
from traffic, which means a platform nobody is currently using looks healthy
because it is silent — indistinguishable from how it looks the moment it breaks.
Of 21 platforms on the public list, the live API reported 5.

This module asks the platform directly, on a schedule, whether extraction still
works. Metadata only: yt-dlp with download=False, no bytes, no Cobalt, no Apify,
no proxy spend. It will not catch "metadata resolves but the media 404s", and
says so rather than implying full coverage — real user failures cover that side,
via the fetch_failed / download_failed events.

WHY MOST TARGETS SHIP EMPTY (2026-10-13: all free platforms now have one)
---------------------------
A probe needs a sample URL per platform that is public and stable. Inventing
twenty of them would produce a monitor that reports false failures on day one,
and a monitor people learn to ignore is worse than no monitor. So only targets
that are genuinely known-stable ship as defaults; everything else reports
`not_configured`, which is neither healthy nor failed, and the operator fills
them in through the admin without a deploy.

Exception (task #6332): Douyin/Xiaohongshu can reach a paid provider. They
have no default target, run only when the admin sets one, at most once per
their minimum interval (24 h default) — see PAID_PROBE_PLATFORMS.

Configured targets live in Redis under `probe:targets` as a JSON object
{platform: url}, overriding the defaults below.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
from typing import Any, Dict, Optional

from app.core.redis_client import get_redis

# Mirrors the user-facing list in frontend PlatformsPage.
PROBE_PLATFORMS = [
    "youtube", "tiktok", "douyin", "facebook", "instagram", "twitter",
    "threads", "reddit", "pinterest", "bilibili", "xiaohongshu", "lemon8",
    "snapchat", "vk", "twitch", "rumble", "odysee", "dailymotion",
    "soundcloud", "spotify", "podcast",
]

# Only URLs whose stability is not a guess. Everything else is left to the
# operator on purpose — see the module docstring.
_DEFAULT_TARGETS: Dict[str, str] = {
    # The first video ever uploaded to YouTube, April 2005, still public.
    "youtube": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    # Verified by running the real probe against it on 2026-09-24, not assumed.
    "vk": "https://vk.com/video-22822305_456241864",
    # Added 2026-10-02. Each is a post by the platform's OWN official account,
    # found from that account's listing (not guessed), and each returned 200
    # with a title and a media URL from the live /api/v1/fetch-link — the same
    # extract_video_info_sync entry point probe_once uses.
    #   @Odysee channel — "Call an Ambulance!" (8 s video). Replaced 2026-10-13
    #   (task #6332): the old target, "VIDEO: The Future of Odysee", still
    #   resolves locally (yt-dlp 2026.08.19: 1080p, 344 s) but is a 344 s
    #   video the probe downloads at 480p, and the live probe kept failing on
    #   it. Listed from the channel's own claim_search; yt-dlp resolved it
    #   (720p, 8 s) on 2026-10-13.
    "odysee": "https://odysee.com/@Odysee:8/call-an-ambulance:6",
    #   soundcloud.com/soundcloud — "Upload Your First Track" (4.5s)
    "soundcloud": "https://soundcloud.com/soundcloud/upload-your-first-track",
    #   space.bilibili.com/8047632 (哔哩哔哩弹幕网) — "欢 迎 来 到 A G I 时 代" (6.3s)
    "bilibili": "https://www.bilibili.com/video/BV1ECeJ65EZS",
    #   @threads — "You can now DM videos, GIFS and stickers on Threads" (5.6s)
    "threads": "https://www.threads.com/@threads/post/DOJPyNPEVvT",
    #   api.dailymotion.com/user/dailymotion — "T'as le projet…" DayOne (59 s).
    #   Replaced 2026-10-08: x5e9eog ("Dailymotion Hackathon Feb 2017") now
    #   answers 403 "This user is not allowed to access this video" (the live
    #   probe failed on it). Listed from the account's own video list.
    "dailymotion": "https://www.dailymotion.com/video/xb6qrbe",
    #   A clip on twitch.tv/twitch — "F1 2017 E3 Gameplay!" (2.4s). A clip, not
    #   a VOD: the official TwitchCon VOD made /fetch-link run past 150s
    #   (it downloads the whole broadcast), unusable as a probe.
    "twitch": "https://www.twitch.tv/twitch/clip/CrispyJollyGullHassaanChop-nPlLKGxGRcBj37e4",
    # Added 2026-10-08 (task #6170). Official accounts only, short clips (the
    # probe takes the real download path, Cobalt included, every 30 min). Each
    # resolved with yt-dlp on 2026-10-08; the first three come from yt-dlp's
    # own extractor tests, which is where their age/stability shows.
    #   Instagram: NOT an official account. @instagram's posts found
    #   (yt-dlp tests: reel Chunk8-jurw, tv/BkfuX9UB-eK, p/BQ0eAlwhDrw) all fail
    #   the real path — Cobalt answers the reel with a .jpg (first live probe
    #   08/10 10:40Z: failed), the IGTV with error.api.fetch.empty, the post
    #   with a picker. This public reel is one users downloaded on 08/10;
    #   Cobalt answers it with an .mp4 redirect.
    "instagram": "https://www.instagram.com/reel/DeJjs2ipNPr/",
    #   CNN's page — "Holocaust survivor becomes US citizen" (44 s). No short
    #   video by Meta's own page could be listed without signing in.
    "facebook": "https://www.facebook.com/cnn/videos/10155529876156509/",
    #   @CaptainAmerica (Marvel) — 3 s, posted 2016. @X's own test post no
    #   longer carries a video.
    "twitter": "https://x.com/CaptainAmerica/status/719944021058060289",
    #   @nba — 6 s, posted 2024-07. @tiktok's own videos could not be listed.
    "tiktok": "https://www.tiktok.com/@nba/video/7390205678729579807",
    # Added 2026-10-13 (task #6332). Each checked from the workspace on that
    # day through the code path the app uses for that platform.
    #   Rumble: yt-dlp's own extractor test URL (online since 2021), 103 s.
    #   Resolved with Chrome impersonation; WITHOUT it the workspace IP gets
    #   403 — if the live probe fails with 403, that is the server path, not
    #   the target. Rumble's own channel could not be listed.
    "rumble": "https://rumble.com/vdmum1-moose-the-dog-helps-girls-dig-a-snow-fort.html",
    #   Snapchat: public Spotlight from yt-dlp's extractor tests (4.7 s);
    #   app.services.snapchat_extractor returned a title and a direct URL.
    "snapchat": "https://www.snapchat.com/spotlight/W7_EDlXWTBiXAEEniNoMPwAAYYWtidGhudGZpAX1TKn0JAX1TKnXJAAAAAA",
    #   Lemon8: the official @lemon8_us account, its one VIDEO post among the
    #   20 on its profile (the rest are image posts);
    #   app.services.lemon8_extractor returned source_type=video + direct URL.
    "lemon8": "https://www.lemon8-app.com/@lemon8_us/7586892375096623671?region=us",
    #   Podcast: NPR "Up First" RSS feed (daily since 2017). The probe takes
    #   the newest episode; app.services.podcast_extractor returned a title
    #   and an audio URL.
    "podcast": "https://feeds.npr.org/510318/podcast.xml",
    #   Spotify: Rick Astley — "Never Gonna Give You Up". The app does not
    #   download from Spotify: it reads the track page (free embed scrape,
    #   spotify_service.get_track_info_async returned the name) and then
    #   finds the audio on SoundCloud/YouTube. The probe covers that chain.
    "spotify": "https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8",
    #   Pinterest: pin from yt-dlp's extractor tests ("Origami", 58 s);
    #   yt-dlp resolved it anonymously (1080p+) on 2026-10-13. Production
    #   already probes Pinterest through an admin-set target, which keeps
    #   overriding this default.
    "pinterest": "https://www.pinterest.com/pin/664281013778109217/",
}

# Platforms whose real path can reach a PAID managed provider (Apify, through
# the China access layer). They ship WITHOUT a default target, on purpose:
#   * a probe only reaches the paid provider when CHINA_ACCESS_MANAGED_PROBES_ENABLED
#     is on (registry.managed_allowed_for refuses origin="probe" otherwise).
#     With it off — the default — a Douyin probe runs the native route only,
#     which fails for every public video (0/1245 on prod), so a default
#     target would raise a red "degraded" banner while users are served fine;
#   * when it is on, every paid call still goes through the provider router's
#     budget_guard.precheck + reserve (requester "probe": the guest per-day
#     limit, the platform and provider daily ceilings, the monthly ceiling).
# The admin can set a target per platform; it is then probed at most once per
# its minimum interval (default 24 h, below), enforced through Redis.
PAID_PROBE_PLATFORMS = frozenset({"douyin", "xiaohongshu", "kuaishou"})
NOT_CONFIGURED_REASON: Dict[str, str] = {
    "douyin": "paid_probe_admin_opt_in",
    "xiaohongshu": "paid_probe_admin_opt_in",
    # Not paid, but anonymous extraction needs a signed-in cookie (yt-dlp from
    # the workspace, 2026-10-13: "Account authentication is required" on its
    # own test URL). Production probes Reddit through an admin-set target.
    "reddit": "needs_cookie_admin_target",
}

# Minimum seconds between two probes of one platform. 0 = every scheduled
# sweep (30 min). Admin-settable per platform (Redis `probe:min_interval`).
PAID_MIN_INTERVAL_SEC = 86400
_PAID_MIN_INTERVAL_FLOOR = 3600     # an admin value below this is raised to it
_MIN_INTERVAL_KEY = "probe:min_interval"          # JSON {platform: seconds}
_LAST_RUN_KEY = "probe:last_run:{platform}"       # SET NX EX — the claim
_LIMITATION_KEY = "probe:known_limitation"        # JSON {platform: text}
_LIMITATION_MAX = 300

_TARGETS_KEY = "probe:targets"
_STATE_KEY = "probe:state"          # hash: platform -> json
# Generous on purpose: VK answered a real probe URL in 50s once (cold) and 3s
# right after; Odysee took 18s, 35s and then >60s on the same URL, and a 60s
# cap turned that slowness into a false outage banner. A hung extractor must
# still not stall the whole sweep (10 probes x 120s < the 30-min schedule).
_PROBE_TIMEOUT_SEC = int(os.getenv("PROBE_TIMEOUT_SEC", "120"))
# One failed probe is not an outage: platforms flap (slow cold start, a single
# throttled request). A platform is reported failed — user banner + Telegram —
# only after this many consecutive failed probes.
_FAIL_CONFIRM = max(1, int(os.getenv("PROBE_FAIL_CONFIRM", "2")))
_STALE_AFTER_SEC = 3 * 3600         # a result older than this is not evidence

# Result statuses. `not_configured` is deliberately distinct from both ok and
# failed: "we never looked" must never render as "we looked and it was fine".
OK = "ok"
FAILED = "failed"
NOT_CONFIGURED = "not_configured"
STALE = "stale"
# Last probe failed but not yet confirmed by a consecutive failure. Not
# reported as ok (we just saw a failure) nor as failed (one failure is noise).
UNCONFIRMED = "unconfirmed"


def get_targets() -> Dict[str, str]:
    """Configured targets, falling back to the built-in defaults."""
    targets = dict(_DEFAULT_TARGETS)
    try:
        raw = get_redis().get(_TARGETS_KEY)
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode()
            stored = json.loads(raw)
            if isinstance(stored, dict):
                for k, v in stored.items():
                    if isinstance(v, str) and v.strip():
                        targets[k] = v.strip()
                    elif k in targets:
                        # Explicit empty clears a default rather than keeping it.
                        targets.pop(k, None)
    except Exception:
        pass
    return targets


def set_target(platform: str, url: Optional[str]) -> Dict[str, str]:
    """Set or clear one platform's probe URL. Returns the full target map."""
    if platform not in PROBE_PLATFORMS:
        raise ValueError(f"Unknown platform: {platform}")
    rc = get_redis()
    try:
        raw = rc.get(_TARGETS_KEY)
        if isinstance(raw, bytes):
            raw = raw.decode()
        stored = json.loads(raw) if raw else {}
        if not isinstance(stored, dict):
            stored = {}
    except Exception:
        stored = {}
    stored[platform] = (url or "").strip()
    rc.set(_TARGETS_KEY, json.dumps(stored))
    return get_targets()


def _read_json_map(key: str) -> Dict[str, Any]:
    """A Redis string holding a JSON object; {} when absent. Raises on Redis
    failure so callers that must fail closed can."""
    raw = get_redis().get(key)
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        val = json.loads(raw) if raw else {}
    except Exception:
        val = {}
    return val if isinstance(val, dict) else {}


def default_min_interval(platform: str) -> int:
    return PAID_MIN_INTERVAL_SEC if platform in PAID_PROBE_PLATFORMS else 0


def get_min_interval(platform: str, _stored: Optional[Dict[str, Any]] = None) -> int:
    """Seconds that must pass between two probes of `platform`.

    Paid platforms fail closed: if Redis cannot be read they keep the 24 h
    default, and an admin value is never allowed below one hour.
    """
    value = default_min_interval(platform)
    try:
        stored = (_stored if _stored is not None else _read_json_map(_MIN_INTERVAL_KEY)).get(platform)
        if stored is not None:
            value = max(0, int(stored))
    except Exception:
        pass
    if platform in PAID_PROBE_PLATFORMS:
        value = max(value, _PAID_MIN_INTERVAL_FLOOR)
    return value


def set_min_interval(platform: str, seconds: Optional[int]) -> int:
    """Set one platform's minimum interval; None restores the default."""
    if platform not in PROBE_PLATFORMS:
        raise ValueError(f"Unknown platform: {platform}")
    stored = _read_json_map(_MIN_INTERVAL_KEY)
    if seconds is None:
        stored.pop(platform, None)
    else:
        stored[platform] = max(0, int(seconds))
    get_redis().set(_MIN_INTERVAL_KEY, json.dumps(stored))
    return get_min_interval(platform)


def claim_probe_slot(platform: str) -> bool:
    """May `platform` be probed now? Claims the slot when it may.

    Platforms without a minimum interval always may. For the others the claim
    is one `SET key NX EX interval` in Redis, so it holds across worker
    restarts and two concurrent sweeps cannot both win it. The claim is taken
    BEFORE the probe runs: a probe that crashes half-way still counts, which is
    the safe side for a paid provider. Any Redis error → no probe (fail
    closed); for a paid platform that is the only acceptable answer.
    """
    interval = get_min_interval(platform)
    if interval <= 0:
        return True
    try:
        won = get_redis().set(_LAST_RUN_KEY.format(platform=platform),
                              str(int(time.time())), nx=True, ex=interval)
        return bool(won)
    except Exception:
        return False


def get_known_limitations() -> Dict[str, str]:
    try:
        stored = _read_json_map(_LIMITATION_KEY)
    except Exception:
        return {}
    return {k: v for k, v in stored.items() if isinstance(v, str) and v.strip()}


def set_known_limitation(platform: str, text: Optional[str]) -> str:
    """Set (or clear, with empty/None) the short note shown next to a platform's
    status, e.g. "Chỉ tải được video công khai". Returns the stored text."""
    if platform not in PROBE_PLATFORMS:
        raise ValueError(f"Unknown platform: {platform}")
    clean = " ".join((text or "").split())[:_LIMITATION_MAX]
    stored = _read_json_map(_LIMITATION_KEY)
    if clean:
        stored[platform] = clean
    else:
        stored.pop(platform, None)
    get_redis().set(_LIMITATION_KEY, json.dumps(stored, ensure_ascii=False))
    return clean


def _iso(ts: Any) -> Optional[str]:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts)))
    except Exception:
        return None


# Platforms whose users are served through Cobalt-first / TikWM: probe them
# with the default quality so the probe takes THAT path. Everyone else is
# probed at 480p: probe_once downloads the file (quality "video" is not
# metadata-only, despite the history above), and a best-quality probe meant
# ~35 MB of 4K Dailymotion every 30 min. "video_fast" would be metadata-only
# but has a 30 s cap (VK/Odysee need ~47 s) and is cached (task #6170).
_FULL_PATH_PLATFORMS = frozenset({"instagram", "facebook", "twitter", "tiktok"})


def probe_quality(platform: str) -> str:
    if platform in _FULL_PATH_PLATFORMS:
        return "video"
    return (os.getenv("PROBE_QUALITY") or "video_480").strip() or "video_480"


def probe_once(url: str, timeout: int = _PROBE_TIMEOUT_SEC, quality: str = "video") -> Dict[str, Any]:
    """
    Ask the platform whether extraction still works, THROUGH THE PATH THE APP
    ACTUALLY USES.

    The first version shelled out to bare yt-dlp, and its very first real run
    got TikTok wrong in the most damaging direction: it reported "Your IP
    address is blocked from accessing this post" and would have marked the
    platform broken and alerted, while /fetch-link returned a title, thumbnail
    and direct_mp4_url for the same video without trouble. The app does not
    reach TikTok with bare yt-dlp — it goes through TikWM, whose servers fetch
    on our behalf, so an IP block on ours never touches it. A monitor that
    cries wolf gets muted, and takes the next real outage with it.

    So the probe now calls extract_video_info_sync, the same entry point
    /fetch-link uses, and therefore the same fallback chain: TikWM, cookie
    pool, proxy, Cobalt. What it reports is what a user would have experienced.

    Still metadata only — _extract_video_info_impl's own docstring says "Uses
    PROXY ONLY for metadata extraction, not file download", and every yt-dlp
    call inside it passes download=False. No bytes, so the sweep stays cheap.
    The caveat worth stating: a platform that falls through to a paid provider
    for metadata costs a little per probe. That was not true of the bare
    yt-dlp version, and it is the price of measuring the real path.

    Never raises: a probe that throws reports nothing, and silence is
    indistinguishable from health.
    """
    started = time.time()

    def _done(ok: bool, reason: Optional[str], title: Optional[str] = None) -> Dict[str, Any]:
        # The user-facing message is ~200 chars of advice before the yt-dlp
        # cause, so the truncation below kept only the advice: 4 probes stayed
        # red on 13/10 with no stored cause (task #6332). Cause first.
        if reason and "(Lý do kỹ thuật:" in reason:
            head, _, tech = reason.partition("(Lý do kỹ thuật:")
            reason = tech.strip().rstrip(")").strip() + " | " + head.strip()
        return {
            "ok": ok,
            # Truncated: extractor errors can embed a whole HTML page, and this
            # is stored in Redis and rendered in the admin.
            "reason": reason[:200] if reason else None,
            "ms": int((time.time() - started) * 1000),
            "title": title,
        }

    try:
        from app.services.downloader import extract_video_info_sync
    except Exception as exc:
        return _done(False, f"probe_unavailable: {type(exc).__name__}: {exc}")

    # Bounded: run the extractor on a worker thread and stop waiting after
    # `timeout`. The thread cannot be killed, but the sweep moves on and the
    # platform is recorded as a (timeout) failure instead of hanging forever.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="probe")
    try:
        # run_as_probe marks the context origin="probe" so the China access
        # layer never spends on a managed provider for a probe (plan §14.1).
        from app.services.china_platforms.probes import run_as_probe
        future = pool.submit(run_as_probe, extract_video_info_sync, url, quality=quality)
        info = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return _done(False, f"timeout_after_{timeout}s")
    except Exception as exc:
        return _done(False, f"{type(exc).__name__}: {exc}")
    finally:
        pool.shutdown(wait=False)

    if not isinstance(info, dict) or not info:
        return _done(False, "no_metadata_returned")
    if not info.get("success", True):
        return _done(False, str(info.get("error") or info.get("message") or "extraction_failed"))
    title = info.get("title")
    # A response carrying neither a title nor a playable URL is not a success
    # however it labelled itself.
    if not title and not (info.get("direct_mp4_url") or info.get("local_file_path")):
        return _done(False, "no_title_or_media_url")
    return _done(True, None, (title or "")[:120] or None)


def record_probe(platform: str, result: Dict[str, Any]) -> str:
    """
    Store one probe result and return the status it produced.

    Failures are counted: the status only becomes FAILED once `_FAIL_CONFIRM`
    probes in a row have failed. Before that it is UNCONFIRMED, which the
    public status renders as unknown — no red banner, no alert — because a
    single slow or throttled request is not an outage.
    """
    prev: Dict[str, Any] = {}
    rc = None
    try:
        rc = get_redis()
        raw = rc.hget(_STATE_KEY, platform)
        if raw:
            prev = json.loads(raw.decode() if isinstance(raw, bytes) else raw) or {}
    except Exception:
        prev = {}

    now = int(time.time())
    # Last SUCCESSFUL probe, carried across failures: "when did we last see
    # it work" is the useful fact once it is broken.
    verified_at = prev.get("verified_at")
    if result.get("ok"):
        streak = 0
        status = OK
        verified_at = now
    else:
        streak = int(prev.get("fail_streak") or 0) + 1
        status = FAILED if streak >= _FAIL_CONFIRM else UNCONFIRMED

    payload = {
        "status": status,
        "reason": result.get("reason"),
        "ms": result.get("ms"),
        "title": result.get("title"),
        "at": now,
        "fail_streak": streak,
        "verified_at": verified_at,
    }
    try:
        (rc or get_redis()).hset(_STATE_KEY, platform, json.dumps(payload))
    except Exception:
        pass
    return status


def get_probe_states() -> Dict[str, Dict[str, Any]]:
    """
    Current probe state for every platform on the list.

    A platform with no target reports not_configured; one whose last result is
    older than _STALE_AFTER_SEC reports stale. Neither is reported as healthy —
    an old success is not evidence about now.
    """
    targets = get_targets()
    stored: Dict[str, Any] = {}
    try:
        raw = get_redis().hgetall(_STATE_KEY) or {}
        for k, v in raw.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            try:
                stored[key] = json.loads(val)
            except Exception:
                pass
    except Exception:
        pass

    limitations = get_known_limitations()
    try:
        intervals = _read_json_map(_MIN_INTERVAL_KEY)
    except Exception:
        intervals = {}
    now = int(time.time())
    out: Dict[str, Dict[str, Any]] = {}
    for platform in PROBE_PLATFORMS:
        target = targets.get(platform)
        rec = stored.get(platform) or {}
        interval = get_min_interval(platform, intervals)
        extra = {
            # Kept even with no target: a success recorded before the target
            # was cleared is still the last time it was seen working.
            "last_verified_at": _iso(rec.get("verified_at")) if rec.get("verified_at") else None,
            "known_limitation": limitations.get(platform, ""),
            "min_interval_s": interval,
            "paid": platform in PAID_PROBE_PLATFORMS,
        }
        if not target:
            out[platform] = {"status": NOT_CONFIGURED, "target": None,
                             "reason": NOT_CONFIGURED_REASON.get(platform),
                             "at": None, "ms": None, "age_s": None, **extra}
            continue
        if not rec.get("at"):
            out[platform] = {"status": STALE, "target": target, "reason": "never_probed",
                             "at": None, "ms": None, "age_s": None, **extra}
            continue
        age = now - int(rec["at"])
        status = rec.get("status")
        # A platform probed once a day is not stale after 3 hours.
        if age > max(_STALE_AFTER_SEC, interval + _STALE_AFTER_SEC):
            status = STALE
        out[platform] = {
            "status": status,
            "target": target,
            "reason": rec.get("reason") if status != STALE else f"last_result_{age}s_old",
            "at": rec.get("at"),
            "ms": rec.get("ms"),
            "age_s": age,
            **extra,
        }
    return out
