"""
Media scraper API routes.
"""
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...runtime.artifacts import (
    create_job_output_dir,
    get_artifact_expiry,
    register_directory_artifacts,
    register_directory_artifacts_with_optional_bundle,
)
from ...runtime.input_resolver import InputResolutionError, resolve_multi_input
from ...runtime.instagram_sessions import InstagramSessionStore, SESSION_HEADER
from ...settings import (
    INSTAGRAM_AUTH_BROWSER_ENABLED,
    INSTAGRAM_AUTH_TIMEOUT_SECONDS,
    ROOT_DIR,
    SCRAPING_PATH,
)
from ...runtime.tasks import append_task_event, submit_bound_job


router = APIRouter(prefix="/media", tags=["Media"])

SESSION_ROOT = ROOT_DIR / "var" / "sessions" / "instagram"
instagram_sessions = InstagramSessionStore(SESSION_ROOT)
USER_ARTIFACT_DIR = "_user_downloads"
MEDIA_DOWNLOAD_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".m4v"}


def _content_type(item: dict) -> str:
    from sift.engines.media.utils import get_content_type

    return get_content_type(item)


def _web_collection_fetcher(*args, **kwargs):
    from sift.engines.media.instagram.web_fetcher import WebCollectionFetcher

    return WebCollectionFetcher(*args, **kwargs)


def _ytdlp_insta_fetcher(*args, **kwargs):
    from sift.engines.media.instagram.ytdlp_fetcher import YtdlpInstaFetcher

    return YtdlpInstaFetcher(*args, **kwargs)


def _scraper_service():
    from sift.engines.media.service import ScraperService

    return ScraperService


def _youtube_service():
    from sift.engines.media.service import YoutubeService

    return YoutubeService


def _llm_cleaner():
    from sift.engines.media.llm_service import LLMCleaner

    return LLMCleaner()


def _accepted_response(task) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "task_id": task.id,
            "status": task.status,
            "queue_position": task.queue_position,
            "lane": task.meta.get("lane"),
            "lane_label": task.meta.get("lane_label"),
            "queue_message": task.meta.get("queue_message"),
            "estimated_wait_seconds": task.meta.get("estimated_wait_seconds"),
            "estimated_runtime_seconds": task.meta.get("estimated_runtime_seconds"),
            "estimated_total_seconds": task.meta.get("estimated_total_seconds"),
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


def _post_id_for(path: Path, base_dir: Path) -> str:
    try:
        relative = path.relative_to(base_dir)
    except ValueError:
        return path.stem
    for part in relative.parts:
        if part.startswith("post_") and len(part) > 5:
            return part[5:]
    return path.stem


def _copy_user_media_files(source_dir: Path, target_dir: Path) -> None:
    counters: dict[tuple[str, str], int] = {}
    for source in sorted(source_dir.rglob("*")):
        if not source.is_file():
            continue
        if USER_ARTIFACT_DIR in source.parts:
            continue
        if source.suffix.lower() not in MEDIA_DOWNLOAD_SUFFIXES:
            continue

        post_id = _post_id_for(source, source_dir)
        kind = "video" if source.suffix.lower() in {".mp4", ".mov", ".m4v"} else "photo"
        key = (post_id, kind)
        counters[key] = counters.get(key, 0) + 1
        index = counters[key]
        label = kind if kind == "video" and index == 1 else f"{kind}_{index}"
        shutil.copy2(source, target_dir / f"{post_id}_{label}{source.suffix.lower()}")


def _write_user_transcription(source_dir: Path, target_dir: Path) -> None:
    transcription_path = source_dir / "transcription.txt"
    if transcription_path.exists():
        shutil.copy2(transcription_path, target_dir / "transcription.txt")
        return

    text_files = [
        path for path in sorted(source_dir.glob("*.txt"))
        if path.name != "all_combined_cleaned.txt"
    ]
    if not text_files:
        return

    blocks = []
    for path in text_files:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            blocks.append(text)
    if blocks:
        (target_dir / "transcription.txt").write_text("\n\n".join(blocks), encoding="utf-8")


