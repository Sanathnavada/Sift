"""
Media scraper  —  /api/media/*

Imports service classes directly from media-node.

Naming conflict fix:
  music-node has  services/  (package directory)
  media-node has  services.py (module file)
  Both named "services" — whichever loads first wins and breaks the other.
  Solution: load media-node's services.py via importlib with a unique name
  "media_services", bypassing sys.modules collision entirely.
"""
import importlib.util
import os
import sys
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from settings import MEDIA_NODE_DIR, DEFAULT_MEDIA_OUTDIR
from tasks import new_task, run_in_thread

# Add media-node to path so transitive imports (utils, processor, etc.) resolve
sys.path.insert(0, str(MEDIA_NODE_DIR))

# Load media-node/services.py under a unique module name to avoid the
# "services" name collision with music-node's services/ package.
_spec = importlib.util.spec_from_file_location("media_services", str(MEDIA_NODE_DIR / "services.py"))
_media_services = importlib.util.module_from_spec(_spec)
sys.modules["media_services"] = _media_services
_spec.loader.exec_module(_media_services)

ScraperService = _media_services.ScraperService
YoutubeService = _media_services.YoutubeService

from insta.web_fetcher import WebCollectionFetcher    # noqa: E402
from insta.ytdlp_fetcher import YtdlpInstaFetcher    # noqa: E402
from llm_service import LLMCleaner                   # noqa: E402

router = APIRouter(prefix="/media", tags=["Media"])


def _dispatch(bg: BackgroundTasks, service: str, fn, *args) -> dict:
    task = new_task(service)
    bg.add_task(run_in_thread, task, fn, *args)
    return {"task_id": task.id, "status": task.status}


# ── Request schemas ────────────────────────────────────────────────────────────

class YoutubeRequest(BaseModel):
    input: str
    outdir: Optional[str] = None

class PublicUserRequest(BaseModel):
    username: str
    first_n: int = 10
    outdir: Optional[str] = None

class PostRequest(BaseModel):
    url: str
    outdir: Optional[str] = None

class IgBulkRequest(BaseModel):
    urls: List[str]
    outdir: Optional[str] = None

class PrivateUserRequest(BaseModel):
    collection: str
    username: Optional[str] = None
    password: Optional[str] = None
    outdir: Optional[str] = None

class CleanBulkRequest(BaseModel):
    file_path: str


# ── Job functions (sync, run in thread pool) ───────────────────────────────────

def _youtube_job(url: str, outdir: str):
    YoutubeService.process_video(url, outdir)


def _public_user_job(username: str, first_n: int, outdir: str):
    fetcher = YtdlpInstaFetcher()
    items = fetcher.fetch_user_posts(username, first_n=first_n)
    checkpoint = os.path.join(outdir, "checkpoint.json")
    ScraperService.process_posts(items, outdir, checkpoint_path=checkpoint)


def _post_job(url: str, outdir: str):
    fetcher = YtdlpInstaFetcher()
    items = fetcher.fetch_single_post(url)
    if not items:
        raise RuntimeError(f"Could not fetch post: {url}")
    checkpoint = os.path.join(outdir, "checkpoint.json")
    ScraperService.process_posts(items, outdir, checkpoint_path=checkpoint)


def _ig_bulk_job(urls: List[str], outdir: str):
    fetcher = YtdlpInstaFetcher()
    items = fetcher.fetch_bulk(urls)
    if not items:
        raise RuntimeError("No media items fetched from the provided URLs.")
    checkpoint = os.path.join(outdir, "checkpoint.json")
    ScraperService.process_posts(items, outdir, checkpoint_path=checkpoint)


def _private_user_job(collection: str, outdir: str, session_dir: str,
                      username: Optional[str], password: Optional[str]):
    web = WebCollectionFetcher(outdir=outdir, session_dir=session_dir,
                               username=username, password=password)
    items = web.fetch_collection(collection)
    checkpoint = os.path.join(outdir, "checkpoint.json")
    ScraperService.process_posts(items, outdir, checkpoint_path=checkpoint)


def _clean_bulk_job(file_path: str):
    base, ext = os.path.splitext(file_path)
    output = f"{base}_cleaned{ext}"
    LLMCleaner().clean_bulk_file(file_path, output)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/youtube")
async def process_youtube(req: YoutubeRequest, bg: BackgroundTasks):
    outdir = req.outdir or os.path.join(DEFAULT_MEDIA_OUTDIR, "youtube")
    return _dispatch(bg, "media.youtube", _youtube_job, req.input, outdir)


@router.post("/public-user")
async def scrape_public_user(req: PublicUserRequest, bg: BackgroundTasks):
    outdir = req.outdir or os.path.join(DEFAULT_MEDIA_OUTDIR, "instagram", "public", req.username)
    return _dispatch(bg, "media.public_user", _public_user_job, req.username, req.first_n, outdir)


@router.post("/post")
async def scrape_post(req: PostRequest, bg: BackgroundTasks):
    outdir = req.outdir or os.path.join(DEFAULT_MEDIA_OUTDIR, "instagram", "posts")
    return _dispatch(bg, "media.post", _post_job, req.url, outdir)


@router.post("/ig-bulk")
async def scrape_ig_bulk(req: IgBulkRequest, bg: BackgroundTasks):
    outdir = req.outdir or os.path.join(DEFAULT_MEDIA_OUTDIR, "instagram", "bulk")
    return _dispatch(bg, "media.ig_bulk", _ig_bulk_job, req.urls, outdir)


@router.post("/private-user")
async def scrape_private_user(req: PrivateUserRequest, bg: BackgroundTasks):
    outdir = req.outdir or os.path.join(DEFAULT_MEDIA_OUTDIR, "instagram", "collections", req.collection)
    session_dir = req.outdir or os.path.join(DEFAULT_MEDIA_OUTDIR, "instagram", "collections")
    return _dispatch(bg, "media.private_user", _private_user_job,
                     req.collection, outdir, session_dir, req.username, req.password)


@router.post("/clean-bulk")
async def clean_bulk(req: CleanBulkRequest, bg: BackgroundTasks):
    if not os.path.isfile(req.file_path):
        raise HTTPException(400, f"File not found: {req.file_path}")
    return _dispatch(bg, "media.clean_bulk", _clean_bulk_job, req.file_path)
