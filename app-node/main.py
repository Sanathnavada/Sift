"""
app-node — Unified FastAPI gateway for all node services.

Endpoints
---------
  /api/telegram/*   Telegram agent  (start / stop / status)
  /api/music/*      Music pipeline  (song / yt / link)
  /api/media/*      Media scraper   (youtube / public-user / post / ig-bulk / private-user / clean-bulk)
  /api/tasks        Task list + polling
  /health           Liveness probe
  /docs             Swagger UI

Run:
    python main.py
"""
import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from settings import NAVIDROME_EXE, NAVIDROME_DIR
from routers import telegram, music, media
from tasks import all_tasks, get_task, cancel_task

logger = logging.getLogger(__name__)

_navidrome_proc = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _navidrome_proc
    # ── Startup ───────────────────────────────────────────────────────────────
    if NAVIDROME_EXE.exists():
        _navidrome_proc = await asyncio.create_subprocess_exec(
            str(NAVIDROME_EXE),
            cwd=str(NAVIDROME_DIR),
        )
        logger.info(f"Navidrome started (pid {_navidrome_proc.pid}) on http://localhost:4533")
    else:
        logger.warning(f"Navidrome not found at {NAVIDROME_EXE} — skipping.")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if _navidrome_proc and _navidrome_proc.returncode is None:
        _navidrome_proc.terminate()
        await _navidrome_proc.wait()
        logger.info("Navidrome stopped.")


app = FastAPI(title="Node Services API", version="1.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(telegram.router, prefix="/api")
app.include_router(music.router,    prefix="/api")
app.include_router(media.router,    prefix="/api")


@app.get("/api/tasks", tags=["Tasks"])
async def list_tasks():
    return [
        {"id": t.id, "service": t.service, "status": t.status,
         "started_at": t.started_at, "finished_at": t.finished_at, "error": t.error}
        for t in all_tasks()
    ]


@app.get("/api/tasks/{task_id}", tags=["Tasks"])
async def get_task_detail(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task '{task_id}' not found.")
    return {"id": task.id, "service": task.service, "status": task.status,
            "started_at": task.started_at, "finished_at": task.finished_at,
            "error": task.error}


@app.delete("/api/tasks/{task_id}", tags=["Tasks"])
async def kill_task(task_id: str):
    if not get_task(task_id):
        raise HTTPException(404, f"Task '{task_id}' not found.")
    if not await cancel_task(task_id):
        raise HTTPException(400, "Task is not running.")
    return {"status": "cancelled", "task_id": task_id}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