def _register_user_media_artifacts(task_id: str, job_outdir: Path) -> list[dict]:
    target_dir = job_outdir / USER_ARTIFACT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    _write_user_transcription(job_outdir, target_dir)
    _copy_user_media_files(job_outdir, target_dir)
    return register_directory_artifacts_with_optional_bundle(
        task_id,
        target_dir,
        include_suffixes=MEDIA_DOWNLOAD_SUFFIXES | {".txt"},
        bundle_if_count_gt=10,
        bundle_name=f"media-output-{task_id}.zip",
        replace_with_bundle=True,
    )


def _summarize_media_items(items: list[dict]) -> dict[str, int]:
    counts = {"visual": 0, "audio": 0, "other": 0}
    for item in items:
        ctype = _content_type(item)
        if ctype in {"image", "carousel"}:
            counts["visual"] += 1
        elif ctype in {"video", "reel"}:
            counts["audio"] += 1
        else:
            counts["other"] += 1
    return counts


def _append_media_item_summary(task_id: str, items: list[dict]) -> dict[str, int]:
    counts = _summarize_media_items(items)
    append_task_event(
        task_id,
        (
            f"Fetched {len(items)} post(s): "
            f"{counts['visual']} visual, {counts['audio']} reel/video, {counts['other']} other"
        ),
    )
    return counts


def _session_dir(session_id: str | None) -> Path:
    try:
        return instagram_sessions.resolve(session_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _instagram_session_file(session_dir: Path) -> Path:
    return session_dir / "web_session.json"


def _instagram_auth_cancel_file(session_dir: Path, task_id: str | None = None) -> Path:
    if task_id:
        safe_task_id = "".join(ch for ch in task_id if ch.isalnum() or ch in {"-", "_"})
        return session_dir / f".instagram_auth_cancel_{safe_task_id}"
    return session_dir / ".instagram_auth_cancel"


def _request_instagram_auth_cancel(session_dir: Path, task_id: str | None = None) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    try:
        _instagram_auth_cancel_file(session_dir, task_id).write_text("cancel", encoding="utf-8")
    except OSError:
        pass


def _clear_instagram_auth_cancel(session_dir: Path, task_id: str | None = None) -> None:
    paths = [_instagram_auth_cancel_file(session_dir, task_id)] if task_id is not None else list(session_dir.glob(".instagram_auth_cancel*"))
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _instagram_auth_cancel_requested(session_dir: Path, task_id: str | None = None) -> bool:
    return _instagram_auth_cancel_file(session_dir).exists() or _instagram_auth_cancel_file(session_dir, task_id).exists()

def _instagram_auth_status(session_dir: Path) -> dict:
    session_file = _instagram_session_file(session_dir)
    status_payload = {
        "authenticated": False,
        "session_file_exists": session_file.exists(),
        "username": None,
    }
    if not session_file.exists():
        return status_payload

    try:
        import json

        data = json.loads(session_file.read_text(encoding="utf-8"))
    except Exception:
        return status_payload

    has_required_cookies = bool(data.get("sessionid") and data.get("csrftoken"))
    status_payload.update(
        {
            "authenticated": has_required_cookies,
            "username": data.get("_ig_username"),
        }
    )
    return status_payload


def _is_instagram_authenticated(session_dir: Path) -> bool:
    return bool(_instagram_auth_status(session_dir)["authenticated"])


def _require_instagram_auth_for_job(session_dir: Path) -> None:
    if not _is_instagram_authenticated(session_dir):
        raise RuntimeError(
            "Instagram login is required. Connect Instagram before running this workflow."
        )


def _reset_instagram_auth(session_dir: Path) -> dict:
    session_file = _instagram_session_file(session_dir)
    if session_file.exists():
        session_file.unlink()
    return _instagram_auth_status(session_dir)


def _instagram_auth_job(task_id: str, session_dir: Path) -> dict:
    if not INSTAGRAM_AUTH_BROWSER_ENABLED:
        raise RuntimeError("Instagram browser authentication is disabled for this deployment.")

    _clear_instagram_auth_cancel(session_dir, task_id)
    append_task_event(task_id, "Opening Instagram login browser. Complete login in the popup console.")
    fetcher = _web_collection_fetcher(
        outdir=str(session_dir),
        session_dir=str(session_dir),
        username=None,
        password=None,
    )
    result = fetcher.acquire_interactive_login(
        timeout_seconds=INSTAGRAM_AUTH_TIMEOUT_SECONDS,
        should_cancel=lambda: _instagram_auth_cancel_requested(session_dir, task_id),
    )
    append_task_event(task_id, "Instagram session saved successfully.")
    status_payload = _instagram_auth_status(session_dir)
    return _build_result(
        task_id=task_id,
        message="Connected Instagram browser session",
        input_payload={"workflow": "instagram_auth"},
        output_payload={
            "username": result.get("username") or status_payload.get("username"),
            "session_file_exists": status_payload["session_file_exists"],
        },
        stats={"authenticated": status_payload["authenticated"]},
        artifacts=[],
    )


def _make_public_insta_fetcher(session_dir: Path):
    return _ytdlp_insta_fetcher(scraping_path=SCRAPING_PATH, session_dir=str(session_dir))


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
        _youtube_service().process_video(
            input_value,
            str(job_outdir),
            progress=lambda message: append_task_event(task_id, message),
        )
        items.append({
            "input": input_value,
            "status": "completed",
        })

    artifacts = _register_user_media_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed YouTube media request",
        input_payload={"inputs": inputs},
        output_payload={"output_mode": output_mode},
        stats={"submitted_count": len(inputs), "completed_count": len(items)},
        artifacts=artifacts,
        items=items,
    )


