---
title: Sift
emoji: 🎧
colorFrom: gray
colorTo: indigo
sdk: docker
app_port: 7860
---

# Sift

Sift is a local-first FastAPI application for downloading, processing, reviewing, and packaging media/music outputs through a clean server-rendered UI.

It started as a set of small experimental scripts and has now been structured into a maintainable Python project with:

- a FastAPI backend
- a server-rendered HTMX/Jinja UI
- media extraction workflows
- music download workflows
- Instagram login/session handling
- Spotify authorization flow
- task tracking and artifact delivery
- automatic ZIP bundling for large outputs

---

## What Sift Does

Sift provides three main workflow areas:

```text
Media
Music
````

### Media Workflow

The Media workflow helps extract and process content from sources such as:

* Instagram posts
* Instagram public profiles
* Instagram private collections
* bulk Instagram URLs
* YouTube videos/transcripts

The output can include:

* images
* videos/reels
* parsed text/transcripts
* cleaned media artifacts

The main purpose of this workflow is to capture and preserve useful social media content that you come across and want to keep.

Instead of saving posts into a collection and forgetting about them, the workflow extracts the content through OCR and transcription, converts it into reusable text, and stores everything in one place. This makes the content easier to search, revisit, organize, and reuse later in any context.---

### Music Workflow

The Music workflow helps download and organize music from:

* direct song names
* YouTube links
* Spotify links
* Spotify playlists/library flows

It supports:

* matching songs to likely YouTube results
* reviewing uncertain matches
* downloading accepted tracks
* maintaining a music session tray
* bundling large task outputs into ZIP files

The main reason I built this workflow was that I already had a large music collection spread across Spotify, playlists, YouTube, and other places.

I did not want to keep depending on big platforms or pay repeatedly just to access music I already cared about. What I wanted was my own offline music library, built from my existing collection without manually searching and downloading every track.

The goal was simple: create a personal library that I can access anywhere, anytime.

---

### Authentication 


We make sure that we never store user credentials, passwords, or personal account information. Authentication is used only to access the user's own saved content for that session and perform the requested extraction. Once the workflow is complete, no sensitive authentication data is retained.

The objective is purely to help users build a searchable, reusable offline library from content they have already chosen to save—not to collect or retain any personal information.


Sift supports two browser-based authentication :



#### Instagram

Instagram login is used when private or restricted Instagram content scraping requires an authenticated session.

The app supports:

* opening a login window
* checking login state
* reusing an authenticated session
* resetting the session when needed
* safe user-facing error messages instead of raw browser automation errors

#### Spotify

Spotify authorization is used to access the logged-in user's playlists and library.

The Spotify app credentials identify the application. The Spotify account that logs in through the approval window is the actual user whose library is accessed.

The flow supports:

* opening a Spotify OAuth approval window
* detecting authorization status
* showing the connected Spotify user
* retrying failed/closed auth attempts
* rejecting placeholder credentials like `replace-me`

---

## Project Structure

```text
sift/
├── src/
│   └── sift/
│       ├── app/
│       │   ├── main.py
│       │   ├── lifespan.py
│       │   ├── middleware.py
│       │   ├── settings.py
│       │   │
│       │   ├── api/
│       │   │   ├── system.py
│       │   │   ├── tasks.py
│       │   │   └── routes/
│       │   │       ├── media.py
│       │   │       ├── music.py
│       │   │       └── telegram.py
│       │   │
│       │   ├── web/
│       │   │   ├── routes.py
│       │   │   ├── view_models.py
│       │   │   ├── form_parsers.py
│       │   │   ├── templates/
│       │   │   └── static/
│       │   │
│       │   └── runtime/
│       │       ├── artifacts.py
│       │       ├── auth_sessions.py
│       │       ├── input_resolver.py
│       │       ├── instagram_sessions.py
│       │       ├── music_download_tray.py
│       │       ├── runtime_capacity.py
│       │       └── tasks.py
│       │
│       ├── engines/
│       │   ├── media/
│       │   ├── music/
│       │   └── telegram/
│       │
│       └── integrations/
│           ├── navidrome/
│           └── novnc/
│
├── tests/
├── var/
├── downloads/
├── Dockerfile
├── docker-compose.yml
├── docker-entrypoint.sh
├── requirements.txt
├── pyproject.toml
├── README.md
└── DOCKER.md
```

---

## Important Folders

### `src/sift/app`

The FastAPI application layer.

This includes:

* app startup/shutdown
* middleware
* API routes
* UI routes
* task management
* authentication sessions
* artifact registration and delivery

---

### `src/sift/app/web`

The server-rendered UI.

This contains:

```text
templates/
static/
routes.py
view_models.py
form_parsers.py
```

Sift does not use a separate React/Vue frontend. The UI is served directly by FastAPI using Jinja templates and HTMX-style interactions.

---

### `src/sift/engines`

The actual processing engines.

```text
engines/media     → Instagram/YouTube/media extraction logic
engines/music     → Spotify/YouTube/music download logic
engines/telegram  → Telegram-related processing
```

---

### `var`

Runtime state used by the backend.

Examples:

```text
var/artifacts
var/sessions
var/music_config
```

This is where Sift stores internal runtime files such as task artifacts, session state, caches, and music config/runtime data.

---

### `downloads`

User-facing output files.

Examples:

```text
downloads/music
downloads/media
```

This is where final downloaded or processed files can be stored when persistent output is needed.

---

## Local Setup

### 1. Create a virtual environment

```bash
python -m venv venv
```

Activate it:

#### Windows PowerShell

```powershell
.\venv\Scripts\Activate.ps1
```

#### macOS/Linux

```bash
source venv/bin/activate
```

---

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

---

### 3. Install Playwright browsers

Instagram login and scraping flows may require Playwright.

```bash
python -m playwright install chromium
```

---

### 4. Configure environment variables

Create or update `.env.docker` in the project root.

Minimum useful configuration:

```env
APP_ENV=local
APP_HOST=0.0.0.0
APP_PORT=8000

