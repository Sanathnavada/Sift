from pathlib import Path

from sift.engines.music.services.spotify import SpotifyProvider


OUTPUT_PATH = Path("var/music_config/spotify_my_playlists_tracks.txt")


def playlist_tracks(sp, playlist_id: str) -> list[dict]:
    tracks = []
    offset = 0
    while True:
        page = sp.playlist_items(
            playlist_id,
            offset=offset,
            limit=100,
            fields=(
                "items(track(name,artists(name),album(name),external_urls(spotify))),"
                "next"
            ),
        )
        items = page.get("items") or []
        for item in items:
            track = item.get("track") or {}
            if not track or track.get("is_local"):
                continue
            artists = ", ".join(artist.get("name", "") for artist in track.get("artists", []))
            album = track.get("album") or {}
            tracks.append(
                {
                    "title": track.get("name") or "",
                    "artists": artists,
                    "album": album.get("name") or "",
                    "url": (track.get("external_urls") or {}).get("spotify", ""),
                }
            )
        if not page.get("next"):
            break
        offset += len(items)
    return tracks


def main() -> None:
    provider = SpotifyProvider(use_user_auth=True)
    sp = provider.sp
    user_id = provider.user_id

    owned_playlists = []
    offset = 0
    while True:
        page = sp.current_user_playlists(limit=50, offset=offset)
        items = page.get("items") or []
        for playlist in items:
            owner = playlist.get("owner") or {}
            if owner.get("id") == user_id:
                owned_playlists.append(playlist)
        if not page.get("next"):
            break
        offset += len(items)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        handle.write("Spotify Playlists Created By Me\n")
        handle.write("===============================\n")
        handle.write(f"Spotify user id: {user_id}\n")
        handle.write(f"Playlists: {len(owned_playlists)}\n\n")

        for index, playlist in enumerate(owned_playlists, 1):
            tracks = playlist_tracks(sp, playlist["id"])
            handle.write(f"[{index}] {playlist['name']}\n")
            handle.write(f"Playlist URL: {(playlist.get('external_urls') or {}).get('spotify', '')}\n")
            handle.write(f"Tracks: {len(tracks)}\n")
            handle.write("-" * 80 + "\n")
            for track_index, track in enumerate(tracks, 1):
                handle.write(
                    f"{track_index}. {track['title']} - {track['album']} - "
                    f"{track['artists']}"
                )
                if track["url"]:
                    handle.write(f" - {track['url']}")
                handle.write("\n")
            handle.write("\n")

    print(f"Exported {len(owned_playlists)} owned playlists to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
