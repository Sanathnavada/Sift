import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Credentials ---
SPOTIFY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI", "http://localhost:8888/callback")

# --- Paths ---
# This module lives in src/sift/engines/music/config.py. Keep runtime state
# outside source code so caches, DBs, cookies, and downloads do not pollute the
# package. Environment variables still allow Docker/local overrides.
REPO_ROOT = Path(os.getenv("SIFT_ROOT", str(Path(__file__).resolve().parents[4])))
STATE_DIR = Path(os.getenv("MUSIC_CONFIG_DIR", str(REPO_ROOT / "var" / "music_config"))).expanduser()
STATE_DIR.mkdir(parents=True, exist_ok=True)

ROOT_OUTPUT_DIR = Path(os.getenv("MUSIC_OUTPUT_DIR", str(REPO_ROOT / "downloads" / "music"))).expanduser()
ROOT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CACHE_FILE = STATE_DIR / "youtube_cache.json"
DB_FILE = STATE_DIR / "download_history.db"
COOKIES_FILE = STATE_DIR / "cookies.txt"
SPOTIFY_USER_CACHE_PATH = STATE_DIR / ".spotify_user_cache"
SPOTIFY_CLIENT_CACHE_PATH = STATE_DIR / ".spotify_client_cache"
# Backwards-compatible alias for code that expects the user OAuth cache path.
SPOTIFY_CACHE_PATH = SPOTIFY_USER_CACHE_PATH
SPOTIFY_BACKUP_FILE = STATE_DIR / "spotify_playlists_backup.txt"

# --- Tuning ---
def _env_int_or_auto(name: str, auto_value: int) -> int:
    value = (os.getenv(name) or "auto").strip().lower()
    if value == "auto":
        return max(auto_value, 1)
    try:
        return max(int(value), 1)
    except ValueError:
        return max(auto_value, 1)


CPU_COUNT = max(os.cpu_count() or 1, 1)
YOUTUBE_RESOLVE_WORKERS = _env_int_or_auto("YOUTUBE_RESOLVE_WORKERS", min(8, max(2, CPU_COUNT)))
SPOTIFY_FETCH_WORKERS = _env_int_or_auto("SPOTIFY_FETCH_WORKERS", min(4, max(2, CPU_COUNT // 2)))
MUSIC_DOWNLOAD_WORKERS = _env_int_or_auto("MUSIC_DOWNLOAD_WORKERS", min(4, max(2, CPU_COUNT // 2)))
MUSIC_DOWNLOAD_WORKERS_EPHEMERAL = _env_int_or_auto(
    "MUSIC_DOWNLOAD_WORKERS_EPHEMERAL",
    min(2, MUSIC_DOWNLOAD_WORKERS),
)
MAX_WORKERS = YOUTUBE_RESOLVE_WORKERS
YT_SEARCH_LIMIT = 15
MIN_CONFIDENCE_SCORE = 80

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)

def get_logger(name):
    return logging.getLogger(name)
