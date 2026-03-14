"""
Telegram Agent  —  /api/telegram/*

Imports run_agent() directly from i-node/telegram_agent.py.
Singleton: only one agent instance runs at a time.
"""
import sys
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException

from settings import INODE_DIR
from tasks import new_task, get_task, cancel_task, run_async

# Add i-node to path so telegram_agent.py can resolve its own imports
sys.path.insert(0, str(INODE_DIR))
from telegram_agent import run_agent  # noqa: E402

router = APIRouter(prefix="/telegram", tags=["Telegram Agent"])

_agent_task_id: Optional[str] = None


@router.post("/start")
async def start_agent():
    global _agent_task_id

    existing = get_task(_agent_task_id) if _agent_task_id else None
    if existing and existing.status == "running":
        return {"status": "already_running", "task_id": _agent_task_id}

    task = new_task("telegram.agent")
    _agent_task_id = task.id
    asyncio.create_task(run_async(task, run_agent()))
    return {"status": "started", "task_id": task.id}


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
    return {"status": task.status, "task_id": task.id, "error": task.error}
