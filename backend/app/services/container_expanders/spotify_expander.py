"""
Spotify Container Expander — Phase 25
=======================================
Discovers Spotify artist / playlist / album as ContainerMeta with sections.

Reuses the production spotify_service.py functions; no new HTTP libs needed.
Async functions are called via asyncio.run() (Celery task context = sync thread).
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from app.services.container_discovery import (
    ContainerMeta, ContainerSection, ContainerItem, ContainerChild,
    make_container_id,
)
from app.services.container_expanders import BaseExpander


def _track_to_item(t: dict) -> ContainerItem:
    """Convert spotify_service track dict → ContainerItem."""
    track_id = t.get("spotify_track_id") or t.get("id", "")
    sp_id = f"sp:{track_id}" if track_id else ""
    # canonical URL for queueing: use the search_query wrapped as a virtual URL
    # or the actual Spotify URL if available
    sp_url = t.get("external_url") or t.get("spotify_url", "")
    if not sp_url and track_id:
        sp_url = f"https://open.spotify.com/track/{track_id}"
    # For queueing, we pass the Spotify URL; process_video_task resolves via ytsearch
    return ContainerItem(
        id=sp_id,
        url=sp_url or t.get("search_query", ""),
        title=t.get("name") or t.get("title", "Unknown"),
        author=_artists_str(t),
        thumbnail=t.get("cover_image") or t.get("thumbnail", ""),
        duration_ms=t.get("duration_ms") or (t.get("duration", 0) * 1000),
        media_type="audio",
        views=0,
        published_at=t.get("release_date", ""),
        extras={
            "album": t.get("album_name", ""),
            "album_type": t.get("album_type", ""),
            "search_query": t.get("search_query", ""),
        },
    )


def _artists_str(t: dict) -> str:
    artists = t.get("artists")
    if artists and isinstance(artists, list):
        return ", ".join(str(a) for a in artists[:3] if a)
    return t.get("artist_str", "")


def _album_to_child(al: dict) -> ContainerChild:
    aid = al.get("album_id") or al.get("id", "")
    return ContainerChild(
        id=f"sp_album:{aid}",
        label=al.get("title") or al.get("name", "Unknown Album"),
        item_count=al.get("total_tracks", 0),
        thumbnail=al.get("cover_image", ""),
        extras={
            "album_type": al.get("album_type", ""),
            "release_date": al.get("release_date", ""),
            "spotify_url": f"https://open.spotify.com/album/{aid}" if aid else "",
        },
    )


class SpotifyExpander(BaseExpander):
    platform = "spotify"
    supported_types = ["artist", "playlist", "album"]

    def discover(
        self,
        url: str,
        max_items: int = 200,
        include_sections: Optional[list[str]] = None,
    ) -> ContainerMeta:
        from app.services.spotify_service import parse_spotify_url
        try:
            parsed = parse_spotify_url(url)
        except ValueError:
            return ContainerMeta(
                container_id=make_container_id(),
                platform="spotify", container_type="unknown", url=url,
                title="", status="failed",
                error_code="C001", error_message="Link Spotify không hợp lệ.",
            )

        sp_type = parsed["source_type"]
        sp_id = parsed["spotify_id"]

        if sp_type == "artist":
            return self._discover_artist(url, sp_id, max_items, include_sections)
        if sp_type in ("playlist", "album"):
            return self._discover_playlist_or_album(url, sp_type, sp_id, max_items)

        return ContainerMeta(
            container_id=make_container_id(),
            platform="spotify", container_type=sp_type, url=url,
            title="", status="failed",
            error_code="C030", error_message=f"Container type '{sp_type}' không hỗ trợ trong Phase 25.",
        )

    # ── Artist ──────────────────────────────────────────────────────────────

    def _discover_artist(
        self,
        url: str,
        sp_id: str,
        max_items: int,
        include_sections: Optional[list[str]],
    ) -> ContainerMeta:
        from app.services.spotify_service import extract_spotify_artist_async

        try:
            data = asyncio.run(extract_spotify_artist_async(url))
        except ValueError as e:
            code = str(e)
            return ContainerMeta(
                container_id=make_container_id(),
                platform="spotify", container_type="artist", url=url,
                title="", status="failed",
                error_code="C001", error_message=f"Không tìm thấy nghệ sĩ: {code}",
            )
        except Exception as e:
            return ContainerMeta(
                container_id=make_container_id(),
                platform="spotify", container_type="artist", url=url,
                title="", status="failed",
                error_code="C001", error_message=str(e)[:200],
            )

        artist = data.get("artist", {})
        top_tracks: list = data.get("top_tracks", [])
        albums_raw: list = data.get("albums", [])
        singles_raw: list = data.get("singles", [])

        meta = ContainerMeta(
            container_id=make_container_id(),
            platform="spotify",
            container_type="artist",
            url=url,
            title=artist.get("name", "Unknown Artist"),
            description=f"{artist.get('followers', '')} followers" if artist.get("followers") else "",
            avatar=artist.get("image", ""),
            item_count=len(top_tracks) + sum(al.get("total_tracks", 0) for al in albums_raw + singles_raw),
            status="ready",
            discovered_at=time.time(),
        )

        # Section: top tracks (always loaded eagerly — small count)
        if top_tracks and (include_sections is None or "top_tracks" in include_sections):
            items = [_track_to_item(t) for t in top_tracks[:10]]
            meta.sections.append(ContainerSection(
                key="top_tracks",
                label=f"Top Tracks ({len(items)})",
                item_count=len(items),
                items_loaded=True,
                items=items,
            ))

        # Section: albums (lazy — user must expand individual album)
        if albums_raw and (include_sections is None or "albums" in include_sections):
            children = [_album_to_child(al) for al in albums_raw]
            meta.sections.append(ContainerSection(
                key="albums",
                label=f"Albums ({len(children)})",
                item_count=len(children),
                items_loaded=False,
                children=children,
            ))

        # Section: singles & EPs (lazy children list)
        if singles_raw and (include_sections is None or "singles" in include_sections):
            children = [_album_to_child(s) for s in singles_raw]
            meta.sections.append(ContainerSection(
                key="singles",
                label=f"Singles & EPs ({len(children)})",
                item_count=len(children),
                items_loaded=False,
                children=children,
            ))

        if not meta.sections:
            meta.status = "failed"
            meta.error_code = "C002"
            meta.error_message = "Không tìm thấy nội dung từ nghệ sĩ này."

        return meta

    # ── Playlist / Album (flat, fully loaded) ──────────────────────────────

    def _discover_playlist_or_album(
        self,
        url: str,
        sp_type: str,
        sp_id: str,
        max_items: int,
    ) -> ContainerMeta:
        from app.services.spotify_service import (
            get_playlist_tracks_async,
            get_album_tracks_async,
        )

        try:
            if sp_type == "playlist":
                data = asyncio.run(get_playlist_tracks_async(url))
            else:
                data = asyncio.run(get_album_tracks_async(url))
        except Exception as e:
            return ContainerMeta(
                container_id=make_container_id(),
                platform="spotify", container_type=sp_type, url=url,
                title="", status="failed",
                error_code="C001", error_message=str(e)[:200],
            )

        tracks = data.get("tracks", [])[:max_items]
        playlist_name = data.get("playlist_name") or data.get("album_name", "Spotify")
        thumbnail = data.get("thumbnail", "")

        items = [_track_to_item(t) for t in tracks]
        section = ContainerSection(
            key="tracks",
            label=f"Tracks ({len(items)})",
            item_count=len(items),
            items_loaded=True,
            items=items,
        )

        return ContainerMeta(
            container_id=make_container_id(),
            platform="spotify",
            container_type=sp_type,
            url=url,
            title=playlist_name,
            avatar=thumbnail,
            item_count=len(items),
            sections=[section],
            status="ready",
            discovered_at=time.time(),
        )

    def expand_section(self, url: str, section_key: str, child_id: str = "") -> list[ContainerItem]:
        """Expand an album child into its tracks. Called by the /expand endpoint."""
        if section_key in ("albums", "singles") and child_id.startswith("sp_album:"):
            album_id = child_id.replace("sp_album:", "")
            album_url = f"https://open.spotify.com/album/{album_id}"
            data = asyncio.run(self._get_album_tracks(album_url))
            return [_track_to_item(t) for t in data]
        return []

    async def _get_album_tracks(self, album_url: str) -> list:
        from app.services.spotify_service import get_album_tracks_async
        data = await get_album_tracks_async(album_url)
        return data.get("tracks", [])
