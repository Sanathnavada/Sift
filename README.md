# Project Overview

This repository is a multi-service local automation and media-processing system built around a single FastAPI gateway.

At a high level:

- `app_node` is the API gateway and orchestration layer.
- `music_node` handles Spotify to YouTube resolution and audio downloads.
- `media_node` handles Instagram and YouTube media scraping/transcription/cleaning.
- `i_node` runs the Telegram agent.

The gateway exposes all of these through one API surface, adds task polling, queueing, ephemeral artifact downloads, and a UI-friendly Spotify auth flow.


# Repository Structure

Main folders:

- `app_node/`
  The unified API gateway.
- `music_node/`
  Music processing pipeline.
- `media_node/`
  Media scraping and transcription pipeline.
- `i_node/`
  Telegram agent runtime.
- `data/`
  Runtime-generated outputs, artifacts, and sessions.
- `venv/`
  Local Python virtual environment.

Important root files:

- [README.md](/abs/path/C:/Users/Dell/Desktop/code/README.md:1)
  This document.
- [IMPLEMENTATION_PLAN.md](/abs/path/C:/Users/Dell/Desktop/code/IMPLEMENTATION_PLAN.md:1)
  The design and rollout plan that shaped the current implementation.
- [CLAUDE.md](/abs/path/C:/Users/Dell/Desktop/code/CLAUDE.md:1)
  Local engineering guidance for work in this repo.
- [req.txt](/abs/path/C:/Users/Dell/Desktop/code/req.txt:1)
  Dependency reference.


# System Architecture

## 1. `app_node` as the gateway

`app_node` is the entrypoint for API consumers. It is responsible for:

- exposing all routes under one FastAPI app
- starting and stopping the in-process task system
- optionally starting Navidrome on app startup
- rate limiting job submissions
- serializing heavy work through a single-worker queue
- exposing task polling and artifact download endpoints
- normalizing how API clients interact with long-running work

Main file:

- [app_node/main.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/main.py:1)

This file wires:

- the gateway app
- CORS
- job submission rate limiting
- router registration
- task polling endpoints
- artifact download endpoints
- Navidrome lifecycle


## 2. `music_node` as the music engine

`music_node` contains the core music logic:

- Spotify public link ingestion
- Spotify user library ingestion
- YouTube search/resolution
- actual yt-dlp downloads
- download history tracking

Important files:

- [music_node/main.py](/abs/path/C:/Users/Dell/Desktop/code/music_node/main.py:1)
  Original CLI entrypoint and source-of-truth behavior reference.
- [music_node/services/spotify.py](/abs/path/C:/Users/Dell/Desktop/code/music_node/services/spotify.py:1)
  Spotify auth and library access.
- [music_node/services/youtube.py](/abs/path/C:/Users/Dell/Desktop/code/music_node/services/youtube.py:1)
  YouTube resolution layer.
- [music_node/services/downloader.py](/abs/path/C:/Users/Dell/Desktop/code/music_node/services/downloader.py:1)
  Download engine.


## 3. `media_node` as the media engine

`media_node` contains the core media logic:

- Instagram public user scraping
- Instagram post scraping
- Instagram saved collection scraping via Playwright
- YouTube audio transcription
- OCR and Whisper passes
- LLM cleaning of raw output

Important files:

- [media_node/main.py](/abs/path/C:/Users/Dell/Desktop/code/media_node/main.py:1)
  Original CLI entrypoint and behavior reference.
- [media_node/services.py](/abs/path/C:/Users/Dell/Desktop/code/media_node/services.py:1)
  Processing and orchestration service layer.
- [media_node/insta/web_fetcher.py](/abs/path/C:/Users/Dell/Desktop/code/media_node/insta/web_fetcher.py:1)
  Playwright-backed private collection fetcher.
- [media_node/insta/ytdlp_fetcher.py](/abs/path/C:/Users/Dell/Desktop/code/media_node/insta/ytdlp_fetcher.py:1)
  Public Instagram fetcher.


