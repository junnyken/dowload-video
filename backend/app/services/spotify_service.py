import os
import re
import json
import httpx
import asyncio
from typing import Optional

from app.core import spotify_artist_ops as _ops

SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")

# ── User-Agent for scraping ──────────────────────────────────────────
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def is_spotify_url(url: str) -> bool:
    u = (url or "").strip()
    return "open.spotify.com" in u or u.startswith("spotify:")


# 4 supported entity types
SPOTIFY_TYPES = ("track", "album", "playlist", "artist")


def parse_spotify_url(url: str) -> dict:
    """
    Parse any Spotify URL/URI into a normalized descriptor.

    Accepts:
      - https://open.spotify.com/{type}/{id}
      - https://open.spotify.com/intl-vi/{type}/{id}   (localized prefix)
      - …/{type}/{id}?si=...                            (tracking params)
      - spotify:{type}:{id}                             (URI form)

    Returns {platform, source_type, spotify_id, canonical_url}.
    Raises ValueError("unsupported_spotify_url") for anything else.
    """
    u = (url or "").strip()

    # URI form: spotify:track:ID
    m = re.match(r"spotify:(track|album|playlist|artist):([A-Za-z0-9]+)", u)
    if m:
        t, sid = m.group(1), m.group(2)
        return {
            "platform": "spotify",
            "source_type": t,
            "spotify_id": sid,
            "canonical_url": f"https://open.spotify.com/{t}/{sid}",
        }

    # Web form (optional /intl-xx/ locale prefix, optional query string)
    m = re.search(
        r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist|artist)/([A-Za-z0-9]+)",
        u,
    )
    if m:
        t, sid = m.group(1), m.group(2)
        return {
            "platform": "spotify",
            "source_type": t,
            "spotify_id": sid,
            "canonical_url": f"https://open.spotify.com/{t}/{sid}",
        }

    raise ValueError("unsupported_spotify_url")


def _extract_spotify_type_and_id(url: str) -> tuple[str, str]:
    """Returns (type, id) for track/album/playlist/artist. Backward-compatible
    thin wrapper over parse_spotify_url (kept for existing callers)."""
    p = parse_spotify_url(url)
    return p["source_type"], p["spotify_id"]


# ── Embed Scraping (works without API key or Premium) ────────────────

async def _scrape_embed_data(sp_type: str, sp_id: str) -> dict:
    """
    Scrape Spotify's embed page to extract __NEXT_DATA__ JSON.
    This contains the full entity data including trackList for
    playlists and albums — no API key or Premium required.
    """
    embed_url = f"https://open.spotify.com/embed/{sp_type}/{sp_id}"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(embed_url, headers={"User-Agent": _UA})
        if resp.status_code != 200:
            raise ValueError(
                f"Không thể truy cập Spotify embed (HTTP {resp.status_code}). "
                "Vui lòng kiểm tra lại link."
            )
        html = resp.text

    # Extract __NEXT_DATA__ JSON
    match = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise ValueError("Không thể đọc dữ liệu từ Spotify embed page.")

    try:
        next_data = json.loads(match.group(1))
    except json.JSONDecodeError:
        raise ValueError("Dữ liệu Spotify embed không hợp lệ.")

    entity = (
        next_data.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("data", {})
        .get("entity", {})
    )
    if not entity:
        raise ValueError("Không tìm thấy thông tin từ Spotify.")

    return entity


