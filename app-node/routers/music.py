"""
Music pipeline  —  /api/music/*

Imports service classes directly from music-node.
Modes: song | yt | link | user (all modes now supported)
"""
import sys
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from settings import MUSIC_NODE_DIR
from tasks import new_task, run_in_thread

# Add music-node to path so its internal imports resolve correctly
sys.path.insert(0, str(MUSIC_NODE_DIR))
from models import Track                          # noqa: E402
from services.spotify import SpotifyProvider      # noqa: E402
from services.youtube import YouTubeResolver      # noqa: E402
from services.downloader import MusicDownloader   # noqa: E402

router = APIRouter(prefix="/music", tags=["Music"])


def _is_youtube_url(text: str) -> bool:
    return text.startswith("http") and ("youtube.com" in text or "youtu.be" in text)


def _dispatch(bg: BackgroundTasks, service: str, fn, *args) -> dict:
    task = new_task(service)
    bg.add_task(run_in_thread, task, fn, *args)
    return {"task_id": task.id, "status": task.status}


# ── Request schemas ────────────────────────────────────────────────────────────

class SongRequest(BaseModel):
    query: str

class YtRequest(BaseModel):
    input: str
    outdir: Optional[str] = None

class LinkRequest(BaseModel):
    url: str

class UserDownloadRequest(BaseModel):
    playlists: List[str]  # playlist names, or ["all"] to download everything


# ── Job functions (sync, run in thread pool) ───────────────────────────────────

def _song_job(query: str):
    track = Track(title=query, is_dummy=True)
    url, err = YouTubeResolver()._search_single_with_retry(track)
    if not url:
        raise RuntimeError(f"Could not resolve song: {err}")
    MusicDownloader().download_all({"Single Downloads": [url]})


def _yt_job(input_str: str, outdir: Optional[str]):
    resolver = YouTubeResolver()
    if _is_youtube_url(input_str):
        url = input_str
    else:
        track = Track(title=input_str, is_dummy=True)
        url, err = resolver._search_single_with_retry(track)
        if not url:
            raise RuntimeError(f"Could not find a match: {err}")
    outdir_path = Path(outdir) if outdir else None
    MusicDownloader(ephemeral=True, outdir=outdir_path).download_all({"YouTube Downloads": [url]})


def _link_job(spotify_url: str):
    sp = SpotifyProvider(use_user_auth=False)
    playlists = sp.fetch_by_url(spotify_url)
    resolution_data = YouTubeResolver().resolve_all(playlists)
    for name, data in resolution_data.items():
        urls = data.get("urls", [])
        if urls:
            MusicDownloader().download_all({name: urls})


def _user_library_job() -> dict:
    """Fetch user's Spotify library and return playlist metadata (no download)."""
    library = SpotifyProvider(use_user_auth=True).fetch_user_library()
    return {
        "playlists": [
            {"name": name, "track_count": len(tracks)}
            for name, tracks in library.items()
        ]
    }


def _user_download_job(selected: List[str]):
    """Download selected playlists from the user's Spotify library."""
    library = SpotifyProvider(use_user_auth=True).fetch_user_library()

    if selected == ["all"]:
        playlists = library
    else:
        playlists = {name: tracks for name, tracks in library.items() if name in selected}
        missing = set(selected) - set(playlists)
        if missing:
            raise RuntimeError(f"Playlists not found in your library: {missing}")

    resolver = YouTubeResolver()
    resolution_data = resolver.resolve_all(playlists)
    for name, data in resolution_data.items():
        urls = data.get("urls", [])
        if urls:
            MusicDownloader().download_all({name: urls})


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/song")
async def download_song(req: SongRequest, bg: BackgroundTasks):
    return _dispatch(bg, "music.song", _song_job, req.query)


@router.post("/yt")
async def download_yt(req: YtRequest, bg: BackgroundTasks):
    return _dispatch(bg, "music.yt", _yt_job, req.input, req.outdir)


@router.post("/link")
async def download_link(req: LinkRequest, bg: BackgroundTasks):
    return _dispatch(bg, "music.link", _link_job, req.url)


@router.get("/user/playlists")
async def get_user_playlists(bg: BackgroundTasks):
    """
    Fetch your Spotify library and return all playlist names + track counts.
    On first run, check server logs for the Spotify OAuth URL to approve access.
    """
    task = new_task("music.user_playlists")
    bg.add_task(run_in_thread, task, _user_library_job)
    return {"task_id": task.id, "status": task.status}


@router.post("/user/download")
async def download_user_playlists(req: UserDownloadRequest, bg: BackgroundTasks):
    """
    Download playlists from your Spotify library.
    Pass specific playlist names or ["all"] to download everything.
    """
    return _dispatch(bg, "music.user_download", _user_download_job, req.playlists)
