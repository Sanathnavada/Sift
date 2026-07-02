"""
Session-scoped music download tray helpers.

The task system stores artifacts per task, which is correct internally. The UI,
however, needs a user-session view of all completed music downloads so that a
later candidate-review download does not hide artifacts from an earlier playlist
pass. This module derives that tray from the existing in-memory task/artifact
store without changing the downloader output model.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional
from urllib.parse import quote

from .tasks import Task, all_tasks


MUSIC_DOWNLOAD_SERVICES = {
    "music.song",
    "music.yt",
    "music.link",
    "music.user_download",
    "music.candidate_download",
}

AUDIO_SUFFIXES = {
    ".aac",
    ".aiff",
    ".alac",
    ".flac",
    ".m4a",
    ".mp3",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".weba",
    ".webm",
}

AUDIO_CONTENT_TYPES = {
    "audio/aac",
    "audio/aiff",
    "audio/flac",
    "audio/m4a",
    "audio/mpeg",
    "audio/mp3",
    "audio/ogg",
    "audio/opus",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "audio/x-wav",
}


def _parse_time(value: Optional[str]) -> datetime:
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min


def _artifact_suffix(artifact: dict[str, Any]) -> str:
    name = str(artifact.get("name") or artifact.get("artifact_id") or "")
    dot = name.rfind(".")
    return name[dot:].lower() if dot >= 0 else ""


def _is_music_artifact(artifact: dict[str, Any]) -> bool:
    content_type = str(artifact.get("content_type") or "").lower()
    if content_type in AUDIO_CONTENT_TYPES or content_type.startswith("audio/"):
        return True
    return _artifact_suffix(artifact) in AUDIO_SUFFIXES


def _session_task_matches(task: Task, session_id: Optional[str], client_id: Optional[str] = None) -> bool:
    if task.service not in MUSIC_DOWNLOAD_SERVICES:
        return False
    if task.status != "completed":
        return False
    if not task.artifacts:
        return False
    if session_id and task.meta.get("music_session_id") == session_id:
        return True
    if client_id and task.meta.get("music_client_id") == client_id:
        return True
    return False


def _download_item(task: Task, artifact: dict[str, Any]) -> dict[str, Any]:
    artifact_id = str(artifact.get("artifact_id") or "")
    return {
        "task_id": task.id,
        "task_label": task.meta.get("workflow_label") or task.service.replace(".", " ").title(),
        "task_detail": task.meta.get("item_detail") or "Music download",
        "submitted_at": task.submitted_at,
        "finished_at": task.finished_at,
        "artifact_id": artifact_id,
        "name": artifact.get("name") or artifact_id,
        "content_type": artifact.get("content_type") or "application/octet-stream",
        "size_bytes": artifact.get("size_bytes") or 0,
        "download_url": artifact.get("download_url") or f"/api/tasks/{task.id}/artifacts/{quote(artifact_id, safe='/')}",
    }


def collect_music_downloads(session_id: Optional[str], *, client_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    """Return completed audio artifacts for the current browser music session."""
    items: list[dict[str, Any]] = []
    for task in all_tasks():
        if not _session_task_matches(task, session_id, client_id):
            continue
        for artifact in task.artifacts:
            if _is_music_artifact(artifact):
                items.append(_download_item(task, artifact))

    # Keep the latest copy of an identical file visible once. This avoids the
    # common review-flow duplicate where the same candidate is submitted twice,
    # while still allowing different files from different tasks to appear.
    deduped: dict[tuple[str, int], dict[str, Any]] = {}
    for item in sorted(items, key=lambda entry: (_parse_time(entry.get("finished_at")), entry.get("name") or "")):
        key = (str(item.get("name") or ""), int(item.get("size_bytes") or 0))
        deduped[key] = item

    return sorted(
        deduped.values(),
        key=lambda entry: (_parse_time(entry.get("finished_at")), entry.get("name") or ""),
        reverse=True,
    )[:limit]


def music_download_count(downloads: Iterable[dict[str, Any]]) -> int:
    return sum(1 for _ in downloads)