async def _get_oembed_info(sp_type: str, sp_id: str) -> dict:
    """Get basic metadata (title, thumbnail) via Spotify OEmbed API."""
    oembed_url = (
        f"https://open.spotify.com/oembed"
        f"?url=https://open.spotify.com/{sp_type}/{sp_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                oembed_url, headers={"User-Agent": "Mozilla/5.0"}
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return {}


def _parse_track_from_embed(track_data: dict, fallback_thumbnail: str = "") -> dict:
    """Parse a single track entry from embed __NEXT_DATA__ trackList."""
    title = track_data.get("title", "Unknown Track")
    subtitle = track_data.get("subtitle", "")
    # subtitle uses non-breaking spaces as separators between artists
    artist_str = subtitle.replace("\xa0", " ").strip()
    duration_ms = track_data.get("duration", 0)
    duration_s = duration_ms // 1000 if duration_ms > 0 else 0

    # Build cover art URL from track's coverArt if available
    cover_art = track_data.get("coverArt", {})
    sources = cover_art.get("sources", []) if cover_art else []
    thumbnail = sources[0].get("url", "") if sources else fallback_thumbnail

    search_query = (
        f"ytsearch1:{artist_str} - {title} audio" if artist_str else f"ytsearch1:{title} audio"
    )

    return {
        "title": f"{artist_str} - {title}" if artist_str else title,
        "name": title,
        "artist_str": artist_str,
        "thumbnail": thumbnail,
        "search_query": search_query,
        "spotify_url": "",  # embed data uses URIs, not URLs
        "duration": duration_s,
    }


# ── API Token (kept as optional fallback) ────────────────────────────

async def _get_api_token() -> Optional[str]:
    """Get Spotify API token via client credentials (requires env vars)."""
    if not SPOTIPY_CLIENT_ID or not SPOTIPY_CLIENT_SECRET:
        return None
    import base64
    creds = base64.b64encode(
        f"{SPOTIPY_CLIENT_ID}:{SPOTIPY_CLIENT_SECRET}".encode()
    ).decode()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://accounts.spotify.com/api/token",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data="grant_type=client_credentials",
            )
            if resp.status_code == 200:
                return resp.json().get("access_token")
    except Exception:
        pass
    return None


