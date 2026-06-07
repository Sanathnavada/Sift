"""
Media scraper API routes.
"""
import importlib.util
import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..artifacts import create_job_output_dir, get_artifact_expiry, register_directory_artifacts
from ..input_resolver import InputResolutionError, resolve_multi_input
from ..settings import MEDIA_NODE_DIR, ROOT_DIR
from ..tasks import append_task_event, submit_bound_job

sys.path.insert(0, str(MEDIA_NODE_DIR))

_spec = importlib.util.spec_from_file_location("media_services", str(MEDIA_NODE_DIR / "services.py"))
_media_services = importlib.util.module_from_spec(_spec)
sys.modules["media_services"] = _media_services
_spec.loader.exec_module(_media_services)

ScraperService = _media_services.ScraperService
YoutubeService = _media_services.YoutubeService

from media_node.insta.web_fetcher import WebCollectionFetcher  # noqa: E402
from media_node.insta.ytdlp_fetcher import YtdlpInstaFetcher  # noqa: E402
from media_node.llm_service import LLMCleaner  # noqa: E402

router = APIRouter(prefix="/media", tags=["Media"])

SESSION_ROOT = ROOT_DIR / "data" / "app_node_sessions" / "instagram"


def _accepted_response(task) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "task_id": task.id,
            "status": task.status,
            "queue_position": task.queue_position,
            "poll_url": f"/api/tasks/{task.id}",
        },
    )


def _build_result(*, task_id: str, message: str, input_payload: dict,
                  output_payload: dict, stats: dict,
                  artifacts: list[dict], items: Optional[list[dict]] = None) -> dict:
    result = {
        "message": message,
        "input": input_payload,
        "output": output_payload,
        "artifacts": artifacts,
        "stats": stats,
    }
    if items is not None:
        result["items"] = items
    expires_at = get_artifact_expiry(task_id)
    if expires_at:
        result["expires_at"] = expires_at
    return result


class YoutubeRequest(BaseModel):
    input: Optional[str] = None
    inputs: Optional[list[str]] = None
    input_file: Optional[str] = None
    outdir: Optional[str] = None


class PublicUserRequest(BaseModel):
    username: str
    first_n: int = 10
    outdir: Optional[str] = None


class PostRequest(BaseModel):
    url: str
    outdir: Optional[str] = None


class IgBulkRequest(BaseModel):
    urls: Optional[list[str]] = None
    input_file: Optional[str] = None
    outdir: Optional[str] = None


class PrivateUserRequest(BaseModel):
    collection: str
    username: Optional[str] = None
    password: Optional[str] = None
    outdir: Optional[str] = None


class CleanBulkRequest(BaseModel):
    file_path: str


def _normalize_yt_inputs(req: YoutubeRequest) -> list[str]:
    direct_values = []
    if req.input:
        direct_values.append(req.input)
    if req.inputs:
        direct_values.extend(req.inputs)
    return resolve_multi_input(direct_values=direct_values or None, input_file=req.input_file)


def _normalize_bulk_urls(req: IgBulkRequest) -> list[str]:
    return resolve_multi_input(direct_values=req.urls, input_file=req.input_file)


def _youtube_job(task_id: str, inputs: list[str], outdir: Optional[str]) -> dict:
    job_outdir, output_mode = create_job_output_dir(task_id, "media.youtube", outdir is None, outdir)
    items = []

    for input_value in inputs:
        append_task_event(task_id, f"Queued YouTube input: {input_value}")
        YoutubeService.process_video(
            input_value,
            str(job_outdir),
            progress=lambda message: append_task_event(task_id, message),
        )
        items.append({
            "input": input_value,
            "status": "completed",
        })

    artifacts = register_directory_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed YouTube media request",
        input_payload={"inputs": inputs},
        output_payload={"output_mode": output_mode, "outdir": str(job_outdir)},
        stats={"submitted_count": len(inputs), "completed_count": len(items)},
        artifacts=artifacts,
        items=items,
    )


def _public_user_job(task_id: str, username: str, first_n: int, outdir: Optional[str]) -> dict:
    job_outdir, output_mode = create_job_output_dir(task_id, "media.public_user", outdir is None, outdir)
    fetcher = YtdlpInstaFetcher()
    items = fetcher.fetch_user_posts(username, first_n=first_n)
    checkpoint = os.path.join(job_outdir, "checkpoint.json")
    ScraperService.process_posts(items, str(job_outdir), checkpoint_path=checkpoint)
    artifacts = register_directory_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed public user scrape",
        input_payload={"username": username, "first_n": first_n},
        output_payload={
            "output_mode": output_mode,
            "outdir": str(job_outdir),
            "checkpoint_path": checkpoint,
        },
        stats={"fetched_item_count": len(items)},
        artifacts=artifacts,
    )


def _post_job(task_id: str, url: str, outdir: Optional[str]) -> dict:
    job_outdir, output_mode = create_job_output_dir(task_id, "media.post", outdir is None, outdir)
    fetcher = YtdlpInstaFetcher()
    items = fetcher.fetch_single_post(url)
    if not items:
        raise RuntimeError(f"Could not fetch post: {url}")
    checkpoint = os.path.join(job_outdir, "checkpoint.json")
    ScraperService.process_posts(items, str(job_outdir), checkpoint_path=checkpoint)
    artifacts = register_directory_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed single post scrape",
        input_payload={"url": url},
        output_payload={
            "output_mode": output_mode,
            "outdir": str(job_outdir),
            "checkpoint_path": checkpoint,
        },
        stats={"fetched_item_count": len(items)},
        artifacts=artifacts,
    )


