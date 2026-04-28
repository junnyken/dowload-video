import os
import re
import httpx
import asyncio
from typing import Optional

SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")


def is_spotify_url(url: str) -> bool:
    return "open.spotify.com" in url


def _extract_spotify_type_and_id(url: str) -> tuple[str, str]:
    """Returns (type, id) where type is 'track', 'playlist', or 'album'."""
    for sp_type in ("track", "playlist", "album"):
        m = re.search(rf"open\.spotify\.com/{sp_type}/([A-Za-z0-9]+)", url)
        if m:
            return sp_type, m.group(1)
    raise ValueError(f"Không thể nhận diện URL Spotify: {url}")


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
    Uses Spotify Web API if credentials are configured, else scrapes public pages.
    """
    _, track_id = _extract_spotify_type_and_id(url)

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

    # No credentials or API failed → scrape public Spotify pages
    return await _scrape_track_meta(track_id)


def get_track_info(url: str) -> str:
    """Synchronous wrapper — returns yt-dlp search query string."""
    info = asyncio.run(get_track_info_async(url))
    return info["search_query"]


async def get_playlist_tracks_async(playlist_url: str) -> dict:
    """
    Fetch all tracks from a Spotify playlist.
    Returns {playlist_name, thumbnail, tracks: [...]}.
    Requires SPOTIPY_CLIENT_ID + SPOTIPY_CLIENT_SECRET env vars.
    """
    token = await _get_api_token()
    if not token:
        raise ValueError(
            "Tính năng Playlist Spotify cần Spotify API Key. "
            "Vui lòng cài đặt SPOTIPY_CLIENT_ID và SPOTIPY_CLIENT_SECRET trong Coolify."
        )

    _, sp_id = _extract_spotify_type_and_id(playlist_url)
    tracks = []
    playlist_name = "Playlist"
    playlist_thumbnail = ""

    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}

        # Playlist metadata
        pl_resp = await client.get(
            f"https://api.spotify.com/v1/playlists/{sp_id}?fields=name,images",
            headers=headers,
        )
        if pl_resp.status_code == 200:
            pl_data = pl_resp.json()
            playlist_name = pl_data.get("name", playlist_name)
            imgs = pl_data.get("images", [])
            playlist_thumbnail = imgs[0].get("url", "") if imgs else ""

        # Paginate tracks
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

    return {
        "playlist_name": playlist_name,
        "thumbnail": playlist_thumbnail,
        "tracks": tracks,
    }


async def get_album_tracks_async(album_url: str) -> dict:
    """
    Fetch all tracks from a Spotify album.
    Returns {album_name, artist, thumbnail, tracks: [...]}.
    Requires SPOTIPY_CLIENT_ID + SPOTIPY_CLIENT_SECRET env vars.
    """
    token = await _get_api_token()
    if not token:
        raise ValueError(
            "Tính năng Album Spotify cần Spotify API Key. "
            "Vui lòng cài đặt SPOTIPY_CLIENT_ID và SPOTIPY_CLIENT_SECRET trong Coolify."
        )

    _, sp_id = _extract_spotify_type_and_id(album_url)
    tracks = []
    album_name = "Album"
    album_artist = ""
    album_thumbnail = ""

    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}

        # Album metadata
        al_resp = await client.get(
            f"https://api.spotify.com/v1/albums/{sp_id}",
            headers=headers,
        )
        if al_resp.status_code == 200:
            al_data = al_resp.json()
            album_name = al_data.get("name", album_name)
            al_artists = [a.get("name", "") for a in al_data.get("artists", [])]
            album_artist = ", ".join(a for a in al_artists if a)
            imgs = al_data.get("images", [])
            album_thumbnail = imgs[0].get("url", "") if imgs else ""

        # Paginate tracks
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

    return {
        "album_name": album_name,
        "artist": album_artist,
        "thumbnail": album_thumbnail,
        "tracks": tracks,
    }


# Legacy sync wrappers for backward compatibility
def get_playlist_tracks(playlist_url: str) -> list[dict]:
    result = asyncio.run(get_playlist_tracks_async(playlist_url))
    return result["tracks"]


def get_album_tracks(album_url: str) -> list[dict]:
    result = asyncio.run(get_album_tracks_async(album_url))
    return result["tracks"]
