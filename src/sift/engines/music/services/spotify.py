import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import Dict, List

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from sift.engines.music.config import (
    SPOTIFY_FETCH_WORKERS,
    SPOTIFY_CLIENT_CACHE_PATH,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
    SPOTIFY_USER_CACHE_PATH,
    get_logger,
)
from sift.engines.music.models import Track

logger = get_logger("SpotifyService")
SPOTIFY_REQUEST_TIMEOUT = 20
SPOTIFY_STATUS_FORCELIST = (429, 500, 502, 503, 504)


class SpotifyProvider:
    def __init__(self, use_user_auth: bool = True, allow_interactive: bool = True):
        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            raise ValueError("Spotify credentials missing in .env")

        client_cache_handler = CacheFileHandler(cache_path=str(SPOTIFY_CLIENT_CACHE_PATH))
        self.client_auth_manager = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            cache_handler=client_cache_handler,
        )

        if use_user_auth:
            user_cache_handler = CacheFileHandler(cache_path=str(SPOTIFY_USER_CACHE_PATH))
            logger.info("Initializing User Auth (Private Data)")
            self.user_auth_manager = SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope=(
                    "playlist-read-private playlist-read-collaborative "
                    "playlist-modify-private playlist-modify-public"
                ),
                open_browser=False,
                cache_handler=user_cache_handler,
            )

            if not allow_interactive:
                token_info = self.user_auth_manager.cache_handler.get_cached_token()
                if not token_info or not self.user_auth_manager.validate_token(token_info):
                    raise PermissionError("Spotify user authorization is required.")

            self.sp = self._spotify_client(self.user_auth_manager)
            try:
                self.user_id = self.sp.current_user()["id"]
            except Exception:
                if allow_interactive:
                    logger.warning("User auth failed, falling back to client auth.")
                    self.sp = self._spotify_client(self.client_auth_manager)
                else:
                    raise PermissionError("Spotify user authorization is required.")
        else:
            logger.info("Initializing Client Auth (Public Data only)")
            self.sp = self._spotify_client(self.client_auth_manager)

        self.last_library_errors: dict[str, str] = {}

    @staticmethod
    def _spotify_client(auth_manager):
        return spotipy.Spotify(
            auth_manager=auth_manager,
            requests_timeout=SPOTIFY_REQUEST_TIMEOUT,
            retries=4,
            status_retries=4,
            backoff_factor=0.6,
            status_forcelist=SPOTIFY_STATUS_FORCELIST,
        )

    @staticmethod
    def _image_url(images) -> str:
        if not images:
            return ""
        return images[-1].get("url") or images[0].get("url") or ""

    @staticmethod
    def _artist_metadata_by_id(client, artist_ids: list[str]) -> dict[str, dict]:
        metadata = {}
        unique_ids = list(dict.fromkeys(artist_id for artist_id in artist_ids if artist_id))
        for start in range(0, len(unique_ids), 50):
            chunk = unique_ids[start:start + 50]
            if not chunk:
                continue
            try:
                result = client.artists(chunk)
            except Exception as exc:
                logger.warning(f"Spotify artist metadata fetch failed: {exc}")
                continue
            for artist in result.get("artists") or []:
                if artist and artist.get("id"):
                    metadata[artist["id"]] = artist
        return metadata

    def _get_tracks_from_id(self, playlist_id: str) -> List[Track]:
        return self._get_tracks_from_id_with_client(self.sp, playlist_id)

    def _get_tracks_from_id_with_client(self, client, playlist_id: str) -> List[Track]:
        tracks = []
        offset = 0
        while True:
            res = client.playlist_items(
                playlist_id,
                offset=offset,
                limit=100,
                fields=(
                    "items(added_at,added_by(id),"
                    "track(id,uri,name,duration_ms,explicit,popularity,preview_url,"
                    "track_number,disc_number,external_urls(spotify),"
                    "artists(id,name,external_urls(spotify)),"
                    "album(id,name,album_type,release_date,total_tracks,images,external_urls(spotify))))"
                    ",next"
                ),
            )
            if not res.get("items"):
                break

            page_artist_ids = []
            for item in res["items"]:
                track = item.get("track") or {}
                for artist in track.get("artists") or []:
                    if artist.get("id"):
                        page_artist_ids.append(artist["id"])
            artist_metadata = self._artist_metadata_by_id(client, page_artist_ids)

            for item in res["items"]:
                track = item.get("track")
                if track:
                    track_artists = track.get("artists") or []
                    artist_names = [artist.get("name", "") for artist in track_artists if artist.get("name")]
                    artist_ids = [artist.get("id", "") for artist in track_artists if artist.get("id")]
                    artist_urls = [
                        (artist.get("external_urls") or {}).get("spotify", "")
                        for artist in track_artists
                        if (artist.get("external_urls") or {}).get("spotify")
                    ]
                    artist_genres = []
                    for artist_id in artist_ids:
                        for genre in (artist_metadata.get(artist_id) or {}).get("genres") or []:
                            if genre not in artist_genres:
                                artist_genres.append(genre)
                    artist = artist_names[0] if artist_names else "Unknown"
                    album = track.get("album") or {}
                    tracks.append(
                        Track(
                            title=track["name"],
                            artist=artist,
                            artists=artist_names,
                            artist_ids=artist_ids,
                            artist_urls=artist_urls,
                            artist_genres=artist_genres,
                            album=album.get("name", ""),
                            album_id=album.get("id") or "",
                            album_type=album.get("album_type") or "",
                            album_release_date=album.get("release_date") or "",
                            album_total_tracks=album.get("total_tracks") or 0,
                            album_url=(album.get("external_urls") or {}).get("spotify", ""),
                            image_url=self._image_url(album.get("images")),
                            duration_ms=track.get("duration_ms") or 0,
                            explicit=bool(track.get("explicit")),
                            popularity=track.get("popularity") or 0,
                            spotify_id=track.get("id") or "",
                            spotify_uri=track.get("uri") or "",
                            spotify_url=(track.get("external_urls") or {}).get("spotify", ""),
                            track_number=track.get("track_number") or 0,
                            disc_number=track.get("disc_number") or 0,
                            added_at=item.get("added_at") or "",
                            added_by=(item.get("added_by") or {}).get("id") or "",
                            preview_url=track.get("preview_url") or "",
                        )
                    )

            if not res.get("next"):
                break
            offset += len(res["items"])
        return tracks

    def _get_tracks_from_id_for_worker(self, playlist_id: str) -> List[Track]:
        client = self._spotify_client(self.user_auth_manager)
        return self._get_tracks_from_id_with_client(client, playlist_id)

    def _get_tracks_from_album(self, album_id: str, album_name: str) -> List[Track]:
        tracks = []
        offset = 0
        while True:
            res = self.sp.album_tracks(album_id, limit=50, offset=offset)
            if not res.get("items"):
                break

            for track in res["items"]:
                if track:
                    artist = track["artists"][0]["name"] if track["artists"] else "Unknown"
                    tracks.append(
                        Track(
                            title=track["name"],
                            artist=artist,
                            album=album_name,
                            duration_ms=track.get("duration_ms") or 0,
                        )
                    )

            if not res.get("next"):
                break
            offset += len(res["items"])
        return tracks

    def _get_track_from_id(self, track_id: str) -> Track:
        track = self.sp.track(track_id)
        artist = track["artists"][0]["name"] if track.get("artists") else "Unknown"
        album = track.get("album") or {}
        return Track(
            title=track["name"],
            artist=artist,
            album=album.get("name", ""),
            image_url=self._image_url(album.get("images")),
            duration_ms=track.get("duration_ms") or 0,
        )

    def _fetch_playlist_batch(
        self,
        playlist_refs: list[tuple[str, str]],
        worker_count: int,
        *,
        retry_pass: bool = False,
    ) -> tuple[dict[str, list[Track]], dict[str, str]]:
        playlists = {}
        errors = {}
        if not playlist_refs:
            return playlists, errors

        if retry_pass:
            time.sleep(1.5)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_ref = {
                executor.submit(self._get_tracks_from_id_for_worker, playlist_id): (name, playlist_id)
                for name, playlist_id in playlist_refs
            }
            for future in as_completed(future_to_ref):
                name, _playlist_id = future_to_ref[future]
                try:
                    playlists[name] = future.result()
                except Exception as exc:
                    errors[name] = str(exc)
                    level = "retry" if retry_pass else "initial"
                    logger.warning(f"Spotify {level} fetch failed for playlist '{name}': {exc}")
        return playlists, errors

    def fetch_user_library(self, owned_only: bool = False) -> Dict[str, List[Track]]:
        if not hasattr(self, "user_auth_manager"):
            raise PermissionError("Cannot fetch user library in public mode.")

        scope = "owned playlists" if owned_only else "library"
        logger.info(f"Fetching {scope} for user: {self.user_id}")
        playlist_refs = []
        seen_names = set()
        offset = 0
        while True:
            res = self.sp.current_user_playlists(limit=50, offset=offset)
            if not res["items"]:
                break
            for playlist in res["items"]:
                owner = playlist.get("owner") or {}
                if owned_only and owner.get("id") != self.user_id:
                    continue
                name = playlist["name"]
                display_name = name
                duplicate_count = 2
                while display_name in seen_names:
                    display_name = f"{name} ({duplicate_count})"
                    duplicate_count += 1
                seen_names.add(display_name)
                playlist_refs.append((display_name, playlist["id"]))
            offset += len(res["items"])

        playlists = {}
        if not playlist_refs:
            return playlists

        worker_count = min(max(SPOTIFY_FETCH_WORKERS, 1), len(playlist_refs))
        logger.info(f"Fetching tracks for {len(playlist_refs)} playlists with {worker_count} workers")
        playlists, errors = self._fetch_playlist_batch(playlist_refs, worker_count)

        if errors:
            failed_refs = [(name, playlist_id) for name, playlist_id in playlist_refs if name in errors]
            retry_workers = min(2, len(failed_refs))
            logger.info(f"Retrying {len(failed_refs)} failed playlist(s) with {retry_workers} worker(s)")
            retried_playlists, retry_errors = self._fetch_playlist_batch(
                failed_refs,
                retry_workers,
                retry_pass=True,
            )
            playlists.update(retried_playlists)
            self.last_library_errors = retry_errors

        if not playlists and self.last_library_errors:
            raise RuntimeError(
                "Spotify timed out while fetching playlist tracks. "
                "Retry after a minute or reduce concurrent playlist fetching."
            )

        return {name: playlists[name] for name, _ in playlist_refs if name in playlists}

    def fetch_by_url(self, url: str) -> Dict[str, List[Track]]:
        track_match = re.search(r"track/([a-zA-Z0-9]+)", url)
        if track_match:
            track_id = track_match.group(1)
            try:
                track = self._get_track_from_id(track_id)
            except spotipy.exceptions.SpotifyException as exc:
                raise ValueError("Track not found.") from exc
            logger.info(f"Detected Track: {track.title} - {track.artist}")
            return {"Single Track": [track]}

        playlist_match = re.search(r"playlist/([a-zA-Z0-9]+)", url)
        if playlist_match:
            playlist_id = playlist_match.group(1)
            try:
                name = self.sp.playlist(playlist_id, fields="name")["name"]
            except spotipy.exceptions.SpotifyException as exc:
                raise PermissionError("Playlist not found.") from exc
            logger.info(f"Detected Playlist: {name}")
            return {name: self._get_tracks_from_id(playlist_id)}

        album_match = re.search(r"album/([a-zA-Z0-9]+)", url)
        if album_match:
            album_id = album_match.group(1)
            try:
                album_data = self.sp.album(album_id)
                name = album_data["name"]
            except spotipy.exceptions.SpotifyException as exc:
                raise ValueError("Album not found.") from exc
            logger.info(f"Detected Album: {name}")
            return {name: self._get_tracks_from_album(album_id, name)}

        raise ValueError("Invalid Spotify URL.")
