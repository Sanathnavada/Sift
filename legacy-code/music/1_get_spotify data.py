#!/usr/bin/env python3
"""
spotify_backup_debug.py
Same as spotify_backup.py but:
 - Writes playlists_owners_debug.txt to show owner.id and owner.display_name for each playlist
 - Filters to playlists where owner.id == current_user.id
 - Saves JSON + TXT backups only for filtered playlists
Requirements:
 - spotipy
 - python-dotenv
"""

import os
import time
import json
from pathlib import Path
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from typing import List, Dict, Any

load_dotenv()

CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI", "http://localhost:8888/callback")

if not CLIENT_ID or not CLIENT_SECRET:
    raise SystemExit("Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET in env or .env file")

SCOPE = "playlist-read-private playlist-read-collaborative"

OUT_JSON = Path("spotify_playlists_backup.json")
OUT_TXT = Path("spotify_playlists_backup.txt")
DEBUG_TXT = Path("playlists_owners_debug.txt")

def fetch_all_playlists(sp: spotipy.Spotify) -> List[Dict[str, Any]]:
    playlists = []
    limit = 50
    offset = 0
    while True:
        res = sp.current_user_playlists(limit=limit, offset=offset)
        items = res.get("items", [])
        if not items:
            break
        playlists.extend(items)
        offset += len(items)
        if len(items) < limit:
            break
    return playlists

def fetch_all_tracks_for_playlist(sp: spotipy.Spotify, playlist_id: str) -> List[Dict[str, Any]]:
    tracks = []
    limit = 100
    offset = 0
    while True:
        res = sp.playlist_items(
            playlist_id,
            offset=offset,
            limit=limit,
            fields="items(track(name,artists(name),album(name),id,uri,added_by(id,uri))),next"
        )
        items = res.get("items", [])
        if not items:
            break
        for it in items:
            track = it.get("track")
            tracks.append(track)
        offset += len(items)
        if len(items) < limit:
            break
    return tracks

def track_to_string(track: Dict[str, Any]) -> str:
    if not track:
        return "<removed/unavailable track>"
    name = track.get("name", "Unknown Title")
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []) if a)
    album = track.get("album", {}).get("name", "")
    uri = track.get("uri", "")
    return f"{name} — {artists} [{album}] ({uri})"

def main():
    auth_manager = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        show_dialog=True
    )
    sp = spotipy.Spotify(auth_manager=auth_manager)

    user = sp.current_user()
    my_user_id = user.get("id")
    username = user.get("display_name") or my_user_id
    print(f"Authenticated as: {username} (id: {my_user_id})")

    print("Fetching playlists...")
    all_playlists = fetch_all_playlists(sp)
    print(f"Found {len(all_playlists)} total playlists in library")

    # --- DEBUG: list owner ids & names so you can inspect why something is included ---
    debug_lines = []
    debug_lines.append(f"Debug playlist owners for account: {username} (id: {my_user_id})")
    debug_lines.append(f"Total playlists returned by API: {len(all_playlists)}")
    debug_lines.append("")
    debug_lines.append(f"{'idx':>3}  {'tracks':>5}  {'is_owner':>8}  {'owner_id':36}  {'owner_display_name':40}  playlist_name")
    debug_lines.append("-" * 140)

    for i, p in enumerate(all_playlists, 1):
        owner = p.get("owner") or {}
        owner_id = owner.get("id")
        owner_name = owner.get("display_name") or ""
        total_tracks = p.get("tracks", {}).get("total", 0)
        is_owner = (owner_id == my_user_id)
        debug_lines.append(f"{i:3d}  {total_tracks:5d}  {str(is_owner):>8}  {repr(owner_id):36}  {repr(owner_name):40}  {p.get('name')}")
    DEBUG_TXT.write_text("\n".join(debug_lines), encoding="utf-8")
    print(f"Wrote debug owner list to: {DEBUG_TXT.resolve()}")

    # --- FILTER to playlists owned by current user (strict owner.id match) ---
    playlists = [p for p in all_playlists if (p.get("owner", {}).get("id") == my_user_id and p.get("tracks", {}).get("total", 0) > 0)]
    print(f"After filtering to playlists owned by you and with >0 tracks: Found {len(playlists)} (from {len(all_playlists)})")

    backup = {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user": {"id": my_user_id, "display_name": user.get("display_name")},
        "playlists": []
    }

    txt_lines = []
    txt_lines.append(f"Spotify playlists backup for {username}")
    txt_lines.append(f"Fetched at: {backup['fetched_at']}")
    txt_lines.append("")

    for idx, p in enumerate(playlists, 1):
        p_name = p.get("name")
        p_id = p.get("id")
        p_owner = p.get("owner", {}).get("display_name") or p.get("owner", {}).get("id")
        p_total = p.get("tracks", {}).get("total", 0)
        print(f"[{idx}/{len(playlists)}] {p_name} ({p_total} tracks)")

        # fetch tracks (paginated)
        tracks = fetch_all_tracks_for_playlist(sp, p_id)

        p_record = {
            "id": p_id,
            "name": p_name,
            "owner": p_owner,
            "snapshot_id": p.get("snapshot_id"),
            "total_tracks_expected": p_total,
            "tracks_fetched": len(tracks),
            "tracks": tracks
        }
        backup["playlists"].append(p_record)

        txt_lines.append(f"Playlist: {p_name}")
        txt_lines.append(f"ID: {p_id}  | Owner: {p_owner}  | Tracks expected: {p_total}  | Tracks fetched: {len(tracks)}")
        txt_lines.append("")
        for i, t in enumerate(tracks, 1):
            txt_lines.append(f"{i}. {track_to_string(t)}")
        txt_lines.append("\n" + ("-" * 60) + "\n")

    with OUT_JSON.open("w", encoding="utf-8") as jf:
        json.dump(backup, jf, ensure_ascii=False, indent=2)
    print(f"Wrote JSON backup to: {OUT_JSON.resolve()}")

    with OUT_TXT.open("w", encoding="utf-8") as tf:
        tf.write("\n".join(txt_lines))
    print(f"Wrote human-readable backup to: {OUT_TXT.resolve()}")

if __name__ == "__main__":
    main()