def _public_user_job(task_id: str, username: str, first_n: int, outdir: Optional[str],
                     session_dir: Path) -> dict:
    _require_instagram_auth_for_job(session_dir)
    job_outdir, output_mode = create_job_output_dir(task_id, "media.public_user", outdir is None, outdir)
    web = _web_collection_fetcher(
        outdir=str(job_outdir),
        session_dir=str(session_dir),
        username=None,
        password=None,
    )
    items = web.fetch_public_profile(username, first_n=first_n)
    if not items:
        raise RuntimeError(
            f"No media items were fetched from @{username}. Login may be required or Instagram returned an empty page."
        )
    media_counts = _append_media_item_summary(task_id, items)
    checkpoint = os.path.join(job_outdir, "checkpoint.json")
    _scraper_service().process_posts(
        items,
        str(job_outdir),
        checkpoint_path=checkpoint,
        combined_file_path=str(job_outdir / "transcription.txt"),
        clean_output=False,
        progress=lambda message: append_task_event(task_id, message),
    )
    artifacts = _register_user_media_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed public user scrape",
        input_payload={"username": username, "first_n": first_n},
        output_payload={"output_mode": output_mode},
        stats={"fetched_item_count": len(items), **media_counts},
        artifacts=artifacts,
    )


def _post_job(task_id: str, url: str, outdir: Optional[str], session_dir: Path) -> dict:
    job_outdir, output_mode = create_job_output_dir(task_id, "media.post", outdir is None, outdir)
    fetcher = _make_public_insta_fetcher(session_dir)
    items = fetcher.fetch_single_post(url)
    if not items:
        raise RuntimeError(f"Could not fetch post: {url}")
    media_counts = _append_media_item_summary(task_id, items)
    checkpoint = os.path.join(job_outdir, "checkpoint.json")
    _scraper_service().process_posts(
        items,
        str(job_outdir),
        checkpoint_path=checkpoint,
        combined_file_path=str(job_outdir / "transcription.txt"),
        clean_output=False,
        progress=lambda message: append_task_event(task_id, message),
    )
    artifacts = _register_user_media_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed single post scrape",
        input_payload={"url": url},
        output_payload={"output_mode": output_mode},
        stats={"fetched_item_count": len(items), **media_counts},
        artifacts=artifacts,
    )


