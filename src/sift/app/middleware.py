"""HTTP middleware registration for the FastAPI gateway."""

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .runtime.artifacts import cleanup_expired_artifacts

_rate_limit_window_seconds = 60
_rate_limit_max_requests = 10
_rate_limit_buckets: dict[str, deque[float]] = defaultdict(deque)
_job_submission_prefixes = (
    "/api/media/",
    "/api/music/",
    "/api/telegram/start",
)
_rate_limit_exempt_prefixes = (
    "/api/tasks",
    "/api/music/user/auth/callback",
    "/api/music/user/auth/",
    "/callback",
    "/health",
    "/docs",
    "/openapi.json",
)


def register_middlewares(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def rate_limit_submissions(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        cleanup_expired_artifacts()

        path = request.url.path
        if path.startswith(_rate_limit_exempt_prefixes):
            return await call_next(request)

        is_submission = request.method in {"POST", "GET"} and path.startswith(_job_submission_prefixes)
        if is_submission:
            client_id = request.client.host if request.client else "unknown"
            now = time.time()
            bucket = _rate_limit_buckets[client_id]
            while bucket and now - bucket[0] > _rate_limit_window_seconds:
                bucket.popleft()
            if len(bucket) >= _rate_limit_max_requests:
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Rate limit exceeded for job submission endpoints."},
                )
            bucket.append(now)

        return await call_next(request)