NOVNC_ENABLED=false

SPOTIPY_CLIENT_ID=replace-me
SPOTIPY_CLIENT_SECRET=replace-me
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8000/callback

MUSIC_OUTPUT_DIR=downloads/music
MEDIA_OUTPUT_DIR=downloads/media
```

For Spotify features, replace the placeholder values with real Spotify Developer App credentials.

---

## Running Locally

From the project root:

```bash
python -m uvicorn sift.app.main:app --reload --host 0.0.0.0 --port 8000
```

If running from inside the `src` folder:

```bash
python -m uvicorn sift.app.main:app --reload --host 0.0.0.0 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

---

## Docker Usage

Build and run:

```bash
docker compose up --build
```

Open:

```text
http://127.0.0.1:8000
```

For Docker-specific details, see:

```text
DOCKER.md
```

---

## Spotify Setup

To use Spotify library/playlist flows:

1. Go to the Spotify Developer Dashboard.
2. Create an app.
3. Copy the Client ID and Client Secret.
4. Add the redirect URI:

```text
http://127.0.0.1:8000/callback
```

5. Set the values in `.env.docker`:

```env
SPOTIPY_CLIENT_ID=your-client-id
SPOTIPY_CLIENT_SECRET=your-client-secret
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8000/callback
```

The developer credentials identify the Sift application.

The Spotify account that logs in through the approval window is the user whose playlists/library are accessed.

If your Spotify app is still in development mode, make sure the Spotify account you use for testing is allowed in the Spotify app dashboard.

---

## Instagram Setup

Instagram flows may require a browser session.

For local development:

```env
NOVNC_ENABLED=false
```

This uses a normal local browser automation flow.

For Docker/noVNC deployments:

```env
NOVNC_ENABLED=true
NOVNC_AUTO_HOST=true
```

The app can open a browser-based login window and reuse the connected Instagram session for private or restricted media workflows.

---

## Output and Artifact Behavior

Sift tracks task outputs as artifacts.

For small outputs, files are shown individually.

For larger outputs, ZIP bundling is applied.

### Media ZIP behavior

```text
<= 10 files  → individual files
> 10 files   → one ZIP artifact
```

Media ZIPs include user-facing files such as:

```text
.jpg
.jpeg
.png
.webp
.mp4
.mov
.m4v
.txt
```

Internal/debug/cache files are excluded.

---

### Music ZIP behavior

```text
<= 10 audio files  → individual audio files
> 10 audio files   → ZIP artifact + individual audio files
```

Music ZIPs include audio files such as:

```text
.mp3
.m4a
.webm
.opus
.wav
.flac
.ogg
.aac
```

Individual audio artifacts are preserved so the music session tray continues to work.

Sift does not automatically ZIP the entire music session tray. ZIP files are created for the current music download task only.

---

## Task Runtime

Most long-running operations are handled as background tasks.

The UI shows:

* queued state
* running state
* finished state
* failed state
* runtime estimates
* logs/activity
* downloadable artifacts

Task-related APIs live under:

```text
/api/tasks
```

---

## API Docs

When the app is running, open:

```text
http://127.0.0.1:8000/docs
```

This shows the FastAPI-generated API documentation.

---

## Testing

Run the full test suite:

```bash
PYTHONPATH=src python -m pytest -q
```

On Windows PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m pytest -q
```

Compile check:

```bash
PYTHONPATH=src python -m compileall -q src tests
```

On Windows PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m compileall -q src tests
```

---

## Development Notes

The project intentionally follows a practical middle-ground structure.

It is not split into microservices and does not use unnecessary enterprise layering.

The main boundaries are:

```text
app          → FastAPI, UI, runtime, task orchestration
engines      → actual media/music/telegram processing logic
integrations → noVNC, Navidrome, external runtime helpers
```

This keeps the codebase simple, understandable, and maintainable without over-engineering.

---

## Current Status

Implemented and validated:

* FastAPI app shell
* server-rendered UI
* media workflows
* music workflows
* Spotify OAuth handling
* Instagram login/session handling
* task runtime
* artifact delivery
* ZIP bundling for large outputs
* Docker setup
* structured `src/sift` package layout
* regression/unit tests for core behavior

---

## License

This project is currently private/internal unless a license is explicitly added.

```
```
