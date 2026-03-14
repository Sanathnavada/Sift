"""
Task store + runner for direct function calls.

Two runners:
  run_in_thread  — wraps a sync service call in asyncio.to_thread (non-blocking)
  run_async      — wraps a long-running async coroutine (telegram agent, supports cancel)

Logs from service code appear in the server console via Python's logging module.
"""
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Task:
    id: str
    service: str
    status: str = "running"
    started_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    error: Optional[str] = None


_tasks: dict[str, Task] = {}
_async_handles: dict[str, asyncio.Task] = {}  # for cancel support (telegram)


def new_task(service: str) -> Task:
    t = Task(id=str(uuid.uuid4()), service=service)
    _tasks[t.id] = t
    return t


def get_task(task_id: str) -> Optional[Task]:
    return _tasks.get(task_id)


def all_tasks() -> list[Task]:
    return list(_tasks.values())


async def cancel_task(task_id: str) -> bool:
    task = _tasks.get(task_id)
    if not task or task.status != "running":
        return False
    handle = _async_handles.pop(task_id, None)
    if handle:
        handle.cancel()
    task.status = "cancelled"
    task.finished_at = _now()
    return True


async def run_in_thread(task: Task, fn: Callable, *args) -> None:
    """Run a sync service function in a thread pool without blocking the event loop."""
    try:
        await asyncio.to_thread(fn, *args)
        task.status = "completed"
    except Exception as e:
        task.error = str(e)
        task.status = "failed"
    finally:
        task.finished_at = _now()


async def run_async(task: Task, coro) -> None:
    """Run a long-lived async coroutine as a cancellable background task."""
    handle = asyncio.create_task(coro)
    _async_handles[task.id] = handle
    try:
        await handle
        task.status = "completed"
    except asyncio.CancelledError:
        task.status = "cancelled"
    except Exception as e:
        task.error = str(e)
        task.status = "failed"
    finally:
        _async_handles.pop(task.id, None)
        task.finished_at = _now()
