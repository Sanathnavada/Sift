"""
MUSIC NODE — Spotify → YouTube → Download Pipeline
====================================================

MODES
-----
  user   Fetch your entire Spotify library, choose playlists interactively, then download.
  link   Fetch a single Spotify playlist or album by URL, then download.
  song   Search YouTube for a single song by name and download it directly.
  yt     Download audio from a YouTube video — no Spotify involved.
         Accepts a direct YouTube URL or a free-text video name.

CREDENTIALS
-----------
  No username/password flags. Credentials live in a .env file in the project root:

    SPOTIPY_CLIENT_ID=your_client_id
    SPOTIPY_CLIENT_SECRET=your_client_secret
    SPOTIPY_REDIRECT_URI=http://localhost:8888/callback

  user mode uses Spotify OAuth — on first run a browser tab opens for you to
  approve access. The token is then cached in config/.cache for future runs.
  link and song modes use app-only auth (no browser needed).
  yt mode needs no credentials at all.

USAGE
-----

  # --- MODE: user ---
  # Fetches all playlists from your Spotify account via OAuth.
  # First run opens a browser tab to approve Spotify access (token cached after that).
  python -m main --mode user

  # --- MODE: link ---
  # Downloads a specific Spotify playlist or album via its public URL.
  python -m main --mode link --input "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"

  # --- MODE: song ---
  # Searches YouTube for a song by name and downloads the best match.
  python -m main --mode song --input "Bohemian Rhapsody Queen"
  python -m main --mode song --input "Blinding Lights The Weeknd"

  # --- MODE: yt ---
  # Download audio by direct YouTube URL (fastest — skips search entirely):
  python -m main --mode yt --input "https://youtu.be/_yhBlE3D9cc" --outdir D:\Music\bahava_bhaktigeete\venkatesha_stotram

  # Download audio by video name (runs keyword search, same engine as 'song' mode):
  python -m main --mode yt --input "Rick Astley Never Gonna Give You Up" --outdir ./downloads

  # Ephemeral — no --outdir → file saved to system temp, path printed (for future UI use):
  python -m main --mode yt --input "https://youtu.be/dQw4w9WgXcQ"

INTERACTIVE MENU (user mode)
-----------------------------
  When prompted, select playlists by number:
    5          → download playlist #5 only
    1, 3, 5    → download playlists #1, #3, and #5
    all        → download everything
    (Enter)    → download everything

OUTPUT
------
  Music files are saved to the directory configured in config/config.py (or --outdir for yt mode).
  A text backup of playlist metadata is appended to the Spotify backup log file.
  Download history is tracked in download_history.db (skips already-downloaded tracks).
  yt mode is always ephemeral — no DB writes, no history tracking.
"""

import argparse
import sys
from pathlib import Path
from config.config import get_logger, SPOTIFY_BACKUP_FILE
from models import Track
from services.spotify import SpotifyProvider
from services.youtube import YouTubeResolver
from services.downloader import MusicDownloader

logger = get_logger("Main")


def _is_youtube_url(text: str) -> bool:
    """Returns True if the input looks like a direct YouTube URL."""
    return text.startswith("http") and ("youtube.com" in text or "youtu.be" in text)


def save_text_backup(playlist_name: str, tracks: list):
    """Saves a readable text backup of the playlist metadata."""
    with open(SPOTIFY_BACKUP_FILE, "a", encoding="utf-8") as f:
        f.write(f"\nPlaylist: {playlist_name}\n")
        f.write(f"Tracks: {len(tracks)}\n")
        f.write("-" * 20 + "\n")
        for i, t in enumerate(tracks, 1):
            f.write(f"{i}. {t.title} — {t.artist} [{t.album}]\n")
        f.write("-" * 40 + "\n")


