"""Task and artifact API routes."""

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse

from ..runtime.artifacts import list_artifacts, resolve_artifact_path
from ..runtime.tasks import all_tasks, cancel_task, get_task

router = APIRouter(prefix="/tasks", tags=["Tasks"])


def _task_summary(task) -> dict:
    display_status = task.meta.get("display_status") or task.status
    return {
        "id": task.id,
        "service": task.service,
        "status": task.status,
        "display_status": display_status,
        "display_status_label": task.meta.get("display_status_label") or display_status.replace("_", " "),
        "submitted_at": task.submitted_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "queue_position": task.queue_position,
        "lane": task.meta.get("lane"),
        "lane_label": task.meta.get("lane_label"),
        "queue_message": task.meta.get("queue_message"),
        "estimated_wait_seconds": task.meta.get("estimated_wait_seconds"),
        "estimated_runtime_seconds": task.meta.get("estimated_runtime_seconds"),
        "estimated_total_seconds": task.meta.get("estimated_total_seconds"),
        "error": task.error,
    }


def _task_detail(task) -> dict:
    payload = _task_summary(task)
    payload.update(
        {
            "estimated_remaining_seconds": task.meta.get("estimated_remaining_seconds"),
            "workload": task.meta.get("workload"),
            "capacity": task.meta.get("capacity"),
            "result": task.result,
            "artifacts": task.artifacts,
        }
    )
    return payload


@router.get("")
async def list_tasks():
    return [_task_summary(task) for task in all_tasks()]


@router.get("/{task_id}")
async def get_task_detail(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task '{task_id}' not found.")

    payload = _task_detail(task)
    if task.status in {"queued", "waiting_for_user", "running"}:
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=payload)
    return JSONResponse(status_code=status.HTTP_200_OK, content=payload)


@router.get("/{task_id}/artifacts")
async def get_task_artifacts(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task '{task_id}' not found.")
    return {"task_id": task_id, "artifacts": list_artifacts(task_id)}


@router.get("/{task_id}/artifacts/{artifact_id:path}")
async def download_task_artifact(task_id: str, artifact_id: str):
    path = resolve_artifact_path(task_id, artifact_id)
    if not path:
        raise HTTPException(404, "Artifact not found.")
    return FileResponse(path, filename=path.name)


@router.delete("/{task_id}")
async def kill_task(task_id: str):
    if not get_task(task_id):
        raise HTTPException(404, f"Task '{task_id}' not found.")
    if not await cancel_task(task_id):
        raise HTTPException(400, "Task is not queued or running.")
    return {"status": "cancelled", "task_id": task_id}
