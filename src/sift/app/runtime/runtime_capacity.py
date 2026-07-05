import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[4]


def _load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _load_dotenv() -> None:
    _load_dotenv_file(ROOT_DIR / ".env")
    _load_dotenv_file(ROOT_DIR / ".env.docker")


_load_dotenv()


def _env_int_or_auto(name: str, default: str = "auto") -> str:
    value = (os.getenv(name) or default).strip().lower()
    if value == "auto":
        return value
    try:
        return str(max(int(value), 1))
    except ValueError:
        return default


def _cpu_count() -> int:
    return max(os.cpu_count() or 1, 1)


def _total_ram_gb() -> float | None:
    try:
        import psutil

        return psutil.virtual_memory().total / 1024**3
    except Exception:
        return None


def _auto_jobs(cpu_count: int, ram_gb: float | None) -> int:
    if cpu_count < 4 or (ram_gb is not None and ram_gb < 8):
        return 1
    if cpu_count < 8 or (ram_gb is not None and ram_gb < 16):
        return 2
    return min(4, cpu_count // 4)


def _auto_music_download_workers(cpu_count: int) -> int:
    return min(4, max(2, cpu_count // 2))


def _auto_youtube_workers(cpu_count: int) -> int:
    return min(8, max(2, cpu_count))


def _auto_spotify_workers(cpu_count: int) -> int:
    return min(4, max(2, cpu_count // 2))


def resolve_worker_count(name: str, auto_value: int) -> int:
    value = _env_int_or_auto(name)
    if value == "auto":
        return max(auto_value, 1)
    return int(value)


CPU_COUNT = _cpu_count()
TOTAL_RAM_GB = _total_ram_gb()

# Legacy global cap retained for backwards compatibility and for callers that
# still read this value. The task scheduler now uses lane-specific limits below.
MAX_CONCURRENT_JOBS = resolve_worker_count(
    "MAX_CONCURRENT_JOBS",
    _auto_jobs(CPU_COUNT, TOTAL_RAM_GB),
)

# Lane limits keep lightweight/interactive work from blocking the heavy ML lane.
# Defaults are deliberately conservative for one-machine and HF/free-CPU style
# deployments. Raise MUSIC_CONCURRENCY locally only after validating CPU/network.
MEDIA_HEAVY_CONCURRENCY = resolve_worker_count("MEDIA_HEAVY_CONCURRENCY", 1)
AUTH_CONCURRENCY = resolve_worker_count("AUTH_CONCURRENCY", 1)
MUSIC_CONCURRENCY = resolve_worker_count("MUSIC_CONCURRENCY", 1)
AGENT_CONCURRENCY = resolve_worker_count("AGENT_CONCURRENCY", 1)

TASK_LANE_CONCURRENCY = {
    "heavy_media": MEDIA_HEAVY_CONCURRENCY,
    "auth": AUTH_CONCURRENCY,
    "music": MUSIC_CONCURRENCY,
    "agent": AGENT_CONCURRENCY,
}

MUSIC_DOWNLOAD_WORKERS = resolve_worker_count(
    "MUSIC_DOWNLOAD_WORKERS",
    _auto_music_download_workers(CPU_COUNT),
)
MUSIC_DOWNLOAD_WORKERS_EPHEMERAL = resolve_worker_count(
    "MUSIC_DOWNLOAD_WORKERS_EPHEMERAL",
    min(2, MUSIC_DOWNLOAD_WORKERS),
)
YOUTUBE_RESOLVE_WORKERS = resolve_worker_count(
    "YOUTUBE_RESOLVE_WORKERS",
    _auto_youtube_workers(CPU_COUNT),
)
SPOTIFY_FETCH_WORKERS = resolve_worker_count(
    "SPOTIFY_FETCH_WORKERS",
    _auto_spotify_workers(CPU_COUNT),
)