def _ig_bulk_job(task_id: str, urls: list[str], outdir: Optional[str],
                 session_dir: Path) -> dict:
    job_outdir, output_mode = create_job_output_dir(task_id, "media.ig_bulk", outdir is None, outdir)
    fetcher = _make_public_insta_fetcher(session_dir)
    items = fetcher.fetch_bulk(urls)
    if not items:
        raise RuntimeError("No media items fetched from the provided URLs.")
    media_counts = _append_media_item_summary(task_id, items)
    checkpoint = os.path.join(job_outdir, "checkpoint.json")
    _scraper_service().process_posts(
        items,
        str(job_outdir),
        checkpoint_path=checkpoint,
        combined_file_path=str(job_outdir / "transcription.txt"),
        clean_output=False,
        progress=lambda message: append_task_event(task_id, message),
    )
    artifacts = _register_user_media_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed bulk Instagram scrape",
        input_payload={"urls": urls},
        output_payload={"output_mode": output_mode},
        stats={
            "submitted_url_count": len(urls),
            "fetched_item_count": len(items),
            **media_counts,
        },
        artifacts=artifacts,
    )


def _private_user_job(task_id: str, collection: str, username: Optional[str],
                      password: Optional[str], outdir: Optional[str],
                      session_dir: Path) -> dict:
    _require_instagram_auth_for_job(session_dir)
    job_outdir, output_mode = create_job_output_dir(task_id, "media.private_user", outdir is None, outdir)
    web = _web_collection_fetcher(
        outdir=str(job_outdir),
        session_dir=str(session_dir),
        username=username,
        password=password,
    )
    items = web.fetch_collection(collection)
    if not items:
        raise RuntimeError(
            f"No media items were fetched from collection '{collection}'. Check the collection name and session."
        )
    media_counts = _append_media_item_summary(task_id, items)
    checkpoint = os.path.join(job_outdir, "checkpoint.json")
    _scraper_service().process_posts(
        items,
        str(job_outdir),
        checkpoint_path=checkpoint,
        combined_file_path=str(job_outdir / "transcription.txt"),
        clean_output=False,
        progress=lambda message: append_task_event(task_id, message),
    )
    artifacts = _register_user_media_artifacts(task_id, job_outdir)
    return _build_result(
        task_id=task_id,
        message="Completed private collection scrape",
        input_payload={"collection": collection},
        output_payload={"output_mode": output_mode},
        stats={"fetched_item_count": len(items), **media_counts},
        artifacts=artifacts,
    )


def _clean_bulk_job(task_id: str, file_path: str) -> dict:
    base, ext = os.path.splitext(file_path)
    output = f"{base}_cleaned{ext}"
    _llm_cleaner().clean_bulk_file(file_path, output)
    artifacts = register_directory_artifacts(task_id, Path(output).parent, include_suffixes=[Path(output).suffix])
    return _build_result(
        task_id=task_id,
        message="Completed bulk file cleaning",
        input_payload={"file_path": file_path},
        output_payload={"cleaned_file_path": output, "output_mode": "persistent"},
        stats={"submitted_file_count": 1},
        artifacts=[artifact for artifact in artifacts if artifact["name"] == Path(output).name],
    )


@router.get("/instagram/auth/status")
async def instagram_auth_status(request: Request):
    session_dir = _session_dir(request.headers.get(SESSION_HEADER))
    return _instagram_auth_status(session_dir)


@router.post("/instagram/auth/start")
async def instagram_auth_start(request: Request):
    session_dir = _session_dir(request.headers.get(SESSION_HEADER))
    task = await submit_bound_job(
        "media.instagram_auth",
        lambda task: _instagram_auth_job(task.id, session_dir),
        submitted_by=request.client.host if request.client else None,
        meta={
            "workflow_label": "Instagram login",
            "item_label": "Auth browser",
            "item_detail": "Complete Instagram login in the popup console",
        },
    )
    return _accepted_response(task)


