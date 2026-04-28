import os
import re
import json
import httpx
import asyncio
from typing import Optional

SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")

# ── User-Agent for scraping ──────────────────────────────────────────
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def is_spotify_url(url: str) -> bool:
    return "open.spotify.com" in url


def _extract_spotify_type_and_id(url: str) -> tuple[str, str]:
    """Returns (type, id) where type is 'track', 'playlist', or 'album'."""
    for sp_type in ("track", "playlist", "album"):
        m = re.search(rf"open\.spotify\.com/{sp_type}/([A-Za-z0-9]+)", url)
        if m:
            return sp_type, m.group(1)
    raise ValueError(f"Không thể nhận diện URL Spotify: {url}")


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
        f"ytsearch1:{artist_str} - {title}" if artist_str else f"ytsearch1:{title}"
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
                        "search_query": f"ytsearch1:{artist_str} - {name}",
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
        f"ytsearch1:{artist} - {title}" if artist else f"ytsearch1:{title}"
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
                        "search_query": f"ytsearch1:{artist_str} - {name}",
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
                            "search_query": f"ytsearch1:{artist_str} - {name}",
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


# Legacy sync wrappers for backward compatibility
def get_playlist_tracks(playlist_url: str) -> list[dict]:
    result = asyncio.run(get_playlist_tracks_async(playlist_url))
    return result["tracks"]


def get_album_tracks(album_url: str) -> list[dict]:
    result = asyncio.run(get_album_tracks_async(album_url))
    return result["tracks"]
