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
from pathlib import Path

# config.py lives in: <project_root>/config
BASE_DIR = Path(__file__).parent            
PROJECT_ROOT = BASE_DIR.parent              

PREFERRED_OUTPUT = Path(r"D:\Music")
ROOT_OUTPUT_DIR = PREFERRED_OUTPUT if PREFERRED_OUTPUT.exists() else PROJECT_ROOT / "Music"

CACHE_FILE = BASE_DIR / "youtube_cache.json"
DB_FILE = PROJECT_ROOT / "download_history.db"

COOKIES_FILE = BASE_DIR / "cookies.txt"
SPOTIFY_CACHE_PATH = BASE_DIR / ".cache" 

# [FIX] Ensure this is consistent
SPOTIFY_BACKUP_FILE = BASE_DIR / "spotify_playlists_backup.txt"
# --- THE FIX IS HERE ---
# We removed 'CACHE_DIR' because we don't need a folder.
# We only ensure the Output Directory exists.
if not ROOT_OUTPUT_DIR.exists():
    ROOT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
# --- Tuning ---
MAX_WORKERS = 8
YT_SEARCH_LIMIT = 10
MIN_CONFIDENCE_SCORE = 40

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)

def get_logger(name):
    return logging.getLogger(name)