async def _try_api_playlist(sp_id: str, token: str) -> Optional[dict]:
    """Try fetching playlist via official API (needs Premium account)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            headers = {"Authorization": f"Bearer {token}"}
            # Test if API works
            pl_resp = await client.get(
                f"https://api.spotify.com/v1/playlists/{sp_id}?fields=name,images",
                headers=headers,
            )
            if pl_resp.status_code != 200:
                return None  # API blocked (403 = no Premium)

            pl_data = pl_resp.json()
            playlist_name = pl_data.get("name", "Playlist")
            imgs = pl_data.get("images", [])
            playlist_thumbnail = imgs[0].get("url", "") if imgs else ""

            tracks = []
            next_url = (
                f"https://api.spotify.com/v1/playlists/{sp_id}/tracks"
                "?limit=100&fields=next,items(track(name,artists,duration_ms,external_urls,album(images)))"
            )
            while next_url:
                resp = await client.get(next_url, headers=headers)
                if resp.status_code != 200:
                    break
                data = resp.json()
                for item in data.get("items", []):
                    track = item.get("track")
                    if not track or not track.get("name"):
                        continue
                    name = track["name"]
                    artists = [a.get("name", "") for a in track.get("artists", [])]
                    artist_str = ", ".join(a for a in artists if a)
                    images = track.get("album", {}).get("images", [])
                    thumb = images[0].get("url", "") if images else ""
                    tracks.append({
                        "title": f"{artist_str} - {name}",
                        "name": name,
                        "artist_str": artist_str,
                        "thumbnail": thumb,
                        "search_query": f"ytsearch1:{artist_str} - {name} audio",
                        "spotify_url": track.get("external_urls", {}).get("spotify", ""),
                        "duration": track.get("duration_ms", 0) // 1000,
                    })
                next_url = data.get("next")

            if tracks:
                return {
                    "playlist_name": playlist_name,
                    "thumbnail": playlist_thumbnail,
                    "tracks": tracks,
                }
    except Exception as e:
        print(f"[Spotify] API fallback failed: {e}")
    return None


# ── Single Track Info ────────────────────────────────────────────────

async def _scrape_track_meta(track_id: str) -> dict:
    """
    Scrape track metadata from Spotify's public pages — no API key needed.
    Uses oembed for title/thumbnail, OG meta tags for artist info.
    """
    title = "Unknown Track"
    artist = ""
    thumbnail = ""

    headers_mobile = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
    }

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        # 1. oembed — always public, gives title + thumbnail
        try:
            oe = await client.get(
                f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if oe.status_code == 200:
                oe_data = oe.json()
                title = oe_data.get("title", title)
                thumbnail = oe_data.get("thumbnail_url", thumbnail)
        except Exception:
            pass

        # 2. Fetch page OG meta for artist (og:description = "Song · Artist · Year")
        try:
            page = await client.get(
                f"https://open.spotify.com/track/{track_id}",
                headers=headers_mobile,
            )
            if page.status_code == 200:
                html = page.text
                # Try og:description first
                desc_m = re.search(
                    r'property="og:description"\s+content="([^"]+)"', html
                ) or re.search(
                    r'content="([^"]+)"\s+property="og:description"', html
                )
                if desc_m:
                    parts = [p.strip() for p in desc_m.group(1).split("·")]
                    if len(parts) >= 2:
                        artist = parts[1]

                # Fallback: og:title sometimes has "Track - Artist"
                if not artist:
                    title_m = re.search(
                        r'property="og:title"\s+content="([^"]+)"', html
                    )
                    if title_m:
                        og_title = title_m.group(1)
                        if " - " in og_title:
                            parts = og_title.split(" - ", 1)
                            title = parts[0].strip()
                            artist = parts[1].strip()
        except Exception:
            pass

    search_query = (
        f"ytsearch1:{artist} - {title} audio" if artist else f"ytsearch1:{title} audio"
    )
    return {
        "name": title,
        "artist_str": artist,
        "thumbnail": thumbnail,
        "search_query": search_query,
        "duration": 0,
    }


async def get_track_info_async(url: str) -> dict:
    """
    Get metadata for a single Spotify track.
    Uses embed scraping first, then API if available, then OG meta scraping.
    """
    _, track_id = _extract_spotify_type_and_id(url)

    # Try embed scraping first (most reliable)
    try:
        entity = await _scrape_embed_data("track", track_id)
        track_list = entity.get("trackList", [])
        if track_list:
            parsed = _parse_track_from_embed(track_list[0])
            return parsed
    except Exception as e:
        print(f"[Spotify] Embed scraping failed for track: {e}")

    # Fallback: API if credentials available
    token = await _get_api_token()
    if token:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://api.spotify.com/v1/tracks/{track_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    name = data.get("name", "")
                    artists = [a.get("name", "") for a in data.get("artists", [])]
                    artist_str = ", ".join(a for a in artists if a)
                    images = data.get("album", {}).get("images", [])
                    thumbnail = images[0].get("url", "") if images else ""
                    duration = data.get("duration_ms", 0) // 1000
                    return {
                        "name": name,
                        "artist_str": artist_str,
                        "thumbnail": thumbnail,
                        "search_query": f"ytsearch1:{artist_str} - {name} audio",
                        "duration": duration,
                    }
        except Exception:
            pass

    # Final fallback: scrape public Spotify pages
    return await _scrape_track_meta(track_id)


def get_track_info(url: str) -> str:
    """Synchronous wrapper — returns yt-dlp search query string."""
    info = asyncio.run(get_track_info_async(url))
    return info["search_query"]


# ── Playlist Tracks ──────────────────────────────────────────────────

async def get_playlist_tracks_async(playlist_url: str) -> dict:
    """
    Fetch all tracks from a Spotify playlist.
    Returns {playlist_name, thumbnail, tracks: [...]}.

    Strategy:
      1. Scrape embed page (__NEXT_DATA__) — works without API key or Premium
      2. Fallback to official API if embed fails and credentials are available
    """
    _, sp_id = _extract_spotify_type_and_id(playlist_url)

    # ── Strategy 1: Embed scraping (primary) ─────────────────────
    try:
        entity = await _scrape_embed_data("playlist", sp_id)
        playlist_name = entity.get("name") or entity.get("title") or "Playlist"

        # Cover art
        cover_art = entity.get("coverArt", {})
        sources = cover_art.get("sources", []) if cover_art else []
        playlist_thumbnail = sources[0].get("url", "") if sources else ""

        # If no cover from entity, try OEmbed
        if not playlist_thumbnail:
            oembed = await _get_oembed_info("playlist", sp_id)
            playlist_thumbnail = oembed.get("thumbnail_url", "")

        # Parse tracks
        track_list = entity.get("trackList", [])
        tracks = []
        for t in track_list:
            parsed = _parse_track_from_embed(t, fallback_thumbnail=playlist_thumbnail)
            tracks.append(parsed)

        if tracks:
            print(f"[Spotify] Embed scraping: found {len(tracks)} tracks in playlist '{playlist_name}'")
            return {
                "playlist_name": playlist_name,
                "thumbnail": playlist_thumbnail,
                "tracks": tracks,
            }
    except Exception as e:
        print(f"[Spotify] Embed scraping failed for playlist: {e}")

    # ── Strategy 2: Official API fallback ────────────────────────
    token = await _get_api_token()
    if token:
        result = await _try_api_playlist(sp_id, token)
        if result:
            return result

    raise ValueError(
        "Không thể tải danh sách nhạc từ Spotify. "
        "Vui lòng kiểm tra lại link hoặc thử lại sau."
    )


# ── Album Tracks ─────────────────────────────────────────────────────

async def get_album_tracks_async(album_url: str) -> dict:
    """
    Fetch all tracks from a Spotify album.
    Returns {album_name, artist, thumbnail, tracks: [...]}.

    Strategy:
      1. Scrape embed page (__NEXT_DATA__) — works without API key or Premium
      2. Fallback to official API if embed fails and credentials are available
    """
    _, sp_id = _extract_spotify_type_and_id(album_url)

    # ── Strategy 1: Embed scraping (primary) ─────────────────────
    try:
        entity = await _scrape_embed_data("album", sp_id)
        album_name = entity.get("name") or entity.get("title") or "Album"
        album_artist = entity.get("subtitle", "").replace("\xa0", " ").strip()

        # Cover art
        cover_art = entity.get("coverArt", {})
        sources = cover_art.get("sources", []) if cover_art else []
        album_thumbnail = sources[0].get("url", "") if sources else ""

        # If no cover from entity, try OEmbed
        if not album_thumbnail:
            oembed = await _get_oembed_info("album", sp_id)
            album_thumbnail = oembed.get("thumbnail_url", "")

        # Parse tracks
        track_list = entity.get("trackList", [])
        tracks = []
        for t in track_list:
            parsed = _parse_track_from_embed(t, fallback_thumbnail=album_thumbnail)
            tracks.append(parsed)

        if tracks:
            print(f"[Spotify] Embed scraping: found {len(tracks)} tracks in album '{album_name}'")
            return {
                "album_name": album_name,
                "artist": album_artist,
                "thumbnail": album_thumbnail,
                "tracks": tracks,
            }
    except Exception as e:
        print(f"[Spotify] Embed scraping failed for album: {e}")

    # ── Strategy 2: Official API fallback ────────────────────────
    token = await _get_api_token()
    if token:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                headers = {"Authorization": f"Bearer {token}"}

                al_resp = await client.get(
                    f"https://api.spotify.com/v1/albums/{sp_id}",
                    headers=headers,
                )
                if al_resp.status_code != 200:
                    raise ValueError("API blocked")

                al_data = al_resp.json()
                album_name = al_data.get("name", "Album")
                al_artists = [a.get("name", "") for a in al_data.get("artists", [])]
                album_artist = ", ".join(a for a in al_artists if a)
                imgs = al_data.get("images", [])
                album_thumbnail = imgs[0].get("url", "") if imgs else ""

                tracks = []
                next_url = f"https://api.spotify.com/v1/albums/{sp_id}/tracks?limit=50"
                while next_url:
                    resp = await client.get(next_url, headers=headers)
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    for track in data.get("items", []):
                        name = track.get("name", "")
                        if not name:
                            continue
                        artists = [a.get("name", "") for a in track.get("artists", [])]
                        artist_str = ", ".join(a for a in artists if a)
                        tracks.append({
                            "title": f"{artist_str} - {name}",
                            "name": name,
                            "artist_str": artist_str,
                            "thumbnail": album_thumbnail,
                            "search_query": f"ytsearch1:{artist_str} - {name} audio",
                            "spotify_url": track.get("external_urls", {}).get("spotify", ""),
                            "duration": track.get("duration_ms", 0) // 1000,
                        })
                    next_url = data.get("next")

                if tracks:
                    return {
                        "album_name": album_name,
                        "artist": album_artist,
                        "thumbnail": album_thumbnail,
                        "tracks": tracks,
                    }
        except Exception as e:
            print(f"[Spotify] API fallback failed for album: {e}")

    raise ValueError(
        "Không thể tải danh sách nhạc từ album Spotify. "
        "Vui lòng kiểm tra lại link hoặc thử lại sau."
    )


# ══════════════════════════════════════════════════════════════════════
#  Spotify ARTIST support
#  Top tracks + artist info come from the keyless embed page.
#  Albums / singles / full discography require the Web API (client
#  credentials — a free app, no Premium/login). Without a key we degrade
#  to top-tracks-only (partial success) instead of failing.
# ══════════════════════════════════════════════════════════════════════

SPOTIFY_MARKET = os.getenv("SPOTIFY_MARKET", "VN")


def _build_track_item(
    track_id: str, title: str, artists: list, duration_ms: int,
    album_name: str = "", album_id: str = "", cover_image: str = "",
    release_date: str = "", disc_number=None, track_number=None,
    preview_url: str = "",
) -> dict:
    """Normalized track schema + legacy fields the download flow relies on
    (name / artist_str / thumbnail / search_query / duration)."""
    artists = [a for a in (artists or []) if a]
    artist_str = ", ".join(artists)
    search_query = (
        f"ytsearch1:{artist_str} - {title} audio" if artist_str
        else f"ytsearch1:{title} audio"
    )
    external_url = f"https://open.spotify.com/track/{track_id}" if track_id else ""
    return {
        # ── full schema (new) ──
        "spotify_track_id": track_id,
        "title": title,
        "artists": artists,
        "duration_ms": duration_ms or 0,
        "album_name": album_name,
        "album_id": album_id,
        "cover_image": cover_image,
        "release_date": release_date,
        "disc_number": disc_number,
        "track_number": track_number,
        "preview_url": preview_url or "",
        "external_url": external_url,
        # ── legacy fields (consumed by the existing download flow) ──
        "name": title,
        "artist_str": artist_str,
        "thumbnail": cover_image,
        "search_query": search_query,
        "spotify_url": external_url,
        "duration": (duration_ms // 1000) if duration_ms else 0,
    }


def _norm_track_from_embed(t: dict, fallback_thumbnail: str = "") -> dict:
    """Embed trackList item → normalized track item."""
    title = t.get("title", "Unknown Track")
    artist_str = t.get("subtitle", "").replace("\xa0", " ").strip()
    uri = t.get("uri", "") or ""
    track_id = uri.split(":")[-1] if uri.startswith("spotify:track:") else ""
    cover = ""
    ca = t.get("coverArt", {}) or {}
    srcs = ca.get("sources", []) if ca else []
    if srcs:
        cover = srcs[0].get("url", "")
    artists = [a.strip() for a in artist_str.split(",") if a.strip()]
    preview = (t.get("audioPreview", {}) or {}).get("url", "")
    return _build_track_item(
        track_id=track_id, title=title, artists=artists or ([artist_str] if artist_str else []),
        duration_ms=t.get("duration", 0) or 0, cover_image=cover or fallback_thumbnail,
        preview_url=preview,
    )


def _norm_album_item(al: dict) -> dict:
    """Spotify API album object → normalized album item."""
    imgs = al.get("images", []) or []
    aid = al.get("id", "")
    return {
        "album_id": aid,
        "title": al.get("name", ""),
        "cover_image": imgs[0].get("url", "") if imgs else "",
        "release_date": al.get("release_date", ""),
        "total_tracks": al.get("total_tracks", 0),
        "album_type": al.get("album_type", ""),  # album | single | compilation
        "external_url": (
            al.get("external_urls", {}).get("spotify", "")
            or (f"https://open.spotify.com/album/{aid}" if aid else "")
        ),
    }


def _track_item_from_api(tr: dict, album_ctx: Optional[dict] = None) -> dict:
    """Spotify API track object → normalized track item. album_ctx supplies
    album metadata when the endpoint omits it (e.g. /albums/{id}/tracks)."""
    album = tr.get("album") or album_ctx or {}
    imgs = album.get("images", []) or []
    artists = [a.get("name", "") for a in tr.get("artists", [])]
    return _build_track_item(
        track_id=tr.get("id", ""), title=tr.get("name", ""), artists=artists,
        duration_ms=tr.get("duration_ms", 0) or 0,
        album_name=album.get("name", ""), album_id=album.get("id", ""),
        cover_image=imgs[0].get("url", "") if imgs else "",
        release_date=album.get("release_date", ""),
        disc_number=tr.get("disc_number"), track_number=tr.get("track_number"),
        preview_url=tr.get("preview_url") or "",
    )


def _dedupe_tracks(tracks: list) -> list:
    """Drop duplicate tracks across albums/singles/compilations. Collapses
    album-vs-single-vs-remaster versions of the same song via a normalized
    (title + primary artist + 5s duration bucket) key; falls back to the
    spotify_track_id when the name is empty."""
    seen, out = set(), []
    for t in tracks:
        primary = (t.get("artists") or [t.get("artist_str", "")] or [""])[0]
        norm = re.sub(r"[^a-z0-9]+", " ", f"{t.get('name','')} {primary}".lower()).strip()
        if norm:
            key = (norm, (t.get("duration_ms", 0) or 0) // 5000)
        else:
            key = ("id", t.get("spotify_track_id") or id(t))
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


async def _api_json(
    client: httpx.AsyncClient, url: str, token: str, *, retries: int = 3,
) -> Optional[dict]:
    """GET a Spotify Web API endpoint with bounded exponential backoff.

    Retries only transient failures (429 + 5xx + network errors); 4xx other than
    429 (e.g. 401/403/404) fail fast — retrying won't help. Returns None after
    exhausting retries so the caller degrades to PARTIAL success instead of
    hard-failing the whole artist (P1: partial > hard fail)."""
    endpoint = url.split("/v1/")[-1][:50]
    last = ""
    for attempt in range(retries):
        try:
            r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                # Honour Retry-After on 429; otherwise exponential backoff, capped.
                try:
                    wait = float(r.headers.get("Retry-After", 2 ** attempt))
                except (TypeError, ValueError):
                    wait = float(2 ** attempt)
                last = f"HTTP {r.status_code}"
                if attempt < retries - 1:
                    await asyncio.sleep(min(wait, 8))
                    continue
            else:
                print(f"[Spotify API] {endpoint} -> HTTP {r.status_code} (no-retry)")
                return None
        except Exception as e:
            last = str(e)[:80]
            if attempt < retries - 1:
                await asyncio.sleep(min(2 ** attempt, 8))
    print(f"[Spotify API] giving up after {retries}x: {endpoint} ({last})")
    return None


async def _artist_top_tracks_via_api(client, sp_id: str, token: str) -> list:
    d = await _api_json(
        client,
        f"https://api.spotify.com/v1/artists/{sp_id}/top-tracks?market={SPOTIFY_MARKET}",
        token,
    )
    return [_track_item_from_api(tr) for tr in (d or {}).get("tracks", []) if tr.get("name")]


async def _artist_albums_raw_via_api(client, sp_id: str, token: str) -> list:
    albums, next_url = [], (
        f"https://api.spotify.com/v1/artists/{sp_id}/albums"
        f"?include_groups=album,single,compilation&market={SPOTIFY_MARKET}&limit=50"
    )
    while next_url:
        d = await _api_json(client, next_url, token)
        if not d:
            break
        albums += d.get("items", [])
        next_url = d.get("next")
    return albums


def _resolve_artist_id(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    if s.startswith("http") or s.startswith("spotify:"):
        return parse_spotify_url(s)["spotify_id"]
    return s


async def extract_spotify_artist_async(url_or_id: str) -> dict:
    """
    Build a normalized Spotify artist overview.

    Keyless (embed): artist name, image, top tracks.
    With API key   : followers, genres, richer top tracks, albums, singles.

    Raises ValueError('spotify_artist_not_found') / ('spotify_artist_no_tracks_found').
    """
    sp_id = _resolve_artist_id(url_or_id)

    # P5a metrics + P4a cache: count every request, serve a 30-min cached
    # overview when available (saves a Spotify embed scrape + API round-trips).
    _ops.artist_metric("requests")
    _cached = _ops.get_cached_artist(sp_id)
    if _cached:
        _ops.artist_seen(_cached.get("artist", {}).get("name", ""))
        return {**_cached, "cached": True}

    artist = {
        "id": sp_id, "name": "", "image": "",
        "followers": None, "genres": [], "popularity": None,
        "external_url": f"https://open.spotify.com/artist/{sp_id}",
    }
    top_tracks: list = []
    albums: list = []
    singles: list = []

    # ── Stage A: embed (keyless) ─────────────────────────────────
    try:
        entity = await _scrape_embed_data("artist", sp_id)
        artist["name"] = entity.get("name") or entity.get("title") or ""
        vimg = (entity.get("visualIdentity", {}) or {}).get("image", []) or []
        if vimg:
            artist["image"] = vimg[0].get("url", "")
        for t in entity.get("trackList", []):
            top_tracks.append(_norm_track_from_embed(t, fallback_thumbnail=artist["image"]))
        print(f"[Spotify Artist] embed: '{artist['name']}' + {len(top_tracks)} top tracks")
    except Exception as e:
        print(f"[Spotify Artist] embed failed: {e}")

    # ── Stage B: API enrichment (albums / singles / followers / genres) ──
    token = await _get_api_token()
    if token:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                a = await _api_json(client, f"https://api.spotify.com/v1/artists/{sp_id}", token)
                if a:
                    artist["name"] = a.get("name") or artist["name"]
                    imgs = a.get("images", []) or []
                    if imgs:
                        artist["image"] = imgs[0].get("url", "") or artist["image"]
                    artist["followers"] = (a.get("followers", {}) or {}).get("total")
                    artist["genres"] = a.get("genres", []) or []
                    artist["popularity"] = a.get("popularity")
                    ext = (a.get("external_urls", {}) or {}).get("spotify")
                    if ext:
                        artist["external_url"] = ext
                api_top = await _artist_top_tracks_via_api(client, sp_id, token)
                if api_top:
                    top_tracks = api_top  # richer (has album/track ids)
                seen_album = set()
                for al in await _artist_albums_raw_via_api(client, sp_id, token):
                    aid = al.get("id")
                    if not aid or aid in seen_album:
                        continue
                    seen_album.add(aid)
                    item = _norm_album_item(al)
                    (singles if item["album_type"] == "single" else albums).append(item)
                print(f"[Spotify Artist] API: {len(albums)} albums, {len(singles)} singles")
        except Exception as e:
            print(f"[Spotify Artist] API enrich failed: {e}")

    if not artist["name"]:
        raise ValueError("spotify_artist_not_found")
    if not top_tracks and not albums and not singles:
        raise ValueError("spotify_artist_no_tracks_found")

    has_api = bool(token)
    est = (
        sum((a.get("total_tracks") or 0) for a in albums)
        + sum((s.get("total_tracks") or 0) for s in singles)
    ) or len(top_tracks)
    result = {
        "platform": "spotify",
        "source_type": "artist",
        "recognized": True,
        "phase": "metadata_ready",
        "artist": artist,
        "summary": {
            "top_tracks_count": len(top_tracks),
            "albums_count": len(albums),
            "singles_count": len(singles),
            "deduped_track_count_estimate": est,
        },
        "top_tracks": top_tracks,
        "albums": albums,
        "singles": singles,
        "actions": {
            "can_download_top_tracks": len(top_tracks) > 0,
            "can_download_all_albums": has_api and len(albums) > 0,
            "can_download_all_singles": has_api and len(singles) > 0,
            "can_download_all_tracks": has_api and (len(albums) > 0 or len(singles) > 0),
        },
        # True when we only have top tracks (no albums/singles) — either no API
        # key, or the Spotify API rejected it (e.g. Development-mode apps now
        # require the owner to have Premium → 403). Frontend shows a notice.
        "partial": (len(albums) == 0 and len(singles) == 0),
    }
    # P4a: cache the assembled overview (30 min) + P5a leaderboard.
    _ops.cache_artist(sp_id, result)
    _ops.artist_seen(artist.get("name", ""))
    return result


def _finalize_collect(mode: str, raw_tracks: list, extra_summary: dict | None = None) -> dict:
    """Dedupe a gathered track list and build the Phase-4 download summary.
    Raises spotify_artist_dedupe_empty / spotify_artist_no_downloadable_tracks."""
    requested = len(raw_tracks)
    deduped = _dedupe_tracks(raw_tracks)
    if requested > 0 and not deduped:
        raise ValueError("spotify_artist_dedupe_empty")
    if not deduped:
        raise ValueError("spotify_artist_no_downloadable_tracks")
    summary = {
        "requested_count": requested,
        "expanded_count": requested,
        "deduped_count": len(deduped),
    }
    if extra_summary:
        summary.update(extra_summary)
    return {"mode": mode, "tracks": deduped, "count": len(deduped), "summary": summary}


async def collect_artist_tracks_async(
    url_or_id: str, mode: str = "all_tracks", *, confirmed: bool = False,
) -> dict:
    """
    Resolve an artist into a concrete, deduped track list for downloading.
    mode: 'top_tracks' | 'albums' | 'singles' | 'all_tracks'.
    Albums/singles/all_tracks require the API; top_tracks works keyless.

    Catalog guard (P3): when the requested set exceeds the default cap and the
    caller did not pass confirmed=True, returns a *non-download* payload
    {needs_confirmation: True, real_count, cap, message} instead of expanding —
    so a huge artist can't silently queue thousands of tracks / burn ScraperAPI.
    Pass confirmed=True (after the UI asks) to expand up to the hard cap.

    Returns {mode, tracks, count, summary:{requested_count, expanded_count,
    deduped_count, [albums_requested, albums_expanded, capped]}}.
    """
    sp_id = _resolve_artist_id(url_or_id)
    token = await _get_api_token()

    if mode == "top_tracks":
        if token:
            async with httpx.AsyncClient(timeout=15) as client:
                tracks = await _artist_top_tracks_via_api(client, sp_id, token)
            if tracks:
                tracks = tracks[:_ops.ARTIST_CAP_TOP_TRACKS]
                _ops.artist_metric("expand_ok")
                return _finalize_collect(mode, tracks)
        art = await extract_spotify_artist_async(sp_id)  # keyless fallback (embed)
        _ops.artist_metric("expand_ok")
        return _finalize_collect(mode, art.get("top_tracks", [])[:_ops.ARTIST_CAP_TOP_TRACKS])

    if not token:
        raise ValueError("spotify_artist_expand_failed")

    def _want(al: dict) -> bool:
        at = al.get("album_type")
        if mode == "albums":
            return at in ("album", "compilation")
        if mode == "singles":
            return at == "single"
        return True  # all_tracks

    try:
        # ── Gather the candidate album set first so we can size-check BEFORE
        #    spending API calls expanding every tracklist. ──────────────────
        async with httpx.AsyncClient(timeout=25) as client:
            all_albums = await _artist_albums_raw_via_api(client, sp_id, token)
            wanted = [al for al in all_albums if _want(al) and al.get("id")]

            # Catalog cap → ask for confirmation instead of runaway expansion.
            capped = False
            if mode in ("albums", "singles"):
                cap = _ops.ARTIST_CAP_ALBUMS if mode == "albums" else _ops.ARTIST_CAP_SINGLES
                if len(wanted) > cap and not confirmed:
                    _ops.artist_metric("needs_confirm")
                    return {
                        "needs_confirmation": True, "mode": mode,
                        "real_count": len(wanted), "cap": cap,
                        "message": (f"Nghệ sĩ có {len(wanted)} {mode}. Mặc định tải {cap} "
                                    f"mới nhất. Xác nhận để tải toàn bộ."),
                    }
                if len(wanted) > cap:
                    wanted = wanted[:cap]; capped = True
            elif mode == "all_tracks":
                est = sum((al.get("total_tracks") or 0) for al in wanted)
                if est > _ops.ARTIST_CAP_ALL_TRACKS and not confirmed:
                    _ops.artist_metric("needs_confirm")
                    return {
                        "needs_confirmation": True, "mode": "all_tracks",
                        "real_count": est, "cap": _ops.ARTIST_CAP_ALL_TRACKS,
                        "message": (f"Nghệ sĩ có ~{est} bài. Mỗi lần tải tối đa "
                                    f"{_ops.ARTIST_CAP_ALL_TRACKS}. Xác nhận để tiếp tục."),
                    }

            raw: list = []
            albums_requested = 0
            albums_expanded = 0  # albums that yielded ≥1 track (partial-expansion tracking)
            for al in wanted:
                # Hard stop once we hit the all_tracks ceiling (defensive: an
                # under-counted total_tracks can't blow past the cap).
                if mode == "all_tracks" and len(raw) >= _ops.ARTIST_CAP_ALL_TRACKS:
                    capped = True
                    break
                aid = al.get("id")
                albums_requested += 1
                album_ctx = {
                    "name": al.get("name"), "id": aid,
                    "images": al.get("images", []), "release_date": al.get("release_date"),
                }
                before = len(raw)
                next_url = f"https://api.spotify.com/v1/albums/{aid}/tracks?market={SPOTIFY_MARKET}&limit=50"
                while next_url:
                    d = await _api_json(client, next_url, token)
                    if not d:
                        break
                    for tr in d.get("items", []):
                        if tr.get("name"):
                            raw.append(_track_item_from_api(tr, album_ctx=album_ctx))
                    next_url = d.get("next")
                if len(raw) > before:
                    albums_expanded += 1
    except Exception:
        _ops.artist_metric("expand_fail")
        raise

    # Final ceiling after dedupe is applied in _finalize_collect; clamp the raw
    # list first so the dedupe key set stays bounded too.
    if mode == "all_tracks" and len(raw) > _ops.ARTIST_CAP_ALL_TRACKS:
        raw = raw[:_ops.ARTIST_CAP_ALL_TRACKS]; capped = True

    _ops.artist_metric("expand_ok")
    if capped:
        _ops.artist_metric("capped")
    result = _finalize_collect(mode, raw, {
        "albums_requested": albums_requested,
        "albums_expanded": albums_expanded,
        "capped": capped,
    })
    _ops.artist_metric("deduped", max(0, result["summary"]["requested_count"] - result["count"]))
    return result


# Legacy sync wrappers for backward compatibility
def get_playlist_tracks(playlist_url: str) -> list[dict]:
    result = asyncio.run(get_playlist_tracks_async(playlist_url))
    return result["tracks"]


def get_album_tracks(album_url: str) -> list[dict]:
    result = asyncio.run(get_album_tracks_async(album_url))
    return result["tracks"]
