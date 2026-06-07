# Gateway Console

## What this project does

This project is a local automation system with a built-in web UI and a FastAPI backend.

It exposes three main engines through one gateway:

- Telegram Agent
  Start, stop, and inspect a long-running Telegram runtime.
- Media Engine
  Process Instagram and YouTube inputs into structured files.
- Music Engine
  Resolve Spotify and YouTube sources into downloadable audio outputs.

The backend is API-first, and the UI is a thin server-rendered layer over the same backend.


## Main features

- One FastAPI gateway for all engines
- Shared task queue with one heavy job running at a time
- Task polling and task detail pages
- Temporary artifact downloads when no output directory is provided
- Persistent output mode when `outdir` is provided
- Spotify browser auth flow for user-library access
- Server-rendered UI using HTML templates, HTMX, CSS, and minimal JavaScript


## Project layout

- `app_node/`
  Main FastAPI gateway, UI router, templates, static files, and task system
- `music_node/`
  Music pipeline
- `media_node/`
  Media pipeline
- `i_node/`
  Telegram runtime
- `data/`
  Runtime outputs, artifacts, and sessions


## Install dependencies

If you already use the existing virtual environment, activate it first:

```powershell
.\venv\Scripts\Activate.ps1
```

If you need to install dependencies:

```powershell
pip install -r req.txt
```


## Environment variables

Create or update the root `.env` file.

Important variables used by the system include:

### Spotify

```env
SPOTIPY_CLIENT_ID=your_client_id
SPOTIPY_CLIENT_SECRET=your_client_secret
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8000/api/music/user/auth/callback
```

### Telegram / other services

```env
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash
```

### LLM / Ollama-style config

```env
OLLAMA_BASE_URL=https://ollama.com/v1
OLLAMA_API_KEY=your_key_if_needed
```

Update these values to match your local machine and accounts.


## How to start the backend and UI

There is only one server to start.

From the project root:

```powershell
uvicorn app_node.main:app --host 0.0.0.0 --port 8000 --reload
```

Or explicitly through the virtual environment:

```powershell
.\venv\Scripts\python.exe -m uvicorn app_node.main:app --host 0.0.0.0 --port 8000 --reload
```

This starts:

- the FastAPI backend
- the server-rendered UI
- the task queue
- Navidrome if configured and present


## Where to access the website

Once the server is running:

- Main UI:
  `http://127.0.0.1:8000/`
- Swagger docs:
  `http://127.0.0.1:8000/docs`

Main UI pages:

- Home:
  `http://127.0.0.1:8000/`
- Telegram:
  `http://127.0.0.1:8000/telegram`
- Media:
  `http://127.0.0.1:8000/media`
- Music:
  `http://127.0.0.1:8000/music`
- Task detail:
  `http://127.0.0.1:8000/tasks/{task_id}`


## How the UI connects to the backend

The UI lives inside the same FastAPI application.

There are 3 route layers:

1. Full HTML pages
- `/`
- `/music`
- `/media`
- `/telegram`
- `/tasks/{task_id}`

2. UI partial routes used by HTMX
- `/ui/...`

3. JSON API routes
- `/api/...`

The UI does not start a separate frontend server.

Instead:

- the page HTML comes from FastAPI templates
- HTMX requests fetch partial HTML from `/ui/...`
- those UI routes call the same backend task logic used by the JSON API
- finished jobs are still visible through `/api/tasks/...`


## Which API endpoints are used by the UI

### Shared task endpoints

- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/artifacts`
- `GET /api/tasks/{task_id}/artifacts/{artifact_id}`
- `DELETE /api/tasks/{task_id}`

### Telegram

- `POST /api/telegram/start`
- `POST /api/telegram/stop`
- `GET /api/telegram/status`

### Media

- `POST /api/media/youtube`
- `POST /api/media/post`
- `POST /api/media/public-user`
- `POST /api/media/private-user`
- `POST /api/media/ig-bulk`
- `POST /api/media/clean-bulk`

### Music

- `POST /api/music/song`
- `POST /api/music/yt`
- `POST /api/music/link`
- `POST /api/music/user/auth/start`
- `GET /api/music/user/auth/session/{auth_session_id}`
- `GET /api/music/user/auth/callback`
- `POST /api/music/user/auth/complete`
- `GET /api/music/user/playlists`
- `POST /api/music/user/playlists`
- `POST /api/music/user/download`


## How to navigate the website

### Home page

Use the homepage to understand the system quickly.

It shows:

- the three engines
- a simple architecture diagram
- live system status
- links into each engine page

### Telegram page

Use this page to:

- start the Telegram agent
- stop the Telegram agent
- refresh runtime status

### Media page

Use the workflow tabs to switch between:

- YouTube
- Instagram Post
- Public User
- Private Collection
- Bulk URLs
- Clean Output

Each form submits a queued job and shows the task card inline.

### Music page

Use the workflow tabs to switch between:

- Song Search
- YouTube Link
- Spotify Link
- Spotify Library

The Spotify Library flow is guided:

1. Start Spotify auth
2. Complete browser login
3. Wait for authorization
4. Fetch playlists
5. Select playlists
6. Submit download job

### Task page

Use the generic task page to:

- inspect task state
- watch queue/running/completed transitions
- inspect results
- download artifacts


## Output behavior

Most file-producing routes support 2 modes.

### Temporary artifact mode

If no output directory is provided:

- the backend creates a temporary job directory
- generated files are exposed through artifact endpoints
- the UI shows artifact download links

### Persistent output mode

If an output directory is provided:

- files are written there
- the backend result still reports task completion and output metadata


## Input behavior

Depending on the workflow, the UI supports:

- single direct input
- multi-line batch input
- local input file path

For input-file mode:

- `.txt`
  one item per line
- `.json`
  list of strings


## How to run verification commands

### Basic health check

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health
```

### Open API docs

Visit:

```text
http://127.0.0.1:8000/docs
```

### Check task list

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/tasks
```

### Load the main pages

```powershell
Invoke-WebRequest http://127.0.0.1:8000/
Invoke-WebRequest http://127.0.0.1:8000/music
Invoke-WebRequest http://127.0.0.1:8000/media
Invoke-WebRequest http://127.0.0.1:8000/telegram
```

### Verify UI partials

```powershell
Invoke-WebRequest http://127.0.0.1:8000/ui/system/status
Invoke-WebRequest http://127.0.0.1:8000/ui/music/forms/song
Invoke-WebRequest http://127.0.0.1:8000/ui/media/forms/youtube
```


## Notes about the UI implementation

- The UI is server-rendered.
- HTMX is used for:
  - loading workflow forms
  - submitting jobs
  - polling task status
  - refreshing artifact/task panels
- CSS is plain CSS.
- JavaScript is minimal and only supports small UI behaviors like output-mode toggles and active tab styling.


## Important limitation

The UI expects HTMX to be available in the browser via the included script tag.

If you want a fully offline frontend with no CDN dependency, the HTMX script should be vendored locally later.


## Useful companion files

- Technical overview:
  [README.md](/abs/path/C:/Users/Dell/Desktop/code/README.md:1)
- API endpoint notes:
  [app_node/endpoints.txt](/abs/path/C:/Users/Dell/Desktop/code/app_node/endpoints.txt:1)
- Implementation plan:
  [IMPLEMENTATION_PLAN.md](/abs/path/C:/Users/Dell/Desktop/code/IMPLEMENTATION_PLAN.md:1)
- UI plan:
  [UI_IMPLEMENTATION_PLAN.md](/abs/path/C:/Users/Dell/Desktop/code/UI_IMPLEMENTATION_PLAN.md:1)
