import yt_dlp
import sqlite3
import logging
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Set

# ======================= CONFIGURATION =======================
INPUT_FILE = Path("urls.txt")
ROOT_OUTPUT_DIR = Path("downloads")
COOKIES_FILE = Path("cookies.txt")
DB_FILE = Path("download_history.db")
LOG_FILE = Path("download_errors.log")

# Concurrency: Don't go too high or YouTube will throttle you
MAX_WORKERS = 3

# Audio Quality: 'bestaudio/best' gets the highest quality source
# We default to 'm4a' container for compatibility without re-encoding quality loss
TARGET_FORMAT = "bestaudio[ext=m4a]/bestaudio/best"
# =============================================================

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

class HistoryDB:
    """
    A persistent SQLite database to track downloaded Video IDs.
    This replaces the 'downloaded.txt' file for reliability and speed.
    """
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = None
        self._init_db()

    def _init_db(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                video_id TEXT PRIMARY KEY,
                title TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def exists(self, video_id: str) -> bool:
        cursor = self.conn.execute("SELECT 1 FROM downloads WHERE video_id = ?", (video_id,))
        return cursor.fetchone() is not None

    def add(self, video_id: str, title: str):
        try:
            self.conn.execute("INSERT OR IGNORE INTO downloads (video_id, title) VALUES (?, ?)", (video_id, title))
            self.conn.commit()
        except Exception as e:
            logging.error(f"DB Write Error: {e}")

    def close(self):
        if self.conn:
            self.conn.close()

class DownloadManager:
    def __init__(self):
        self.db = HistoryDB(DB_FILE)
        
    def parse_input_file(self) -> Dict[str, List[str]]:
        """Parses the urls.txt file into {PlaylistName: [URLs]}."""
        if not INPUT_FILE.exists():
            logging.critical(f"Input file {INPUT_FILE} not found!")
            return {}

        playlists = {}
        current_playlist = "Uncategorized"

        with INPUT_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("---") or line.startswith("ID:") or "Tracks expected:" in line:
                    continue

                if line.lower().startswith("playlist:"):
                    # Clean folder name
                    raw_name = line.split(":", 1)[1].strip()
                    current_playlist = "".join([c for c in raw_name if c.isalnum() or c in " -_"]).strip()
                    if current_playlist not in playlists:
                        playlists[current_playlist] = []
                    continue

                if "http" in line:
                    if current_playlist not in playlists:
                        playlists[current_playlist] = []
                    playlists[current_playlist].append(line)
        
        return playlists

    def get_video_id_from_url(self, url: str) -> str:
        """Extracts ID quickly without network call if possible."""
        if "v=" in url:
            return url.split("v=")[1].split("&")[0]
        elif "youtu.be/" in url:
            return url.split("youtu.be/")[1].split("?")[0]
        return url # Fallback, let yt-dlp handle it

    def download_track(self, url: str, playlist_name: str):
        """
        The Worker Function.
        Executed by the ThreadPool.
        """
        # 1. Pre-check DB to save network calls
        # (This is a fuzzy check based on URL, the real check happens inside yt-dlp hook)
        # But we do a quick check here if ID is obvious
        vid_id_guess = self.get_video_id_from_url(url)
        if self.db.exists(vid_id_guess):
            logging.info(f"⏭️  Skipping (Already Downloaded): {vid_id_guess}")
            return

        # 2. Output Path Configuration
        output_template = ROOT_OUTPUT_DIR / playlist_name / "%(title)s [%(id)s].%(ext)s"

        # 3. yt-dlp Configuration Options
        ydl_opts = {
            'format': TARGET_FORMAT, # The fix for "Lossy-to-Lossy"
            'outtmpl': str(output_template),
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            
            # Metadata & Embedding
            'writethumbnail': True,
            'addmetadata': True,
            'postprocessors': [
                {'key': 'FFmpegMetadata'}, # Writes ID3 tags
                {'key': 'EmbedThumbnail'}, # Embeds album art
            ],
            
            # Anti-Ban / Throttling
            'sleep_interval': 2,
            'max_sleep_interval': 10,
        }

        # Cookie handling
        if COOKIES_FILE.exists():
            ydl_opts['cookiefile'] = str(COOKIES_FILE)

        # 4. Execution
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # We need to extract info first to get the REAL ID for DB checking
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    logging.warning(f"❌ Failed to extract info: {url}")
                    return

                real_id = info.get('id')
                title = info.get('title')

                # Final DB Check with confirmed ID
                if self.db.exists(real_id):
                    logging.info(f"⏭️  Skipping (DB Match): {title}")
                    return

                # Perform Download
                print(f"⬇️  Downloading: {title}...")
                ydl.download([url])
                
                # Success - Log to DB
                self.db.add(real_id, title)
                logging.info(f"✅ Completed: {title}")

        except Exception as e:
            logging.error(f"💥 Critical Error downloading {url}: {str(e)}")

    def run(self):
        print("🎧 Production Grade Music Downloader")
        print(f"📂 Output: {ROOT_OUTPUT_DIR}")
        print(f"🍪 Cookies: {'Found' if COOKIES_FILE.exists() else 'Missing (Recommended)'}")
        print("-" * 50)

        playlists = self.parse_input_file()
        
        # Flatten the list of tasks for the ThreadPool
        # Each task is a tuple: (url, playlist_name)
        tasks = []
        for pl_name, urls in playlists.items():
            for url in urls:
                tasks.append((url, pl_name))

        print(f"🚀 Queueing {len(tasks)} downloads across {len(playlists)} playlists...")

        # Concurrent Execution
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(self.download_track, url, pl): url for url, pl in tasks}
            
            for future in concurrent.futures.as_completed(futures):
                url = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    logging.error(f"Thread generated an exception for {url}: {exc}")

        self.db.close()
        print("\n✨ All operations finished.")

if __name__ == "__main__":
    # Ensure FFmpeg is installed or in path!
    manager = DownloadManager()
    manager.run()