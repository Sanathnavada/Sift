"""
Central configuration — resolves absolute paths to every node service.
All paths are derived from this file's location so the server works
regardless of where it is invoked from.
"""
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Root of the project checkout. settings.py lives in src/sift/app/.
ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / "src"
PACKAGE_DIR = SRC_DIR / "sift"
APP_DIR = PACKAGE_DIR / "app"
WEB_DIR = APP_DIR / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = (os.getenv(name) or default).strip().lower()
    return value if value in choices else default


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return max(int(value.strip()), minimum)
    except ValueError:
        return default


def _normalize_novnc_url(url: str) -> str:
    """Return the clean embedded noVNC client URL used by the auth popup.

    Existing .env files may still point at the full noVNC console
    (/vnc.html) or noVNC lite client (/vnc_lite.html), both of which can
    show noVNC chrome. For the Instagram auth popup we default to the
    custom clean client while still allowing NOVNC_CLEAN_UI=false to
    preserve the old full-console behavior.
    """
    if not _env_bool("NOVNC_CLEAN_UI", True):
        return url

    parts = urlsplit(url)
    path = parts.path
    for old_page in ("/vnc.html", "/vnc_lite.html"):
        if path.endswith(old_page):
            path = path[: -len(old_page)] + "/vnc_clean.html"
            break

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("autoconnect", "true")
    query.setdefault("resize", "scale")
    query.setdefault("quality", "9")
    query.setdefault("compression", "0")
    query.setdefault("reconnect", "true")

    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), parts.fragment))


# Local development usually uses .env. The final cleanup also keeps a
# sanitized .env.docker template at the project root; loading it as a fallback
# keeps direct local runs and Docker-based runs consistent. Existing process
# environment values still win because _load_dotenv uses os.environ.setdefault.
_load_dotenv(ROOT_DIR / ".env")
_load_dotenv(ROOT_DIR / ".env.docker")

# ── Node service working directories ────────────────────────────────────────
TELEGRAM_ENGINE_DIR = PACKAGE_DIR / "engines" / "telegram"
MUSIC_ENGINE_DIR = PACKAGE_DIR / "engines" / "music"
MEDIA_ENGINE_DIR = PACKAGE_DIR / "engines" / "media"
NAVIDROME_DIR  = PACKAGE_DIR / "integrations" / "navidrome" / "server"
NAVIDROME_EXE  = NAVIDROME_DIR / "Navidrome.exe"

# ── Default output directories ───────────────────────────────────────────────
DEFAULT_MUSIC_OUTDIR = str(ROOT_DIR / "downloads" / "music")
DEFAULT_MEDIA_OUTDIR = str(ROOT_DIR / "downloads" / "media")

# ── Python interpreter (same one that's running this server) ─────────────────
PYTHON = sys.executable

# ── API settings ─────────────────────────────────────────────────────────────
API_HOST = "0.0.0.0"
API_PORT = 8000

TELEGRAM_NODE_ENABLED = _env_bool("TELEGRAM_NODE_ENABLED", True)
MEDIA_NODE_ENABLED = _env_bool("MEDIA_NODE_ENABLED", True)
MUSIC_NODE_ENABLED = _env_bool("MUSIC_NODE_ENABLED", True)
NAVIDROME_ENABLED = _env_bool("NAVIDROME_ENABLED", True)
SCRAPING_PATH = _env_choice("SCRAPING_PATH", "playwright", {"instaloader", "ytdlp", "playwright"})

# ── Instagram interactive browser auth ───────────────────────────────────────
INSTAGRAM_AUTH_BROWSER_ENABLED = _env_bool("INSTAGRAM_AUTH_BROWSER_ENABLED", True)
INSTAGRAM_AUTH_TIMEOUT_SECONDS = _env_int("INSTAGRAM_AUTH_TIMEOUT_SECONDS", 300, minimum=30)

# Local dev usually opens a normal Playwright/Chromium window. Docker/noVNC
# deployments can enable the embedded browser frame from .env/.env.docker.
NOVNC_ENABLED = _env_bool("NOVNC_ENABLED", False)
NOVNC_PORT = _env_int("NOVNC_PORT", 6080, minimum=1)
NOVNC_AUTO_HOST = _env_bool("NOVNC_AUTO_HOST", True)
_DEFAULT_NOVNC_PUBLIC_URL = f"http://localhost:{NOVNC_PORT}/vnc_clean.html?autoconnect=true&resize=scale&quality=9&compression=0&reconnect=true"
NOVNC_CLEAN_UI = _env_bool("NOVNC_CLEAN_UI", True)
NOVNC_PUBLIC_URL = _normalize_novnc_url(os.getenv("NOVNC_PUBLIC_URL", _DEFAULT_NOVNC_PUBLIC_URL))