def _ig_bulk_job(task_id: str, urls: list[str], outdir: Optional[str]) -> dict:
    job_outdir, output_mode = create_job_output_dir(task_id, "media.ig_bulk", outdir is None, outdir)
    fetcher = YtdlpInstaFetcher()
    items = fetcher.fetch_bulk(urls)
    if not items:
        raise RuntimeError("No media items fetched from the provided URLs.")
    checkpoint = os.path.join(job_outdir, "checkpoint.json")
    ScraperService.process_posts(items, str(job_outdir), checkpoint_path=checkpoint)
    artifacts = register_directory_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed bulk Instagram scrape",
        input_payload={"urls": urls},
        output_payload={
            "output_mode": output_mode,
            "outdir": str(job_outdir),
            "checkpoint_path": checkpoint,
        },
        stats={
            "submitted_url_count": len(urls),
            "fetched_item_count": len(items),
        },
        artifacts=artifacts,
    )


def _private_user_job(task_id: str, collection: str, username: Optional[str],
                      password: Optional[str], outdir: Optional[str]) -> dict:
    job_outdir, output_mode = create_job_output_dir(task_id, "media.private_user", outdir is None, outdir)
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    web = WebCollectionFetcher(
        outdir=str(job_outdir),
        session_dir=str(SESSION_ROOT),
        username=username,
        password=password,
    )
    items = web.fetch_collection(collection)
    checkpoint = os.path.join(job_outdir, "checkpoint.json")
    ScraperService.process_posts(items, str(job_outdir), checkpoint_path=checkpoint)
    artifacts = register_directory_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed private collection scrape",
        input_payload={
            "collection": collection,
            "username_provided": bool(username),
            "password_provided": bool(password),
        },
        output_payload={
            "output_mode": output_mode,
            "outdir": str(job_outdir),
            "checkpoint_path": checkpoint,
            "session_dir": str(SESSION_ROOT),
        },
        stats={"fetched_item_count": len(items)},
        artifacts=artifacts,
    )


def _clean_bulk_job(task_id: str, file_path: str) -> dict:
    base, ext = os.path.splitext(file_path)
    output = f"{base}_cleaned{ext}"
    LLMCleaner().clean_bulk_file(file_path, output)
    artifacts = register_directory_artifacts(task_id, Path(output).parent, include_suffixes=[Path(output).suffix])
    return _build_result(
        task_id=task_id,
        message="Completed bulk file cleaning",
        input_payload={"file_path": file_path},
        output_payload={"cleaned_file_path": output, "output_mode": "persistent"},
        stats={"submitted_file_count": 1},
        artifacts=[artifact for artifact in artifacts if artifact["name"] == Path(output).name],
    )


@router.post("/youtube")
async def process_youtube(req: YoutubeRequest, request: Request):
    try:
        inputs = _normalize_yt_inputs(req)
    except InputResolutionError as exc:
        raise HTTPException(400, str(exc)) from exc

    task = await submit_bound_job(
        "media.youtube",
        lambda task: _youtube_job(task.id, inputs, req.outdir),
        submitted_by=request.client.host if request.client else None,
    )
    return _accepted_response(task)


@router.post("/public-user")
async def scrape_public_user(req: PublicUserRequest, request: Request):
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "Instagram username is required.")
    if req.first_n < 1:
        raise HTTPException(400, "first_n must be at least 1.")

    task = await submit_bound_job(
        "media.public_user",
        lambda task: _public_user_job(task.id, username, req.first_n, req.outdir),
        submitted_by=request.client.host if request.client else None,
    )
    return _accepted_response(task)


@router.post("/post")
async def scrape_post(req: PostRequest, request: Request):
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "Instagram post URL is required.")

    task = await submit_bound_job(
        "media.post",
        lambda task: _post_job(task.id, url, req.outdir),
        submitted_by=request.client.host if request.client else None,
    )
    return _accepted_response(task)


@router.post("/ig-bulk")
async def scrape_ig_bulk(req: IgBulkRequest, request: Request):
    try:
        urls = _normalize_bulk_urls(req)
    except InputResolutionError as exc:
        raise HTTPException(400, str(exc)) from exc

    task = await submit_bound_job(
        "media.ig_bulk",
        lambda task: _ig_bulk_job(task.id, urls, req.outdir),
        submitted_by=request.client.host if request.client else None,
    )
    return _accepted_response(task)


@router.post("/private-user")
async def scrape_private_user(req: PrivateUserRequest, request: Request):
    collection = req.collection.strip()
    if not collection:
        raise HTTPException(400, "Collection name is required.")

    task = await submit_bound_job(
        "media.private_user",
        lambda task: _private_user_job(task.id, collection, req.username, req.password, req.outdir),
        submitted_by=request.client.host if request.client else None,
    )
    return _accepted_response(task)


@router.post("/clean-bulk")
async def clean_bulk(req: CleanBulkRequest, request: Request):
    if not os.path.isfile(req.file_path):
        raise HTTPException(400, f"File not found: {req.file_path}")

    task = await submit_bound_job(
        "media.clean_bulk",
        lambda task: _clean_bulk_job(task.id, req.file_path),
        submitted_by=request.client.host if request.client else None,
    )
    return _accepted_response(task)
