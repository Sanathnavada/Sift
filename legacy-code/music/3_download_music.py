import subprocess
import re
import os
import sys
import time
from pathlib import Path

# ===================== CONFIGURATION =====================
NODE_PATH = r"D:\nodejs\node.exe"   # Your node path
URLS_FILE = Path("urls.txt")
ARCHIVE_FILE = Path("downloaded.txt") # The "Checkpoint" file
COOKIES_FILE = Path("cookies.txt")    # [NEW] Look for this file first
ROOT_OUTPUT_DIR = Path("downloads")

# Browser fallback if cookies.txt is missing
BROWSER_FALLBACK = "chrome" 

# Sleep settings to mimic human behavior (Anti-Ban)
MIN_SLEEP = 5
MAX_SLEEP = 15
# =========================================================

def sanitize_folder_name(name):
    """Removes illegal characters from folder names."""
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', name)
    return cleaned.strip()

def parse_urls_file():
    """Parses urls.txt into a dictionary."""
    if not URLS_FILE.exists():
        print(f"❌ Error: {URLS_FILE} not found.")
        return {}

    playlists = {}
    current_playlist = "Uncategorized"
    
    print(f"📖 Reading {URLS_FILE}...")

    with URLS_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("---") or line.startswith("ID:") or "Tracks expected:" in line:
                continue

            if line.lower().startswith("playlist:"):
                raw_name = line.split(":", 1)[1].strip()
                current_playlist = sanitize_folder_name(raw_name)
                if current_playlist not in playlists:
                    playlists[current_playlist] = []
                continue

            if "youtube.com" in line or "youtu.be" in line:
                if current_playlist not in playlists:
                    playlists[current_playlist] = []
                if line not in playlists[current_playlist]:
                    playlists[current_playlist].append(line)

    return playlists

def run_yt_dlp_robust(playlist_name, urls):
    """
    Runs yt-dlp with smart authentication handling.
    """
    if not urls:
        return

    target_dir = ROOT_OUTPUT_DIR / playlist_name
    target_dir.mkdir(parents=True, exist_ok=True)

    temp_url_file = target_dir / "batch_urls.txt"
    with open(temp_url_file, "w", encoding="utf-8") as f:
        f.write("\n".join(urls))

    print(f"\n🎵 Processing Playlist: {playlist_name}")
    print(f"   📂 Output: {target_dir}")
    print(f"   🔗 Tracks: {len(urls)}")

    # --- Build the Command ---
    cmd = [
        "yt-dlp",
        "--js-runtimes", f"node:{NODE_PATH}",
        
        # Audio Settings
        "-f", "251/140/bestaudio",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        
        # Resume/Checkpoint System
        "--download-archive", str(ARCHIVE_FILE.resolve()), 
        
        # Output Template
        "-o", str(target_dir / "%(title)s.%(ext)s"),
        
        # Input Source
        "-a", str(temp_url_file),
        
        # Robustness
        "--ignore-errors",      
        "--no-warnings",
        "--retries", "10",
        
        # Sleep (Anti-bot)
        "--sleep-interval", str(MIN_SLEEP),
        "--max-sleep-interval", str(MAX_SLEEP),
    ]

    # --- Smart Authentication Selector ---
    # 1. Try to use cookies.txt (Best/Most Stable)
    if COOKIES_FILE.exists():
        cmd.extend(["--cookies", str(COOKIES_FILE.resolve())])
    # 2. Fallback to Browser (Requires closing browser)
    else:
        print(f"   ⚠️  'cookies.txt' not found. Trying to pull from {BROWSER_FALLBACK}...")
        print(f"   ℹ️  NOTE: If this fails, CLOSE {BROWSER_FALLBACK} COMPLETELY.")
        cmd.extend(["--cookies-from-browser", BROWSER_FALLBACK])

    try:
        subprocess.run(cmd, check=True)
        print(f"✅ Playlist '{playlist_name}' processed.")
    
    except subprocess.CalledProcessError:
        print(f"⚠️  Warning: Issues in '{playlist_name}'. Non-fatal errors skipped.")
    except KeyboardInterrupt:
        print("\n🛑 Process interrupted by user. Progress saved.")
        if temp_url_file.exists(): temp_url_file.unlink()
        sys.exit(0)
    finally:
        if temp_url_file.exists():
            temp_url_file.unlink()

def main():
    print("🎧 Smart YouTube Downloader (Checkpoint + Cookies Support)")
    print("=" * 60)
    
    # Check for cookies file status
    if COOKIES_FILE.exists():
        print(f"🍪 Found 'cookies.txt'. using file authentication (Stable).")
    else:
        print(f"🟠 'cookies.txt' missing. Will try to steal cookies from {BROWSER_FALLBACK}.")
        print(f"   (For better stability, export cookies.txt to this folder)")

    playlists_data = parse_urls_file()

    if not playlists_data:
        print("❌ No playlists or URLs found!")
        return

    total = len(playlists_data)
    print(f"\n✅ Found {total} playlist(s). Starting...")
    
    for index, (name, urls) in enumerate(playlists_data.items(), 1):
        print(f"\n--- [ Playlist {index}/{total} ] ---")
        run_yt_dlp_robust(name, urls)

    print("\n✅ All jobs finished!")

if __name__ == "__main__":
    main()