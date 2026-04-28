import os
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from dotenv import load_dotenv

load_dotenv()

# Ensure spotipy credentials are set in environment
CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")

def _get_spotify_client():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("Missing SPOTIPY_CLIENT_ID or SPOTIPY_CLIENT_SECRET in environment variables.")
    # Uses client credentials flow (no user login required)
    auth_manager = SpotifyClientCredentials(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    return spotipy.Spotify(auth_manager=auth_manager)

def is_spotify_url(url: str) -> bool:
    return "open.spotify.com" in url

def get_track_info(track_url: str) -> str:
    """
    Returns a search query string for yt-dlp to find the song on YouTube.
    e.g., 'Artist - Song Name Official Audio'
    """
    sp = _get_spotify_client()
    # Extract track id from url
    track_id = track_url.split('/')[-1].split('?')[0]
    
    track = sp.track(track_id)
    track_name = track['name']
    artist_names = ", ".join([artist['name'] for artist in track['artists']])
    
    # Returning standard yt-dlp search string format
    return f"ytsearch1:{artist_names} - {track_name} Official Audio"

def get_playlist_tracks(playlist_url: str) -> list[dict]:
    """
    Extracts all tracks from a Spotify playlist URL.
    Returns a list of dicts with 'title' and 'query' keys representing each track.
    Automatically handles pagination.
    """
    sp = _get_spotify_client()
    playlist_id = playlist_url.split('/')[-1].split('?')[0]
    
    # Retrieve track results
    results = sp.playlist_tracks(playlist_id)
    tracks = results['items']
    
    # Handle pagination
    while results['next']:
        results = sp.next(results)
        tracks.extend(results['items'])
        
    track_queries = []
    for item in tracks:
        track = item.get('track', {})
        if not track:
            continue
            
        track_name = track.get('name', '')
        if not track_name:
            continue
            
        artists = track.get('artists', [])
        artist_names = ", ".join([artist.get('name', '') for artist in artists])
        
        display_title = f"{artist_names} - {track_name}"
        search_query = f"ytsearch1:{display_title} Official Audio"
        
        track_queries.append({
            "title": display_title,
            "query": search_query,
            "original_url": track.get('external_urls', {}).get('spotify', playlist_url)
        })
        
    return track_queries

def get_album_tracks(album_url: str) -> list[dict]:
    """
    Extracts all tracks from a Spotify album URL.
    """
    sp = _get_spotify_client()
    album_id = album_url.split('/')[-1].split('?')[0]
    
    results = sp.album_tracks(album_id)
    tracks = results['items']
    
    while results['next']:
        results = sp.next(results)
        tracks.extend(results['items'])
        
    track_queries = []
    for track in tracks:
        track_name = track.get('name', '')
        artists = track.get('artists', [])
        artist_names = ", ".join([artist.get('name', '') for artist in artists])
        
        display_title = f"{artist_names} - {track_name}"
        search_query = f"ytsearch1:{display_title} Official Audio"
        
        track_queries.append({
            "title": display_title,
            "query": search_query,
            "original_url": track.get('external_urls', {}).get('spotify', album_url)
        })
        
    return track_queries
