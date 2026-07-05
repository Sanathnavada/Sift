"""
Lightweight task estimation helpers.

This module is intentionally side-effect light: it does not schedule or execute
jobs. It only annotates task metadata with conservative queue/runtime estimates
so the UI and API can explain what is happening across task lanes.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Iterable, Optional


DEFAULT_RUNTIME_SECONDS = 120
AUTH_TIMEOUT_SECONDS = 300

# Conservative defaults. These are not meant to be exact promises; they give the
# UI a useful starting point until runtime events refine the estimate.
SERVICE_RUNTIME_ESTIMATES: dict[str, dict[str, Any]] = {
    "media.instagram_auth": {
        "base": AUTH_TIMEOUT_SECONDS,
        "per_item": 0,
        "style": "user_wait",
        "note": "Waiting for user login; timeout based estimate.",
    },
    "media.youtube": {"base": 90, "per_item": 360, "style": "approx"},
    "media.public_user": {"base": 90, "per_item": 180, "style": "approx"},
    "media.private_user": {"base": 120, "per_item": 180, "style": "approx"},
    "media.post": {"base": 90, "per_item": 180, "style": "approx"},
    "media.ig_bulk": {"base": 90, "per_item": 180, "style": "approx"},
    "media.clean_bulk": {"base": 30, "per_item": 90, "style": "approx"},
    # Music work is mostly network/disk bound. Keep these estimates realistic
    # for the UI: one song is usually around one to two minutes, and playlist
    # selections should not look like long ML jobs.
    "music.song": {"base": 20, "per_item": 55, "style": "approx"},
    "music.yt": {"base": 15, "per_item": 45, "style": "approx"},
    "music.link": {"base": 30, "per_item": 60, "style": "approx"},
    "music.user_playlists": {"base": 45, "per_item": 0, "style": "approx"},
    "music.user_download": {"base": 20, "per_item": 45, "style": "approx"},
    "music.candidate_download": {"base": 15, "per_item": 45, "style": "approx"},
    "telegram.agent": {"base": 30, "per_item": 0, "style": "open_ended"},
}

_TRANSCRIPTION_RE = re.compile(
    r"Estimated transcription time:\s*(?:(\d+)\s*min\s*)?(\d+)\s*sec",
    re.IGNORECASE,
)
_FETCHED_POSTS_RE = re.compile(
    r"Fetched\s+(\d+)\s+post\(s\):\s+(\d+)\s+visual,\s+(\d+)\s+reel/video,\s+(\d+)\s+other",
    re.IGNORECASE,
)
_RUNNING_OCR_RE = re.compile(r"Running OCR for\s+(\d+)\s+Instagram image", re.IGNORECASE)
_TRANSCRIBING_VIDEOS_RE = re.compile(r"Transcribing\s+(\d+)\s+Instagram video", re.IGNORECASE)
_PROCESSING_POSTS_RE = re.compile(r"Processing\s+(\d+)\s+new post", re.IGNORECASE)
_RESOLVED_SPOTIFY_RE = re.compile(r"Resolved Spotify metadata:\s+(\d+)\s+track", re.IGNORECASE)
_DOWNLOADING_MATCHES_RE = re.compile(r"Downloading\s+(\d+)\s+YouTube match", re.IGNORECASE)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _started_at_timestamp(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _submitted_count(meta: dict[str, Any]) -> int:
    for key in (
        "submitted_count",
        "track_count",
        "url_count",
        "input_count",
        "item_count",
    ):
        count = _safe_int(meta.get(key))
        if count > 0:
            return count
    workload = meta.get("workload")
    if isinstance(workload, dict):
        for key in ("submitted_count", "posts", "tracks", "urls", "inputs"):
            count = _safe_int(workload.get(key))
            if count > 0:
                return count
    return 1


def estimate_initial_runtime_seconds(service: str, meta: Optional[dict[str, Any]] = None) -> int:
    meta = meta or {}
    config = SERVICE_RUNTIME_ESTIMATES.get(service, {"base": 60, "per_item": DEFAULT_RUNTIME_SECONDS})
    count = _submitted_count(meta)
    seconds = _safe_int(config.get("base"), 60) + (_safe_int(config.get("per_item"), DEFAULT_RUNTIME_SECONDS) * max(count, 1))
    if service == "media.instagram_auth":
        seconds = _safe_int(meta.get("auth_timeout_seconds"), AUTH_TIMEOUT_SECONDS)
    return max(seconds, 15)


def format_seconds(value: Optional[int], *, approximate: bool = False, up_to: bool = False) -> str:
    if value is None:
        return "Calculating"
    seconds = max(int(value), 0)
    if seconds == 0:
        return "Now"

    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        label = f"{hours}h {minutes:02d}m"
    elif minutes:
        label = f"{minutes}m {remainder:02d}s" if remainder and minutes < 10 else f"{minutes}m"
    else:
        label = f"{remainder}s"

    if up_to:
        return f"Up to {label}"
    if approximate:
        return f"Approx. {label}"
    return label


def _estimate_style(service: str, meta: dict[str, Any]) -> str:
    if meta.get("estimate_style"):
        return str(meta["estimate_style"])
    return str(SERVICE_RUNTIME_ESTIMATES.get(service, {}).get("style") or "approx")


def _sync_labels(task: Any) -> None:
    meta = task.meta
    display_status = str(meta.get("display_status") or task.status)
    if display_status == "needs_review":
        actual_runtime = meta.get("actual_runtime_seconds")
        meta["estimated_wait_label"] = "Now"
        meta["estimated_runtime_label"] = (
            f"{format_seconds(actual_runtime)} actual"
            if actual_runtime is not None
            else format_seconds(meta.get("estimated_runtime_seconds"), approximate=False)
        )
        meta["estimated_total_label"] = "Needs review"
        meta["estimated_remaining_label"] = "Needs review"
        return

    if task.status in {"completed", "failed", "cancelled"}:
        actual_runtime = meta.get("actual_runtime_seconds")
        meta["estimated_wait_label"] = "Now"
        meta["estimated_runtime_label"] = (
            f"{format_seconds(actual_runtime)} actual"
            if actual_runtime is not None
            else format_seconds(meta.get("estimated_runtime_seconds"), approximate=False)
        )
        meta["estimated_total_label"] = task.status.title()
        meta["estimated_remaining_label"] = task.status.title()
        return

    style = _estimate_style(task.service, meta)
    approximate = style == "approx"
    user_wait = style == "user_wait"

    meta["estimated_wait_label"] = format_seconds(meta.get("estimated_wait_seconds"), approximate=False)
    meta["estimated_runtime_label"] = format_seconds(
        meta.get("estimated_runtime_seconds"),
        approximate=approximate,
        up_to=user_wait,
    )
    meta["estimated_total_label"] = format_seconds(
        meta.get("estimated_total_seconds"),
        approximate=approximate,
        up_to=user_wait and task.status in {"queued", "running"},
    )
    meta["estimated_remaining_label"] = format_seconds(
        meta.get("estimated_remaining_seconds"),
        approximate=approximate,
        up_to=user_wait and task.status == "running",
    )


def initialize_task_estimate(task: Any) -> None:
    meta = task.meta
    workload = dict(meta.get("workload") or {})
    submitted_count = _submitted_count(meta)
    workload.setdefault("submitted_count", submitted_count)
    meta["workload"] = workload

    meta.setdefault("estimated_runtime_seconds", estimate_initial_runtime_seconds(task.service, meta))
    meta.setdefault("estimated_wait_seconds", 0)
    meta.setdefault("estimated_total_seconds", meta.get("estimated_runtime_seconds"))
    meta.setdefault("estimated_remaining_seconds", meta.get("estimated_runtime_seconds"))
    meta.setdefault("queue_message", "Waiting for a processing slot.")
    meta.setdefault("estimate_source", "initial")
    if task.service == "media.instagram_auth":
        meta.setdefault("estimate_note", "Interactive login runs separately from music and heavy media jobs.")
    _sync_labels(task)


def _merge_workload(meta: dict[str, Any], **updates: int) -> None:
    workload = dict(meta.get("workload") or {})
    for key, value in updates.items():
        if value is not None:
            workload[key] = max(_safe_int(value), 0)
    meta["workload"] = workload


def update_estimate_from_event(task: Any, message: str) -> None:
    meta = task.meta
    meta["current_activity"] = message

    match = _TRANSCRIPTION_RE.search(message)
    if match:
        minutes = _safe_int(match.group(1))
        seconds = _safe_int(match.group(2))
        estimated_seconds = (minutes * 60) + seconds
        meta["estimated_transcription_seconds"] = estimated_seconds
        meta["estimated_runtime_seconds"] = max(
            _safe_int(meta.get("estimated_runtime_seconds"), 0),
            estimated_seconds + 45,
        )
        meta["poll_interval_seconds"] = max(5, min(20, estimated_seconds // 4 or 5))
        meta["estimate_source"] = "transcription_event"

    match = _FETCHED_POSTS_RE.search(message)
    if match:
        posts = _safe_int(match.group(1))
        visual = _safe_int(match.group(2))
        videos = _safe_int(match.group(3))
        other = _safe_int(match.group(4))
        _merge_workload(meta, posts=posts, visual_posts=visual, video_posts=videos, other_posts=other)
        refined = 60 + (visual * 75) + (videos * 240) + (other * 45)
        meta["estimated_runtime_seconds"] = max(_safe_int(meta.get("estimated_runtime_seconds"), 0), refined)
        meta["estimate_source"] = "instagram_fetch_event"

    match = _RUNNING_OCR_RE.search(message)
    if match:
        images = _safe_int(match.group(1))
        _merge_workload(meta, images=images)
        meta["estimated_runtime_seconds"] = max(_safe_int(meta.get("estimated_runtime_seconds"), 0), 45 + (images * 60))
        meta["estimate_source"] = "ocr_event"

    match = _TRANSCRIBING_VIDEOS_RE.search(message)
    if match:
        videos = _safe_int(match.group(1))
        _merge_workload(meta, videos=videos)
        meta["estimated_runtime_seconds"] = max(_safe_int(meta.get("estimated_runtime_seconds"), 0), 60 + (videos * 240))
        meta["estimate_source"] = "video_event"

    match = _PROCESSING_POSTS_RE.search(message)
    if match:
        posts = _safe_int(match.group(1))
        _merge_workload(meta, posts=posts)
        meta["estimated_runtime_seconds"] = max(_safe_int(meta.get("estimated_runtime_seconds"), 0), 60 + (posts * 150))
        meta["estimate_source"] = "post_count_event"

    match = _RESOLVED_SPOTIFY_RE.search(message)
    if match:
        tracks = _safe_int(match.group(1))
        _merge_workload(meta, tracks=tracks)
        meta["estimated_runtime_seconds"] = max(_safe_int(meta.get("estimated_runtime_seconds"), 0), 30 + (tracks * 45))
        meta["estimate_source"] = "spotify_metadata_event"

    match = _DOWNLOADING_MATCHES_RE.search(message)
    if match:
        tracks = _safe_int(match.group(1))
        _merge_workload(meta, tracks=tracks)
        meta["estimated_runtime_seconds"] = max(_safe_int(meta.get("estimated_runtime_seconds"), 0), 20 + (tracks * 45))
        meta["estimate_source"] = "music_download_event"

    _sync_labels(task)


def _remaining_seconds(task: Any, *, now_ts: float) -> int:
    runtime = _safe_int(task.meta.get("estimated_runtime_seconds"), estimate_initial_runtime_seconds(task.service, task.meta))
    if task.status != "running":
        return runtime
    started_ts = _started_at_timestamp(task.started_at)
    if started_ts is None:
        return runtime
    elapsed = max(int(now_ts - started_ts), 0)
    # Keep a small floor while a task is actively running so queued tasks do not
    # misleadingly show "Now" until the worker really exits.
    return max(runtime - elapsed, 15)


def update_queue_estimates(
    *,
    tasks: Iterable[Any],
    queues_by_lane: dict[str, Any],
    running_task_ids: Iterable[str],
    lane_concurrency: dict[str, int],
    lane_label_fn: Callable[[str], str],
    exclusive_group_fn: Callable[[str], Optional[str]],
) -> None:
    task_list = list(tasks)
    tasks_by_id = {task.id: task for task in task_list}
    running_ids = set(running_task_ids)
    now_ts = datetime.now().timestamp()

    running_by_lane: dict[str, list[Any]] = {}
    running_group_remaining: dict[str, int] = {}
    queued_by_lane: dict[str, list[Any]] = {}

    for task in task_list:
        initialize_task_estimate(task)
        lane = task.meta.get("lane") or "heavy_media"
        if task.status == "running":
            running_by_lane.setdefault(lane, []).append(task)
            remaining = _remaining_seconds(task, now_ts=now_ts)
            task.meta["estimated_wait_seconds"] = 0
            task.meta["estimated_remaining_seconds"] = remaining
            task.meta["estimated_total_seconds"] = remaining
            
            if lane == "music":
                task.meta["queue_message"] = "Processing in the Music lane. Music jobs can continue while media processing or login sessions are active."
            elif lane == "auth":
                task.meta["queue_message"] = "Waiting for user approval. Login sessions do not block music jobs."
            else:
                task.meta["queue_message"] = "Processing in the Media lane. OCR and transcription jobs are controlled to protect CPU and memory."
            group = exclusive_group_fn(task.service)
            if group:
                running_group_remaining[group] = max(running_group_remaining.get(group, 0), remaining)
        elif task.status == "queued":
            queued_by_lane.setdefault(lane, []).append(task)
        elif task.status in {"completed", "failed", "cancelled"}:
            task.meta["estimated_wait_seconds"] = 0
            task.meta["estimated_remaining_seconds"] = 0
            task.meta["estimated_total_seconds"] = 0
            if task.meta.get("display_status") == "needs_review":
                task.meta["queue_message"] = "Needs review before the remaining file can be downloaded."
            else:
                task.meta["queue_message"] = "Completed. Outputs are available below or in the session tray." if task.status == "completed" else task.status.title()
        _sync_labels(task)

    for lane, queue in queues_by_lane.items():
        lane_label = lane_label_fn(lane)
        running_lane_tasks = running_by_lane.get(lane, [])
        lane_queued_count = len(queue)
        lane_running_count = len(running_lane_tasks)
        capacity = max(lane_concurrency.get(lane, 1), 1)

        slot_available_at = sorted(
            _remaining_seconds(task, now_ts=now_ts) for task in running_lane_tasks
        )[:capacity]
        while len(slot_available_at) < capacity:
            slot_available_at.append(0)

        for index, job in enumerate(queue, start=1):
            task = tasks_by_id.get(job.task_id)
            if not task or task.status != "queued":
                continue

            group = exclusive_group_fn(task.service)
            group_wait = running_group_remaining.get(group or "", 0)
            earliest_slot_index = min(range(len(slot_available_at)), key=slot_available_at.__getitem__)
            lane_wait = slot_available_at[earliest_slot_index]
            wait_seconds = max(lane_wait, group_wait)
            runtime = _safe_int(task.meta.get("estimated_runtime_seconds"), estimate_initial_runtime_seconds(task.service, task.meta))

            task.meta["estimated_wait_seconds"] = wait_seconds
            task.meta["estimated_remaining_seconds"] = wait_seconds + runtime
            task.meta["estimated_total_seconds"] = wait_seconds + runtime
            task.meta["capacity"] = {
                "lane": lane,
                "lane_label": lane_label,
                "lane_running": lane_running_count,
                "lane_queued": lane_queued_count,
                "lane_capacity": capacity,
            }

            if group_wait > lane_wait:
                task.meta["queue_message"] = "Waiting for the connected Instagram session to become available."
            elif index == 1 and lane_running_count == 0:
                task.meta["queue_message"] = f"Next in the {lane_label} lane."
            elif index == 1:
                task.meta["queue_message"] = f"Waiting for the running {lane_label} task to finish."
            else:
                task.meta["queue_message"] = f"Waiting behind {index - 1} task(s) in the {lane_label} lane."

            _sync_labels(task)
            slot_available_at[earliest_slot_index] = wait_seconds + runtime

    # Keep capacity metadata current for running tasks as well.
    for task in task_list:
        lane = task.meta.get("lane") or "heavy_media"
        task.meta["capacity"] = {
            "lane": lane,
            "lane_label": lane_label_fn(lane),
            "lane_running": len(running_by_lane.get(lane, [])),
            "lane_queued": len(queues_by_lane.get(lane, [])),
            "lane_capacity": lane_concurrency.get(lane, 1),
        }
        if task.id in running_ids:
            _sync_labels(task)