## 4. `i_node` as the Telegram runtime

`i_node` contains the Telegram agent runtime.

Important file:

- [i_node/telegram_agent.py](/abs/path/C:/Users/Dell/Desktop/code/i_node/telegram_agent.py:1)

The gateway does not reimplement the Telegram logic. It wraps it with API-friendly task controls.


# Request Lifecycle

For heavy routes, the request flow is now:

1. Client sends a request to `app_node`.
2. The route validates payload shape.
3. The route normalizes inputs.
4. The route submits a job into the central queue.
5. The API returns `202 Accepted` with:
   - `task_id`
   - `status`
   - `queue_position`
   - `poll_url`
6. The single-worker task system runs the job when it reaches the front of the queue.
7. The client polls `GET /api/tasks/{task_id}`.
8. If the job produced ephemeral files, the client downloads them through artifact endpoints.


# Queueing And Task Model

The task and queue system lives in:

- [app_node/tasks.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/tasks.py:1)

What it does:

- stores tasks in memory
- assigns task ids
- runs heavy jobs through one FIFO worker
- supports queued and running states
- stores final results
- supports cancellation for queued and running tasks

Task statuses:

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`

Current execution model:

- one heavy job at a time
- in-process memory-backed queue
- no external queue system like Redis/Celery

This was chosen deliberately because the app is currently designed for one machine with constrained hardware.


# Rate Limiting

Rate limiting is applied in:

- [app_node/main.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/main.py:1)

Current behavior:

- limits job-submission traffic per client IP
- protects `/api/media/*`, `/api/music/*`, and `/api/telegram/start`
- leaves polling and docs routes exempt

The purpose is protective only.

Important distinction:

- rate limiting controls how often clients may submit jobs
- queueing controls when heavy jobs are actually allowed to execute


# Artifacts And Ephemeral Output

Artifact handling lives in:

- [app_node/artifacts.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/artifacts.py:1)

This is how the system handles temporary outputs when `outdir` is omitted.

Behavior:

- if `outdir` is provided:
  persistent mode
- if `outdir` is omitted:
  ephemeral mode

In ephemeral mode:

- a job-scoped directory is created under the artifact root
- files are generated there
- the task result includes artifact metadata
- files can be downloaded through API endpoints
- artifacts are cleaned up after a TTL

Task artifact endpoints:

- `GET /api/tasks/{task_id}/artifacts`
- `GET /api/tasks/{task_id}/artifacts/{artifact_id}`

Artifact metadata generally includes:

- artifact id
- filename
- content type
- size
- download URL


# Input Normalization

Shared input normalization lives in:

- [app_node/input_resolver.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/input_resolver.py:1)

This module gives the API one consistent way to support:

- direct payload input
- local `input_file` payload references

Supported patterns:

- single direct input
- multiple inline inputs
- `.txt` input file
- `.json` input file

Current file rules:

- `.txt`
  one item per line, blank lines ignored, `#` comments ignored
- `.json`
  list of strings


# Spotify Auth Flow

Spotify auth session management lives in:

- [app_node/auth_sessions.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/auth_sessions.py:1)

This was introduced because the original CLI Spotify flow was terminal-oriented and not suitable for Postman or a future web UI.

The current API-friendly flow is:

1. `POST /api/music/user/auth/start`
   Returns:
   - `auth_session_id`
   - `authorization_url`
   - `redirect_uri`
   - `poll_url`

2. Open `authorization_url` in a browser.

3. Complete auth through either:
   - `GET /api/music/user/auth/callback?...`
   - or `POST /api/music/user/auth/complete`

4. Poll auth status:
   - `GET /api/music/user/auth/session/{auth_session_id}`

5. Once authorized:
   - fetch playlists
   - download selected playlists

This keeps Spotify user auth explicit and UI-friendly.


# Router Design

## Music routes

Music API routes live in:

- [app_node/routers/music.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/routers/music.py:1)

What this router does:

- validates music request payloads
- normalizes direct and file-backed inputs
- supports singular and batch forms
- uses `music_node` services directly
- chooses persistent vs ephemeral output handling
- wraps final outputs as task results + artifacts
- handles Spotify auth session routes

Main route groups:

- `/api/music/song`
- `/api/music/yt`
- `/api/music/link`
- `/api/music/user/auth/*`
- `/api/music/user/playlists`
- `/api/music/user/download`


## Media routes

Media API routes live in:

- [app_node/routers/media.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/routers/media.py:1)

What this router does:

- normalizes YouTube and bulk URL input
- wraps public and private Instagram scraping
- supports persistent and ephemeral outputs
- registers produced files as artifacts
- uses a shared stable session directory for private Instagram login reuse

Main route groups:

- `/api/media/youtube`
- `/api/media/public-user`
- `/api/media/post`
- `/api/media/ig-bulk`
- `/api/media/private-user`
- `/api/media/clean-bulk`


## Telegram routes

Telegram routes live in:

- [app_node/routers/telegram.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/routers/telegram.py:1)

What this router does:

- starts the Telegram agent as a tracked async task
- exposes stop/status controls
- surfaces task metadata in the same gateway model


# API Conventions

## Async submission response

Heavy routes return:

```json
{
  "task_id": "uuid",
  "status": "queued",
  "queue_position": 1,
  "poll_url": "/api/tasks/uuid"
}
```

## Polling response

`GET /api/tasks/{task_id}` returns:

- `202` while the task is still in progress
- `200` when it has finished

Common fields:

- `id`
- `service`
- `status`
- `submitted_at`
- `started_at`
- `finished_at`
- `queue_position`
- `error`
- `result`
- `artifacts`


# Persistent Vs Ephemeral Outputs

This is one of the key design rules in the current system.

If the endpoint supports file output:

- passing `outdir` means:
  save files persistently there
- omitting `outdir` means:
  generate files in a temporary job directory and expose them as API artifacts

This behavior is already wired into:

- music `song`
- music `yt`
- music `link`
- music `user/download`
- media `youtube`
- media `public-user`
- media `post`
- media `ig-bulk`
- media `private-user`

`media/clean-bulk` is file-transform oriented and returns a persistent cleaned file path.


# Batch Support

The API now supports batch-oriented input for routes that previously behaved more singularly.

Music batch-capable routes:

- `song`
  - `query`
  - `queries`
  - `input_file`
- `yt`
  - `input`
  - `inputs`
  - `input_file`
- `link`
  - `url`
  - `urls`
  - `input_file`

Media batch-capable routes:

- `youtube`
  - `input`
  - `inputs`
  - `input_file`
- `ig-bulk`
  - `urls`
  - `input_file`

Each API request becomes one queued job, even if it contains multiple internal items.


# Navidrome Integration

Navidrome startup is handled in:

- [app_node/main.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/main.py:1)

Behavior:

- on app startup, if `Navidrome.exe` exists, the gateway launches it
- on app shutdown, the gateway stops it

Configured paths come from:

- [app_node/settings.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/settings.py:1)


# Important Configuration

Gateway settings:

- [app_node/settings.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/settings.py:1)

Music configuration:

- [music_node/config/config.py](/abs/path/C:/Users/Dell/Desktop/code/music_node/config/config.py:1)

Environment file:

- [.env](/abs/path/C:/Users/Dell/Desktop/code/.env:1)

Important values include:

- Spotify credentials
- Spotify redirect URI
- Ollama / LLM config
- any service-specific secrets


# How To Run

From the repo root:

```powershell
.\venv\Scripts\python.exe -m uvicorn app_node.main:app --host 0.0.0.0 --port 8000 --reload
```

Primary base URL:

```text
http://127.0.0.1:8000
```

Health check:

```text
GET /health
```


# How To Test

The Postman-oriented reference is here:

- [app_node/endpoints.txt](/abs/path/C:/Users/Dell/Desktop/code/app_node/endpoints.txt:1)

Recommended testing order:

1. Hit `/health`
2. Submit one music or media job
3. Poll `/api/tasks/{task_id}`
4. If ephemeral mode was used, list artifacts
5. Download one artifact
6. Test one `input_file` flow
7. Test Spotify auth start/status flow


# Data Flow Examples

## Example 1: Ephemeral music download

1. Client calls `POST /api/music/yt` without `outdir`
2. Router normalizes input
3. Router submits one queued job
4. Task runner executes YouTube resolve/download
5. Files are saved into a temp artifact directory
6. Artifacts are registered
7. Client polls task result
8. Client downloads artifact through `/api/tasks/{task_id}/artifacts/{artifact_id}`

## Example 2: Private Instagram collection scrape

1. Client calls `POST /api/media/private-user`
2. Router submits one queued job
3. Job creates output directory
4. `WebCollectionFetcher` reuses or creates a logged-in web session
5. Collection items are fetched
6. `ScraperService.process_posts(...)` processes them
7. Outputs are saved and artifacts registered if ephemeral
8. Client polls final task result

## Example 3: Spotify user playlists

1. Client starts auth session
2. User authorizes through Spotify browser flow
3. Auth session is marked complete
4. Client requests playlist fetch
5. Playlist fetch runs as a queued task
6. Client polls final playlist result
7. Client chooses playlists and submits download job


# Current Limitations And Design Choices

- The queue is in-memory only.
  If the server restarts, queued/running task state is lost.
- Heavy execution is intentionally serialized to one worker.
- Telegram currently shares the same general task framework, but because it is long-lived it can occupy the single-worker slot.
- External dependencies still determine actual runtime behavior:
  - Spotify
  - Instagram
  - yt-dlp
  - Playwright
  - Ollama / LLM endpoint
  - GPU model execution

These are deliberate tradeoffs for simplicity on a single-machine system.


# Where To Look When Debugging

If a route returns the wrong API shape:

- [app_node/main.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/main.py:1)
- [app_node/routers/music.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/routers/music.py:1)
- [app_node/routers/media.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/routers/media.py:1)

If queueing or task state looks wrong:

- [app_node/tasks.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/tasks.py:1)

If ephemeral downloads or missing files are the issue:

- [app_node/artifacts.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/artifacts.py:1)

If `input_file` payloads fail:

- [app_node/input_resolver.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/input_resolver.py:1)

If Spotify user auth fails:

- [app_node/auth_sessions.py](/abs/path/C:/Users/Dell/Desktop/code/app_node/auth_sessions.py:1)
- [music_node/services/spotify.py](/abs/path/C:/Users/Dell/Desktop/code/music_node/services/spotify.py:1)

If media processing itself fails:

- [media_node/services.py](/abs/path/C:/Users/Dell/Desktop/code/media_node/services.py:1)

If music downloads/resolution fail:

- [music_node/services/downloader.py](/abs/path/C:/Users/Dell/Desktop/code/music_node/services/downloader.py:1)
- [music_node/services/youtube.py](/abs/path/C:/Users/Dell/Desktop/code/music_node/services/youtube.py:1)


# Companion Docs

Useful companion documents:

- [IMPLEMENTATION_PLAN.md](/abs/path/C:/Users/Dell/Desktop/code/IMPLEMENTATION_PLAN.md:1)
- [app_node/endpoints.txt](/abs/path/C:/Users/Dell/Desktop/code/app_node/endpoints.txt:1)
- [CLAUDE.md](/abs/path/C:/Users/Dell/Desktop/code/CLAUDE.md:1)


# Summary

This project is now wired as a single API-first gateway around several specialized local processing nodes.

The important idea is:

- node folders still own the domain logic
- `app_node` owns orchestration, API contract, queueing, auth UX, and artifact delivery

That separation is what makes the system usable today from Postman and extensible later for a real UI.
