"""
Central task store and lane-aware job orchestrator for the Sift application runtime.

Heavy media jobs stay protected in a single ML-focused lane, while interactive
browser login, music downloads, and background agents run in their own bounded
lanes. This prevents a long Instagram login wait from blocking lightweight music
work without allowing unbounded background execution.
"""
import asyncio
import inspect
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from .estimation import initialize_task_estimate, update_estimate_from_event, update_queue_estimates
from .runtime_capacity import MAX_CONCURRENT_JOBS, TASK_LANE_CONCURRENCY


LANE_HEAVY_MEDIA = "heavy_media"
LANE_AUTH = "auth"
LANE_MUSIC = "music"
LANE_AGENT = "agent"

LANE_ORDER = (LANE_AUTH, LANE_MUSIC, LANE_HEAVY_MEDIA, LANE_AGENT)
LANE_LABELS = {
    LANE_HEAVY_MEDIA: "Media / ML",
    LANE_MUSIC: "Music",
    #LANE_AGENT: "Agent",
}

INSTAGRAM_SESSION_SERVICES = {
    "media.instagram_auth",
    "media.public_user",
    "media.private_user",
    "media.post",
    "media.ig_bulk",
}


JobRunner = Callable[[], Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lane_for_service(service: str) -> str:
    if service == "media.instagram_auth":
        return LANE_AUTH
    if service.startswith("music."):
        return LANE_MUSIC
    if service.startswith("telegram."):
        return LANE_AGENT
    return LANE_HEAVY_MEDIA


def _lane_label(lane: str) -> str:
    return LANE_LABELS.get(lane, lane.replace("_", " ").title())


def _exclusive_group_for_service(service: str) -> Optional[str]:
    if service in INSTAGRAM_SESSION_SERVICES:
        return "instagram_session"
    return None




def _result_needs_review(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    stats = result.get("stats")
    if isinstance(stats, dict):
        try:
            if int(stats.get("needs_review_count") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower().replace(" ", "_")
        if status == "needs_review" or item.get("candidates"):
            return True
    return False


def _actual_runtime_seconds(task: Any, finished_at: str) -> Optional[int]:
    if not task.started_at:
        return None
    try:
        started = datetime.fromisoformat(task.started_at)
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return None
    return max(int((finished - started).total_seconds()), 0)


def _prepare_meta(service: str, meta: Optional[dict[str, Any]]) -> dict[str, Any]:
    prepared = dict(meta or {})
    lane = _lane_for_service(service)
    exclusive_group = _exclusive_group_for_service(service)
    prepared.setdefault("lane", lane)
    prepared.setdefault("lane_label", _lane_label(lane))
    if exclusive_group:
        prepared.setdefault("exclusive_group", exclusive_group)
    return prepared


@dataclass
class Task:
    id: str
    service: str
    status: str = "queued"
    submitted_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    result: Any = None
    submitted_by: Optional[str] = None
    queue_position: int = 0
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class _QueuedJob:
    task_id: str
    runner: JobRunner


class TaskManager:
    def __init__(
        self,
        max_concurrent_jobs: int = 1,
        lane_concurrency: Optional[dict[str, int]] = None,
    ):
        self.max_concurrent_jobs = max(max_concurrent_jobs, 1)
        self.lane_concurrency = self._normalize_lane_concurrency(lane_concurrency)
        self._tasks: dict[str, Task] = {}
        self._queues: dict[str, deque[_QueuedJob]] = {
            lane: deque() for lane in self.lane_concurrency
        }
        for lane in LANE_ORDER:
            self._queues.setdefault(lane, deque())
        self._queue_event = asyncio.Event()
        self._worker_task: Optional[asyncio.Task] = None
        self._running_handles: dict[str, asyncio.Task] = {}
        self._running_lanes: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _normalize_lane_concurrency(
        self,
        lane_concurrency: Optional[dict[str, int]],
    ) -> dict[str, int]:
        if lane_concurrency is None:
            values = dict(TASK_LANE_CONCURRENCY)
        else:
            values = dict(lane_concurrency)

        # Backward-compatible fallback for older tests/configs that only pass a
        # global max_concurrent_jobs value.
        values.setdefault(LANE_HEAVY_MEDIA, self.max_concurrent_jobs)
        values.setdefault(LANE_AUTH, 1)
        values.setdefault(LANE_MUSIC, 1)
        values.setdefault(LANE_AGENT, 1)

        return {
            lane: max(int(limit), 1)
            for lane, limit in values.items()
        }

    async def startup(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def shutdown(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        for handle in list(self._running_handles.values()):
            handle.cancel()
        self._running_handles.clear()
        self._running_lanes.clear()

    async def submit(
        self,
        service: str,
        runner: JobRunner,
        submitted_by: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> Task:
        task = Task(
            id=str(uuid.uuid4()),
            service=service,
            submitted_by=submitted_by,
            meta=_prepare_meta(service, meta),
        )
        await self._enqueue_task(task, runner)
        return task

    async def submit_existing(self, task: Task, runner: JobRunner) -> Task:
        task.meta = _prepare_meta(task.service, task.meta)
        await self._enqueue_task(task, runner)
        return task

    async def _enqueue_task(self, task: Task, runner: JobRunner) -> None:
        lane = task.meta.get("lane") or _lane_for_service(task.service)
        task.meta["lane"] = lane
        task.meta.setdefault("lane_label", _lane_label(lane))
        initialize_task_estimate(task)
        async with self._lock:
            self._tasks[task.id] = task
            self._queues.setdefault(lane, deque()).append(_QueuedJob(task_id=task.id, runner=runner))
            self._refresh_queue_positions_locked()
            self._queue_event.set()

    def get_task(self, task_id: Optional[str]) -> Optional[Task]:
        if not task_id:
            return None
        self._refresh_estimates_unlocked()
        return self._tasks.get(task_id)

    def all_tasks(self) -> list[Task]:
        self._refresh_estimates_unlocked()
        return list(self._tasks.values())

    async def cancel_task(self, task_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False

            if task.status == "queued":
                for lane, queue in self._queues.items():
                    self._queues[lane] = deque(job for job in queue if job.task_id != task_id)
                task.status = "cancelled"
                task.finished_at = _now()
                task.queue_position = 0
                self._refresh_queue_positions_locked()
                self._queue_event.set()
                return True

            if task.status != "running":
                return False

            handle = self._running_handles.get(task_id)
            if handle:
                handle.cancel()
            return True

    def attach_artifacts(self, task_id: str, artifacts: list[dict[str, Any]]) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.artifacts = artifacts

    def queue_summary(self) -> dict[str, Any]:
        self._refresh_estimates_unlocked()
        tasks = list(self._tasks.values())
        queued = sum(1 for task in tasks if task.status == "queued")
        running = sum(1 for task in tasks if task.status == "running")
        lanes = []
        for lane in LANE_ORDER:
            lanes.append(self._lane_summary(lane, tasks))
        for lane in sorted(self.lane_concurrency):
            if lane not in LANE_ORDER:
                lanes.append(self._lane_summary(lane, tasks))

        status = "idle" if queued == 0 and running == 0 else "busy"
        return {
            "queued_jobs": queued,
            "running_jobs": running,
            "worker_status": "available" if running == 0 else "occupied",
            "queue_status": status,
            "lanes": lanes,
        }

    def _lane_summary(self, lane: str, tasks: list[Task]) -> dict[str, Any]:
        queued = sum(
            1 for task in tasks
            if task.status == "queued" and task.meta.get("lane") == lane
        )
        running = sum(
            1 for task in tasks
            if task.status == "running" and task.meta.get("lane") == lane
        )
        capacity = self.lane_concurrency.get(lane, 1)
        next_task = next(
            (task for task in tasks if task.status == "queued" and task.meta.get("lane") == lane),
            None,
        )
        return {
            "lane": lane,
            "label": _lane_label(lane),
            "queued": queued,
            "running": running,
            "capacity": capacity,
            "status": "idle" if queued == 0 and running == 0 else "busy",
            "next_wait_label": next_task.meta.get("estimated_wait_label") if next_task else None,
            "next_task_label": next_task.meta.get("workflow_label") if next_task else None,
        }

    async def _worker_loop(self) -> None:
        while True:
            await self._queue_event.wait()
            async with self._lock:
                jobs = self._select_ready_jobs_locked()
                if not self._has_queued_jobs_locked():
                    self._queue_event.clear()
                elif not jobs:
                    # Remaining jobs are blocked by lane capacity or an exclusive
                    # group. They will be reconsidered when a running task exits
                    # or a new task is submitted/cancelled.
                    self._queue_event.clear()

            if not jobs:
                continue

    def _select_ready_jobs_locked(self) -> list[_QueuedJob]:
        selected: list[_QueuedJob] = []
        running_groups = self._running_exclusive_groups_locked()

        # Keep scheduling until no lane can provide more ready work. This lets
        # auth/music run beside heavy media while still honoring per-lane caps.
        made_progress = True
        while made_progress:
            made_progress = False
            for lane in self._scheduled_lane_order_locked():
                if not self._lane_has_available_slot_locked(lane):
                    continue
                job = self._pop_ready_job_for_lane_locked(lane, running_groups)
                if not job:
                    continue
                self._start_job_locked(job, lane)
                selected.append(job)
                made_progress = True

        self._refresh_queue_positions_locked()
        return selected

    def _scheduled_lane_order_locked(self) -> list[str]:
        lanes = [lane for lane in LANE_ORDER if lane in self._queues]
        lanes.extend(lane for lane in self._queues if lane not in lanes)
        return lanes

    def _running_exclusive_groups_locked(self) -> set[str]:
        groups: set[str] = set()
        for task_id in self._running_handles:
            task = self._tasks.get(task_id)
            if not task:
                continue
            group = _exclusive_group_for_service(task.service)
            if group:
                groups.add(group)
        return groups

    def _lane_has_available_slot_locked(self, lane: str) -> bool:
        limit = self.lane_concurrency.get(lane, 1)
        running = sum(1 for running_lane in self._running_lanes.values() if running_lane == lane)
        return running < limit

    def _pop_ready_job_for_lane_locked(
        self,
        lane: str,
        running_groups: set[str],
    ) -> Optional[_QueuedJob]:
        queue = self._queues.get(lane)
        if not queue:
            return None

        selected: Optional[_QueuedJob] = None
        remaining: deque[_QueuedJob] = deque()
        while queue:
            job = queue.popleft()
            task = self._tasks.get(job.task_id)
            if not task or task.status != "queued":
                continue

            group = _exclusive_group_for_service(task.service)
            if selected is None and (not group or group not in running_groups):
                selected = job
                if group:
                    running_groups.add(group)
                continue
            remaining.append(job)

        self._queues[lane] = remaining
        return selected

    def _start_job_locked(self, job: _QueuedJob, lane: str) -> None:
        task = self._tasks[job.task_id]
        task.status = "running"
        task.started_at = _now()
        task.queue_position = 0
        handle = asyncio.create_task(self._run_job(job))
        self._running_handles[job.task_id] = handle
        self._running_lanes[job.task_id] = lane

    def _has_queued_jobs_locked(self) -> bool:
        return any(queue for queue in self._queues.values())

    async def _run_job(self, job: _QueuedJob) -> None:
        task = self._tasks[job.task_id]

        async def invoke_runner():
            if inspect.iscoroutinefunction(job.runner):
                return await job.runner()
            result = await asyncio.to_thread(job.runner)
            if inspect.isawaitable(result):
                return await result
            return result

        result: Any = None
        error: Optional[str] = None
        final_status = "completed"

        try:
            result = await invoke_runner()
        except asyncio.CancelledError:
            final_status = "cancelled"
            error = "Task was cancelled."
        except Exception as exc:
            final_status = "failed"
            error = str(exc)

        async with self._lock:
            task.result = result
            if isinstance(result, dict) and isinstance(result.get("artifacts"), list):
                task.artifacts = result["artifacts"]
            task.status = final_status
            task.error = error
            task.finished_at = _now()
            actual_runtime = _actual_runtime_seconds(task, task.finished_at)
            if actual_runtime is not None:
                task.meta["actual_runtime_seconds"] = actual_runtime

            if final_status == "completed" and _result_needs_review(result):
                task.meta["display_status"] = "needs_review"
                task.meta["display_status_label"] = "needs review"
                task.meta["queue_message"] = "Needs review: choose the correct candidate match below to finish the remaining item(s)."
            else:
                task.meta.pop("display_status", None)
                task.meta.pop("display_status_label", None)

            task.queue_position = 0
            self._running_handles.pop(task.id, None)
            self._running_lanes.pop(task.id, None)
            self._refresh_queue_positions_locked()
            self._queue_event.set()

    def _refresh_estimates_unlocked(self) -> None:
        update_queue_estimates(
            tasks=self._tasks.values(),
            queues_by_lane=self._queues,
            running_task_ids=self._running_handles.keys(),
            lane_concurrency=self.lane_concurrency,
            lane_label_fn=_lane_label,
            exclusive_group_fn=_exclusive_group_for_service,
        )

    def _refresh_queue_positions_locked(self) -> None:
        queued_ids = {job.task_id for queue in self._queues.values() for job in queue}
        for task in self._tasks.values():
            if task.id in queued_ids:
                continue
            if task.status == "queued":
                task.queue_position = 0

        for lane, queue in self._queues.items():
            for index, job in enumerate(queue, start=1):
                task = self._tasks.get(job.task_id)
                if not task:
                    continue
                task.queue_position = index
                task.meta.setdefault("lane", lane)
                task.meta.setdefault("lane_label", _lane_label(lane))

        self._refresh_estimates_unlocked()


task_manager = TaskManager(
    max_concurrent_jobs=MAX_CONCURRENT_JOBS,
    lane_concurrency=TASK_LANE_CONCURRENCY,
)


async def startup_tasks() -> None:
    await task_manager.startup()


async def shutdown_tasks() -> None:
    await task_manager.shutdown()


async def submit_sync_job(
    service: str,
    fn: Callable,
    *args,
    submitted_by: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> Task:
    return await task_manager.submit(
        service=service,
        runner=lambda: fn(*args),
        submitted_by=submitted_by,
        meta=meta,
    )


async def submit_bound_job(
    service: str,
    runner_factory: Callable[[Task], Any],
    submitted_by: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> Task:
    placeholder = Task(
        id=str(uuid.uuid4()),
        service=service,
        submitted_by=submitted_by,
        meta=_prepare_meta(service, meta),
    )
    return await task_manager.submit_existing(
        placeholder,
        lambda: runner_factory(placeholder),
    )


async def submit_async_job(
    service: str,
    coro_factory: Callable[[], Awaitable[Any]],
    submitted_by: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> Task:
    return await task_manager.submit(
        service=service,
        runner=coro_factory,
        submitted_by=submitted_by,
        meta=meta,
    )


def get_task(task_id: Optional[str]) -> Optional[Task]:
    return task_manager.get_task(task_id)


def all_tasks() -> list[Task]:
    return task_manager.all_tasks()


async def cancel_task(task_id: str) -> bool:
    return await task_manager.cancel_task(task_id)


def attach_artifacts(task_id: str, artifacts: list[dict[str, Any]]) -> None:
    task_manager.attach_artifacts(task_id, artifacts)


def append_task_event(task_id: str, message: str) -> None:
    task = task_manager.get_task(task_id)
    if not task:
        return
    events = task.meta.setdefault("events", [])
    events.append({"time": _event_time(), "message": message})
    del events[:-8]

    update_estimate_from_event(task, message)

    if "Transcribing audio" in message and task.meta.get("estimated_transcription_seconds"):
        task.meta["poll_interval_seconds"] = max(
            5,
            min(20, task.meta["estimated_transcription_seconds"] // 4 or 5),
        )
    task_manager._refresh_estimates_unlocked()


def get_queue_summary() -> dict[str, Any]:
    return task_manager.queue_summary()
