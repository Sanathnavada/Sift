import asyncio
from collections import deque

from starlette.requests import Request
from fastapi.testclient import TestClient

from sift.app.runtime.task_events import TaskEventNotifier, task_event_notifier
from sift.app.runtime.tasks import Task, task_manager
from sift.app.main import app
from sift.app.web.routes import (
    _system_status_event_stream,
    _task_card_event_stream,
    TASK_TERMINAL_STATUSES,
)


def _request() -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({
        "type": "http",
        "method": "GET",
        "path": "/ui/tasks/task-1/stream",
        "headers": [],
        "query_string": b"",
    }, receive)


def _system_request() -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({
        "type": "http",
        "method": "GET",
        "path": "/ui/system/status/stream",
        "headers": [],
        "query_string": b"",
    }, receive)


def _reset_task_manager() -> None:
    task_manager._tasks.clear()
    for lane in list(task_manager._queues):
        task_manager._queues[lane] = deque()
    task_manager._running_handles.clear()
    task_manager._running_lanes.clear()


def test_task_event_notifier_wakes_subscriber_on_publish():
    async def run() -> None:
        notifier = TaskEventNotifier()
        async with notifier.subscribe("task-1") as updates:
            notifier.publish("task-1")
            await asyncio.wait_for(updates.get(), timeout=1)

    asyncio.run(run())


def test_task_card_sse_stream_sends_initial_render():
    async def run() -> None:
        _reset_task_manager()
        task_manager._tasks["task-1"] = Task(
            id="task-1",
            service="music.song",
            status="completed",
            meta={"workflow_label": "Music download", "item_label": "Input", "item_detail": "song"},
        )

        stream = _task_card_event_stream(_request(), "task-1", container_id="music-task-panel")
        chunk = await stream.__anext__()

        assert chunk.startswith("event: task-card\n")
        assert 'class="task-card"' in chunk
        assert "Music download" in chunk

    asyncio.run(run())


def test_task_card_sse_stream_sends_final_update_and_terminates():
    async def run() -> None:
        _reset_task_manager()
        task = Task(
            id="task-1",
            service="music.song",
            status="running",
            meta={"workflow_label": "Music download", "item_label": "Input", "item_detail": "song"},
        )
        task_manager._tasks[task.id] = task

        stream = _task_card_event_stream(_request(), task.id, container_id="music-task-panel")
        initial = await stream.__anext__()
        assert "running" in initial

        final_update = asyncio.create_task(stream.__anext__())
        await asyncio.sleep(0)
        task.status = "completed"
        task.finished_at = "2026-07-07T12:00:00+00:00"
        task_event_notifier.publish(task.id)

        final = await asyncio.wait_for(final_update, timeout=1)
        assert "completed" in final
        assert task.status in TASK_TERMINAL_STATUSES

        try:
            await asyncio.wait_for(stream.__anext__(), timeout=1)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("Expected terminal task-card stream to stop")

    asyncio.run(run())


def test_existing_task_card_html_route_still_renders():
    _reset_task_manager()
    task_manager._tasks["task-1"] = Task(
        id="task-1",
        service="music.song",
        status="completed",
        meta={"workflow_label": "Music download", "item_label": "Input", "item_detail": "song"},
    )

    response = TestClient(app).get("/ui/tasks/task-1/card?container_id=music-task-panel")

    assert response.status_code == 200
    assert 'class="task-card"' in response.text
    assert "Music download" in response.text


def test_system_status_html_route_still_renders():
    response = TestClient(app).get("/ui/system/status")

    assert response.status_code == 200
    assert "status-grid" in response.text
    assert "Queue" in response.text


def test_system_status_sse_stream_sends_initial_render():
    async def run() -> None:
        stream = _system_status_event_stream(_system_request())
        chunk = await stream.__anext__()

        assert chunk.startswith("event: system-status\n")
        assert "status-grid" in chunk
        await stream.aclose()

    asyncio.run(run())


def test_system_status_sse_stream_emits_after_runtime_publish():
    async def run() -> None:
        stream = _system_status_event_stream(_system_request())
        initial = await stream.__anext__()
        assert "system-status" in initial

        next_update = asyncio.create_task(stream.__anext__())
        await asyncio.sleep(0)
        task_event_notifier.publish_system_status()

        update = await asyncio.wait_for(next_update, timeout=1)
        assert update.startswith("event: system-status\n")
        assert "status-grid" in update
        await stream.aclose()

    asyncio.run(run())


def test_home_system_status_panel_uses_sse_instead_of_htmx_polling():
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert 'id="system-status-panel"' in response.text
    assert 'data-system-status-stream-url="/ui/system/status/stream"' in response.text
    assert 'hx-trigger="load, every 8s"' not in response.text
