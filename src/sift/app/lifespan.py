"""Application startup and shutdown lifecycle hooks."""

import asyncio
import logging
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from .runtime.tasks import shutdown_tasks, startup_tasks
from .settings import NAVIDROME_DIR, NAVIDROME_ENABLED, NAVIDROME_EXE

logger = logging.getLogger(__name__)

_navidrome_proc = None


def _is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def _start_navidrome() -> None:
    global _navidrome_proc

    if not NAVIDROME_ENABLED:
        logger.info("Navidrome disabled by NAVIDROME_ENABLED=false.")
        return

    if _is_port_open("127.0.0.1", 4533):
        logger.info("Navidrome already available on http://localhost:4533 - reusing it.")
        return

    if not NAVIDROME_EXE.exists():
        logger.warning("Navidrome not found at %s - skipping.", NAVIDROME_EXE)
        return

    if sys.platform == "win32":
        _navidrome_proc = subprocess.Popen(
            [str(NAVIDROME_EXE)],
            cwd=str(NAVIDROME_DIR),
        )
    else:
        _navidrome_proc = await asyncio.create_subprocess_exec(
            str(NAVIDROME_EXE),
            cwd=str(NAVIDROME_DIR),
        )
    logger.info("Navidrome started (pid %s) on http://localhost:4533", _navidrome_proc.pid)


async def _stop_navidrome() -> None:
    global _navidrome_proc

    if not _navidrome_proc:
        return

    running = (
        _navidrome_proc.poll() is None
        if hasattr(_navidrome_proc, "poll")
        else _navidrome_proc.returncode is None
    )
    if running:
        _navidrome_proc.terminate()
        if sys.platform == "win32":
            _navidrome_proc.wait()
        else:
            await _navidrome_proc.wait()
        logger.info("Navidrome stopped.")
    _navidrome_proc = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await startup_tasks()
    await _start_navidrome()

    yield

    await shutdown_tasks()
    await _stop_navidrome()
