import re
import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials
# [CRITICAL IMPORT] This tool is required to move the Client Cache
from spotipy.cache_handler import CacheFileHandler 
from typing import List, Dict
from config.config import (
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI, 
    SPOTIFY_CACHE_PATH, get_logger
)
from models import Track

logger = get_logger("SpotifyService")

class SpotifyProvider:
    def __init__(self, use_user_auth=True):
        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            raise ValueError("⚠️ Spotify Credentials missing in .env")
        
        # [FIX] Create a Shared Handler pointing to 'music1/config/.cache'
        # We use this object to force BOTH managers to write to the correct place.
        handler = CacheFileHandler(cache_path=str(SPOTIFY_CACHE_PATH))

        # 1. SETUP CLIENT AUTH (The Culprit)
        # We MUST pass 'cache_handler=handler' here. 
        # Without this, it defaults to creating '.cache' in the root.
        self.client_auth_manager = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            cache_handler=handler  # <--- THIS STOPS THE ROOT FILE
        )
        
        # 2. SETUP USER AUTH (Lazy Loaded)
        if use_user_auth:
            logger.info("🔐 Initializing User Auth (Private Data)")
            self.user_auth_manager = SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope="playlist-read-private playlist-read-collaborative",
                open_browser=False,
                cache_handler=handler  # <--- Use the same handler here
            )
            
            self.sp = spotipy.Spotify(auth_manager=self.user_auth_manager)
            try:
                self.user_id = self.sp.current_user()["id"]
            except Exception:
                logger.warning("⚠️ User Auth failed, falling back to Client Auth")
                self.sp = spotipy.Spotify(auth_manager=self.client_auth_manager)
        
        else:
            logger.info("🌍 Initializing Client Auth (Public Data only)")
            self.sp = spotipy.Spotify(auth_manager=self.client_auth_manager)

    def _get_tracks_from_id(self, playlist_id: str) -> List[Track]:
        """ Helper for Playlists """
        tracks = []
        offset = 0
        while True:
            res = self.sp.playlist_items(
                playlist_id, offset=offset, limit=100,
                fields="items(track(name,artists(name),album(name))),next"
            )
            if not res.get("items"): break
            
            for item in res["items"]:
                t = item.get("track")
                if t:
                    artist = t["artists"][0]["name"] if t["artists"] else "Unknown"
                    tracks.append(Track(title=t["name"], artist=artist, album=t["album"]["name"]))
            
            if not res.get("next"): break
            offset += len(res["items"])
        return tracks

    def _get_tracks_from_album(self, album_id: str, album_name: str) -> List[Track]:
        """ Helper for Albums """
        tracks = []
        offset = 0
        while True:
            res = self.sp.album_tracks(album_id, limit=50, offset=offset)
            if not res.get("items"): break
            
            for t in res["items"]:
                if t:
                    artist = t["artists"][0]["name"] if t["artists"] else "Unknown"
                    tracks.append(Track(title=t["name"], artist=artist, album=album_name))
            
            if not res.get("next"): break
            offset += len(res["items"])
        return tracks

    def fetch_user_library(self) -> Dict[str, List[Track]]:
        if not hasattr(self, 'user_auth_manager'):
            raise PermissionError("❌ Cannot fetch User Library in Public Mode.")
        logger.info(f"🔄 Fetching library for user: {self.user_id}")
        playlists = {}
        offset = 0
        while True:
            res = self.sp.current_user_playlists(limit=50, offset=offset)
            if not res["items"]: break
            for p in res["items"]:
                if p["owner"]["id"] == self.user_id:
                    playlists[p["name"]] = self._get_tracks_from_id(p["id"])
            offset += len(res["items"])
        return playlists

    def fetch_by_url(self, url: str) -> Dict[str, List[Track]]:
        match_pl = re.search(r"playlist/([a-zA-Z0-9]+)", url)
        if match_pl:
            pid = match_pl.group(1)
            try:
                name = self.sp.playlist(pid, fields="name")["name"]
            except spotipy.exceptions.SpotifyException:
                 raise PermissionError("❌ Playlist not found.")
            logger.info(f"🔗 Detected Playlist: {name}")
            return {name: self._get_tracks_from_id(pid)}
            
        match_al = re.search(r"album/([a-zA-Z0-9]+)", url)
        if match_al:
            aid = match_al.group(1)
            try:
                album_data = self.sp.album(aid)
                name = album_data["name"]
            except spotipy.exceptions.SpotifyException:
                 raise ValueError("❌ Album not found.")
            logger.info(f"💿 Detected Album: {name}")
            return {name: self._get_tracks_from_album(aid, name)}
        raise ValueError("Invalid Spotify URL.")