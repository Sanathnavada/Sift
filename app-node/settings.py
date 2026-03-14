"""
Central configuration — resolves absolute paths to every node service.
All paths are derived from this file's location so the server works
regardless of where it is invoked from.
"""
import sys
from pathlib import Path

# Root of the whole monorepo  (…/code/)
ROOT_DIR = Path(__file__).resolve().parent.parent

# ── Node service working directories ────────────────────────────────────────
INODE_DIR      = ROOT_DIR / "i-node"
MUSIC_NODE_DIR = ROOT_DIR / "music-node"
MEDIA_NODE_DIR = ROOT_DIR / "media-node"
NAVIDROME_DIR  = ROOT_DIR / "music-node" / "server"
NAVIDROME_EXE  = NAVIDROME_DIR / "Navidrome.exe"

# ── Default output directories ───────────────────────────────────────────────
DEFAULT_MUSIC_OUTDIR = str(ROOT_DIR / "downloads" / "music")
DEFAULT_MEDIA_OUTDIR = str(ROOT_DIR / "downloads" / "media")

# ── Python interpreter (same one that's running this server) ─────────────────
PYTHON = sys.executable

# ── API settings ─────────────────────────────────────────────────────────────
API_HOST = "0.0.0.0"
API_PORT = 8000
