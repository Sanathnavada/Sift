"""
View-model helpers for server-rendered UI task cards and summary panels.

These functions intentionally keep presentation formatting out of the route
handlers while preserving the exact shape expected by the existing Jinja
partials.
"""
from __future__ import annotations

from datetime import datetime
import json
from typing import Optional

from ..runtime.tasks import get_queue_summary


def _task_page_url(task_id: str) -> str:
    return f"/tasks/{task_id}"


def _task_card_url(task_id: str, container_id: str) -> str:
    return f"/ui/tasks/{task_id}/card?container_id={container_id}"


def _artifact_list_url(task_id: str) -> str:
    return f"/ui/tasks/{task_id}/artifacts"


def _format_task_time(value: Optional[str], *, include_zone: bool = True) -> str:
    if not value:
        return "Pending"
    try:
        parsed = datetime.fromisoformat(value)
        local_time = parsed.astimezone()
    except ValueError:
        return value

    time_label = local_time.strftime("%I:%M:%S %p").lstrip("0")
    zone_label = local_time.tzname() or "local time"
    return f"{time_label} {zone_label}" if include_zone else time_label


def _format_duration_seconds(value: Optional[int]) -> Optional[str]:
    if not value:
        return None
    minutes, seconds = divmod(max(int(value), 0), 60)
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _task_timezone_label(task) -> str:
    for value in (task.submitted_at, task.started_at, task.finished_at):
        if value:
            try:
                return datetime.fromisoformat(value).astimezone().tzname() or "local time"
            except ValueError:
                continue
    return "local time"


def _format_task_events(task) -> list[dict]:
    events = task.meta.get("events", [])
    formatted = []
    for event in events[-6:]:
        event_time = event.get("time")
        formatted.append({
            "time": _format_task_time(event_time),
            "short_time": _format_task_time(event_time, include_zone=False) if event_time else "",
            "message": event.get("message", ""),
        })
    return formatted


def _poll_interval_seconds(task) -> int:
    if task.status == "queued":
        return 2
    if task.status != "running":
        return 0
    try:
        return int(task.meta.get("poll_interval_seconds") or 2)
    except (TypeError, ValueError):
        return 2


def _task_display_name(task) -> str:
    return task.meta.get("workflow_label") or task.service.replace(".", " ").title()


def _task_item_label(task) -> str:
    return task.meta.get("item_label") or "Task info"


def _task_item_detail(task) -> str:
    if task.meta.get("item_detail"):
        return task.meta["item_detail"]
    if task.result and isinstance(task.result, dict):
        inputs = task.result.get("input", {}).get("inputs")
        if inputs:
            return f"{inputs[0]} + {len(inputs) - 1} more" if len(inputs) > 1 else inputs[0]
    return "Waiting for job details"


def _task_result_items(task) -> list[dict]:
    if not task.result or not isinstance(task.result, dict):
        return []
    return task.result.get("items", [])[:8]


def _task_display_status(task) -> str:
    return str(task.meta.get("display_status") or task.status)


def _task_status_label(display_status: str) -> str:
    return display_status.replace("_", " ")


def _task_status_variant(display_status: str) -> str:
    if display_status == "completed":
        return "success"
    if display_status == "failed":
        return "danger"
    if display_status in {"cancelled", "needs_review"}:
        return "warning"
    if display_status == "running":
        return "accent"
    return "muted"


def _task_queue_label(task, display_status: str) -> str:
    if display_status == "needs_review":
        return "Review"
    return task.queue_position if task.queue_position else "Now"


def _show_task_artifacts(task, *, on_detail_page: bool) -> bool:
    if not task.artifacts:
        return False
    if task.service.startswith("music.") and not on_detail_page:
        return False
    return True


def _task_view_model(task, *, container_id: str) -> dict:
    duration = None
    if task.started_at and task.finished_at:
        started = datetime.fromisoformat(task.started_at)
        finished = datetime.fromisoformat(task.finished_at)
        delta = max(int((finished - started).total_seconds()), 0)
        minutes, seconds = divmod(delta, 60)
        duration = f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"

    display_status = _task_display_status(task)
    on_detail_page = container_id == "task-detail-panel"
    queue_message = task.meta.get("queue_message")
    if display_status == "needs_review":
        queue_message = "Needs review before the remaining file can be downloaded."

    playlists = []
    if (
        task.service == "music.user_playlists"
        and task.status == "completed"
        and isinstance(task.result, dict)
    ):
        playlists = task.result.get("output", {}).get("playlists", [])
        for playlist in playlists:
            for track in playlist.get("tracks", []):
                track["payload"] = json.dumps(
                    {
                        "title": track.get("title", ""),
                        "artist": track.get("artist", ""),
                        "album": track.get("album", ""),
                        "image_url": track.get("image_url", ""),
                        "duration_ms": track.get("duration_ms", 0),
                    },
                    separators=(",", ":"),
                )

    return {
        "task": task,
        "task_card_url": _task_card_url(task.id, container_id),
        "task_page_url": _task_page_url(task.id),
        "artifact_list_url": _artifact_list_url(task.id),
        "is_detail_view": on_detail_page,
        "is_polling": task.status in {"queued", "running"},
        "poll_interval_seconds": _poll_interval_seconds(task),
        "display_status": display_status,
        "display_status_label": _task_status_label(display_status),
        "display_status_variant": _task_status_variant(display_status),
        "is_needs_review": display_status == "needs_review",
        "queue_label": _task_queue_label(task, display_status),
        "progress_terminal_label": "Review" if display_status == "needs_review" else "Finished",
        "show_artifacts_in_card": _show_task_artifacts(task, on_detail_page=on_detail_page),
        "duration_label": duration,
        "estimate_label": task.meta.get("estimated_total_label") or _format_duration_seconds(task.meta.get("estimated_transcription_seconds")),
        "wait_label": task.meta.get("estimated_wait_label"),
        "runtime_label": task.meta.get("estimated_runtime_label"),
        "remaining_label": task.meta.get("estimated_remaining_label"),
        "total_eta_label": task.meta.get("estimated_total_label"),
        "queue_message": queue_message,
        "current_activity": task.meta.get("current_activity"),
        "capacity": task.meta.get("capacity") or {},
        "lane_label": task.meta.get("lane_label"),
        "display_name": _task_display_name(task),
        "item_label": _task_item_label(task),
        "item_detail": _task_item_detail(task),
        "submitted_label": _format_task_time(task.submitted_at),
        "started_label": _format_task_time(task.started_at),
        "finished_label": _format_task_time(task.finished_at) if task.finished_at else "Not finished",
        "timezone_label": _task_timezone_label(task),
        "task_events": _format_task_events(task),
        "result_items": _task_result_items(task),
        "playlist_options": playlists,
        "library_warnings": task.result.get("warnings", []) if isinstance(task.result, dict) else [],
        "playlist_target_id": f"#{container_id}",
    }


def _task_summary_cards():
    summary = get_queue_summary()
    queued_jobs = summary["queued_jobs"]
    running_jobs = summary["running_jobs"]
    return [
        {
            "label": "Queue",
            "value": "Idle" if queued_jobs == 0 else f"{queued_jobs} queued",
            "variant": "success" if queued_jobs == 0 else "warning",
        },
        {
            "label": "Workers",
            "value": "Available" if running_jobs == 0 else f"{running_jobs} running",
            "variant": "success" if running_jobs == 0 else "accent",
        },
    ]