def filter_playlists_interactively(playlists: dict) -> dict:
    """
    The Intervention Layer: Shows a menu and returns only the playlists the user wants.
    """
    print("\n📜 Spotify Library Fetched:")
    print("=" * 50)

    # Create an indexed map: { 1: "Playlist A", 2: "Playlist B" }
    names = list(playlists.keys())

    for i, name in enumerate(names, 1):
        count = len(playlists[name])
        print(f"  [{i}] {name} ({count} songs)")

    print("=" * 50)
    print("👉 Options:")
    print("   - Type a specific number (e.g. '5')")
    print("   - Type multiple numbers comma-separated (e.g. '1, 3, 5')")
    print("   - Type 'all' or press Enter to download everything")

    choice = input("\nEnter selection: ").strip().lower()

    # 1. Handle "ALL"
    if choice in ['all', 'a', '']:
        print("✅ Selected: ALL Playlists")
        return playlists

    # 2. Handle Specific Numbers
    selected_playlists = {}
    try:
        indices = [int(x.strip()) for x in choice.split(',') if x.strip().isdigit()]

        for idx in indices:
            if 1 <= idx <= len(names):
                name = names[idx - 1]
                selected_playlists[name] = playlists[name]
            else:
                print(f"⚠️  Skipping invalid number: {idx}")

        if not selected_playlists:
            print("❌ No valid selections made. Exiting.")
            sys.exit(0)

        print(f"✅ Selected {len(selected_playlists)} playlists.")
        return selected_playlists

    except Exception as e:
        logger.error(f"Input parsing error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Modular Music Pipeline")
    parser.add_argument("--mode", choices=["user", "link", "song", "yt"], required=True)
    parser.add_argument("--input", help="URL or Song/Video Name", default="")
    parser.add_argument("--outdir", default=None,
                        help="(yt mode) Directory to save the audio file. "
                             "Omit to use ephemeral temp storage (for future UI use).")
    args = parser.parse_args()

    # Initialize Services
    try:
        if args.mode == "link":
            sp = SpotifyProvider(use_user_auth=False)
        elif args.mode == "user":
            sp = SpotifyProvider(use_user_auth=True)
    except Exception as e:
        logger.error(str(e))
        return

    resolver = YouTubeResolver()

    # --- DATA COLLECTION ---
    playlists_to_process = {}

    if args.mode == "song":
        if not args.input:
            logger.error("Mode 'song' requires --input")
            return

        track = Track(title=args.input, is_dummy=True)
        print(f"\n🚀 Processing Single Song: {args.input}")

        # V3.2 UPDATE: Use the new Retry method
        url, err = resolver._search_single_with_retry(track)

        if url:
            # Per-song isolation: fresh downloader instance
            dl = MusicDownloader()
            dl.download_all({"Single Downloads": [url]})
        else:
            print(f"❌ Could not resolve song: {err}")
        return

    elif args.mode == "yt":
        if not args.input:
            logger.error("Mode 'yt' requires --input (YouTube URL or video name)")
            return

        # Step 1: Resolve — direct URL or keyword search
        if _is_youtube_url(args.input):
            url = args.input
            print(f"\n🔗 Direct YouTube URL — skipping search.")
        else:
            print(f"\n🔍 Searching YouTube for: '{args.input}'")
            track = Track(title=args.input, is_dummy=True)
            url, err = resolver._search_single_with_retry(track)
            if not url:
                print(f"❌ Could not find a match: {err}")
                return
            print(f"✅ Matched: {url}")

        # Step 2: Download
        # Always ephemeral (no DB / history tracking); outdir controls save location.
        outdir = Path(args.outdir) if args.outdir else None
        dl = MusicDownloader(ephemeral=True, outdir=outdir)
        result = dl.download_all({"YouTube Downloads": [url]})

        if result["files"]:
            saved_path = result["files"][0]
            if outdir:
                print(f"✅ Saved to: {saved_path}")
            else:
                print(f"📦 Ephemeral path: {saved_path}  (pass --outdir to persist)")
        else:
            print("❌ Download failed.")
        return

    elif args.mode == "link":
        playlists_to_process = sp.fetch_by_url(args.input)

    elif args.mode == "user":
        full_library = sp.fetch_user_library()
        if not full_library:
            print("❌ No playlists found.")
            return
        playlists_to_process = filter_playlists_interactively(full_library)

    # --- PIPELINE START ---
    total = len(playlists_to_process)
    print(f"\n📋 Queueing {total} playlists...")
    print("=" * 60)

    # Reset backup file
    if not SPOTIFY_BACKUP_FILE.exists():
        with open(SPOTIFY_BACKUP_FILE, "w", encoding="utf-8") as f:
            f.write("Spotify Backup Log\n==================\n")

    for index, (p_name, tracks) in enumerate(playlists_to_process.items(), 1):
        print(f"\n▶️  [{index}/{total}] Processing: {p_name} ({len(tracks)} tracks)")

        # Backup
        save_text_backup(p_name, tracks)

        if not tracks:
            print("   ⚠️  Empty playlist, skipping.")
            continue

        # --- RESOLUTION PHASE (V3.2 Update) ---
        # resolve_all now returns { "urls": [...], "failed": [(Track, Reason)] }
        resolution_data = resolver.resolve_all({p_name: tracks})
        playlist_data = resolution_data.get(p_name, {})

        urls = playlist_data.get("urls", [])
        failed_items = playlist_data.get("failed", [])

        if not urls:
            print("   ❌ No songs resolved. Skipping.")
            continue

        # --- DOWNLOAD PHASE ---
        # Fresh Downloader prevents DB locks and resets stats
        downloader = MusicDownloader()
        result = downloader.download_all({p_name: urls})
        stats = result['stats']

        # --- REPORTING PHASE (V3.2 Update) ---
        # Handle the case where stats might be None (if downloader failed critically)
        if not stats:
            stats = {'downloaded': 0, 'skipped': 0, 'failed': 0}

        print(f"\n📊 Summary for '{p_name}':")
        print(f"   - Found on Spotify: {len(tracks)}")
        print(f"   - Resolved (YT):    {len(urls)}")
        print(f"   - Downloaded New:   {stats['downloaded']}")
        print(f"   - Skipped (Exists): {stats['skipped']}")
        print(f"   - Failed Downloads: {stats['failed']}")
        print(f"   - Failed Matches:   {len(failed_items)}")

        # 1. Print Network/File Failures
        if stats['failed'] > 0:
            print(f"   ⚠️  Check logs for {stats['failed']} network/file errors.")

        # 2. Print Resolution Failures (Detailed)
        if failed_items:
            print("\n   ❌ Failed to Find Matches for:")
            for failure in failed_items:
                track = failure["track"]
                print(f"      - {track.title} ({track.artist}) -> {failure['error']}")

        print(f"✅ Playlist '{p_name}' Complete.\n")

    print("\n🎉 All Jobs Finished!")


if __name__ == "__main__":
    main()