@router.delete("/instagram/auth/session")
async def instagram_auth_reset(request: Request):
    session_dir = _session_dir(request.headers.get(SESSION_HEADER))
    return _reset_instagram_auth(session_dir)


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
        meta={
            "workflow_label": "YouTube transcription",
            "item_label": "YouTube input",
            "item_detail": f"{inputs[0]} + {len(inputs) - 1} more" if len(inputs) > 1 else inputs[0],
            "submitted_count": len(inputs),
        },
    )
    return _accepted_response(task)


@router.post("/public-user")
async def scrape_public_user(req: PublicUserRequest, request: Request):
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "Instagram username is required.")
    if req.first_n < 1:
        raise HTTPException(400, "first_n must be at least 1.")

    session_dir = _session_dir(request.headers.get(SESSION_HEADER))
    if not _is_instagram_authenticated(session_dir):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Connect Instagram before running public profile scraping.",
        )
    task = await submit_bound_job(
        "media.public_user",
        lambda task: _public_user_job(task.id, username, req.first_n, req.outdir, session_dir),
        submitted_by=request.client.host if request.client else None,
        meta={
            "workflow_label": "Public profile scrape",
            "item_label": "Profile",
            "item_detail": f"@{username} - first {req.first_n} posts",
            "submitted_count": req.first_n,
        },
    )
    return _accepted_response(task)


@router.post("/post")
async def scrape_post(req: PostRequest, request: Request):
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "Instagram post URL is required.")

    session_dir = _session_dir(request.headers.get(SESSION_HEADER))
    task = await submit_bound_job(
        "media.post",
        lambda task: _post_job(task.id, url, req.outdir, session_dir),
        submitted_by=request.client.host if request.client else None,
        meta={
            "workflow_label": "Instagram post",
            "item_label": "Post URL",
            "item_detail": url,
            "submitted_count": 1,
        },
    )
    return _accepted_response(task)


@router.post("/ig-bulk")
async def scrape_ig_bulk(req: IgBulkRequest, request: Request):
    try:
        urls = _normalize_bulk_urls(req)
    except InputResolutionError as exc:
        raise HTTPException(400, str(exc)) from exc

    session_dir = _session_dir(request.headers.get(SESSION_HEADER))
    task = await submit_bound_job(
        "media.ig_bulk",
        lambda task: _ig_bulk_job(task.id, urls, req.outdir, session_dir),
        submitted_by=request.client.host if request.client else None,
        meta={
            "workflow_label": "Bulk Instagram scrape",
            "item_label": "URLs",
            "item_detail": f"{urls[0]} + {len(urls) - 1} more" if len(urls) > 1 else urls[0],
            "submitted_count": len(urls),
        },
    )
    return _accepted_response(task)


@router.post("/private-user")
async def scrape_private_user(req: PrivateUserRequest, request: Request):
    collection = req.collection.strip()
    if not collection:
        raise HTTPException(400, "Collection name is required.")

    session_dir = _session_dir(request.headers.get(SESSION_HEADER))
    if not _is_instagram_authenticated(session_dir):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Connect Instagram before running private collection scraping.",
        )
    task = await submit_bound_job(
        "media.private_user",
        lambda task: _private_user_job(
            task.id, collection, req.username, req.password, req.outdir, session_dir
        ),
        submitted_by=request.client.host if request.client else None,
        meta={
            "workflow_label": "Private collection scrape",
            "item_label": "Collection",
            "item_detail": collection,
            "submitted_count": 1,
        },
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
        meta={
            "workflow_label": "Bulk clean",
            "item_label": "File",
            "item_detail": req.file_path,
            "submitted_count": 1,
        },
    )
    return _accepted_response(task)
