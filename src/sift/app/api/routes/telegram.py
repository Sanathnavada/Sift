"""
Telegram agent routes.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from ...runtime.tasks import cancel_task, get_task, submit_async_job

router = APIRouter(prefix="/telegram", tags=["Telegram Agent"])


def _run_agent():
    from sift.engines.telegram.agent import run_agent

    return run_agent()

_agent_task_id: Optional[str] = None


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


@router.post("/start")
async def start_agent(request: Request):
    global _agent_task_id

    existing = get_task(_agent_task_id) if _agent_task_id else None
    if existing and existing.status in {"queued", "running"}:
        return {
            "status": "already_running",
            "task_id": _agent_task_id,
            "queue_position": existing.queue_position,
        }

    task = await submit_async_job(
        "telegram.agent",
        lambda: _run_agent(),
        submitted_by=request.client.host if request.client else None,
    )
    _agent_task_id = task.id
    return _accepted_response(task)


@router.post("/stop")
async def stop_agent():
    global _agent_task_id

    if not _agent_task_id:
        raise HTTPException(404, "Agent is not running.")

    await cancel_task(_agent_task_id)
    stopped_id = _agent_task_id
    _agent_task_id = None
    return {"status": "stopped", "task_id": stopped_id}


@router.get("/status")
async def agent_status():
    task = get_task(_agent_task_id) if _agent_task_id else None
    if not task:
        return {"status": "stopped"}
    return {
        "status": task.status,
        "task_id": task.id,
        "queue_position": task.queue_position,
        "error": task.error,
    }
