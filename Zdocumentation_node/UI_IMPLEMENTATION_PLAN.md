

## Core UI idea

Build it like a **control-room portfolio**.

Not a heavy React dashboard. Not a plain form app. More like:

> A minimal black-and-white command console for three automation engines: Telegram, Media, and Music.

The homepage should immediately communicate:

1. This is a real working system.
2. It has three clear capabilities.
3. It has a clean backend architecture.
4. The user can either read the explanation or test the API-backed workflow.

Think of the UI as a thin, elegant layer over your FastAPI gateway.

## Recommended visual direction

Use this style:

* dark background, near-black, not pure black
* soft cards with thin borders
* one accent color, maybe electric blue, green, or violet
* monospaced labels for API-ish things
* clean sans-serif font for normal content
* lots of whitespace
* subtle hover states
* no big gradients everywhere
* no animated noise-heavy landing page
* no complex charts unless needed
* technical explanations as compact cards, not walls of text

The best aesthetic for this project is:

> minimal terminal energy, but recruiter-friendly.

A recruiter should be able to understand the project in 30 seconds. A technical interviewer should be able to inspect the API flow in 3 minutes.






## Runtime Capability States

The UI should show whether major capabilities are available, unavailable, disabled, or local-only.

Examples:
- Gateway: healthy / unavailable
- Queue: idle / busy / unavailable
- Media engine: available / not configured
- Music engine: available / not configured
- Telegram runtime: available / disabled / local-only
- Spotify auth: configured / missing credentials
- Instagram session: configured / missing session
- Artifact storage: ephemeral / persistent

Actions that cannot run should be disabled with a clear reason.

## UI/API Architecture Rule

Keep API routes and UI routes separate by response type.

- `/api/*` routes return JSON.
- `/ui/*` routes return HTML partials.
- Normal page routes return full server-rendered HTML pages.

Where possible, API routes and UI routes should call shared service-layer functions. Avoid making server-side UI routes call the app’s own HTTP API unless there is a specific reason.

## HTMX and Form Safety

Use HTMX for:
- Loading workflow forms
- Submitting jobs
- Polling task status
- Refreshing artifact lists

Avoid using HTMX for unrelated behavior.

For state-changing actions, ensure the public version is safe. Actions should be demo-only, protected, rate-limited, disabled, or clearly local-only if they can trigger private credentials, long jobs, file writes, or external account actions.

## Responsive and Portfolio-Ready UI

The UI must be usable on desktop and mobile.

The homepage, architecture strip, task status page, and completed artifact view should be polished enough to use as portfolio screenshots.

Use PicoCSS for base typography, forms, spacing, and simple controls. Use custom CSS only for the console theme, cards, layout, chips, task states, and small reusable components.



## Page structure

Your UI can have only 5 main pages:

```text
/
  Home page

/telegram
  Telegram bot control and explanation

/media
  Instagram / YouTube media processing UI

/music
  Spotify / YouTube music workflow UI

/tasks/{task_id}
  Generic task status and artifact viewer
```

Optionally:

```text
/about
  Architecture explanation, tech stack, limitations, screenshots
```

But I would avoid a separate about page initially. Fold the architecture explanation into the homepage and each subpage.

## Homepage layout

The homepage should be simple and memorable.

### Hero section

Top of page:

```text
Local Automation Gateway
Telegram agent, media processor, and music resolver behind one FastAPI orchestration layer.
```

Subtext:

```text
A single-machine automation system with queued execution, artifact downloads, Spotify auth flow, and API-first service boundaries.
```

Then three primary cards:

```text
[ Telegram Agent ]
Start, stop, and inspect the Telegram runtime.

[ Media Engine ]
Scrape, transcribe, OCR, and clean Instagram / YouTube media.

[ Music Engine ]
Resolve Spotify and YouTube links into downloadable audio artifacts.
```

Each card should have:

* title
* one-line value proposition
* 3 tiny capability chips
* “Open” button
* “API routes” mini text

Example:

```text
Media Engine

Extract useful text and files from Instagram and YouTube sources.

YouTube transcription
Instagram scraping
LLM cleanup

POST /api/media/youtube
POST /api/media/post
POST /api/media/private-user

[ Open Media Console ]
```

### Architecture strip

Below the three cards, add a horizontal architecture strip:

```text
Client UI
   ↓
FastAPI Gateway
   ↓
Task Queue
   ↓
music_node / media_node / i_node
   ↓
Artifacts / Results
```

This should not be a complex diagram. Use simple boxes.

This will help recruiters understand the backend quality quickly.

### Live system status

Add a tiny status panel:

```text
Gateway
Healthy

Queue
Idle

Worker
Available
```

This can call:

```text
GET /health
```

If you later expose queue metadata, you can populate queue status too.

## Navigation style

Use a sticky top nav:

```text
Sanath Automation Lab       Telegram  Media  Music  Architecture
```

Right side:

```text
GitHub
API Docs
```

The `API Docs` button can link to:

```text
/docs
```

because FastAPI already gives you Swagger.

Keep the nav thin and elegant. No giant navbar.

## Subpage pattern

Each of the three feature pages should follow the same structure:

```text
1. What this engine does
2. Try it
3. Task status
4. Artifacts / result
5. How it works technically
6. Relevant API routes
7. Current limitations
```

This consistency will make the project feel engineered, not stitched together.

## Telegram page

The Telegram page should feel like a runtime controller.

### Page title

```text
Telegram Agent
A long-running bot runtime exposed through gateway task controls.
```

### Main UI

Use a two-column layout.

Left side: controls.

```text
Status
Stopped / Running / Unknown

[ Start Agent ]
[ Stop Agent ]
[ Refresh Status ]
```

Right side: explanation card.

```text
How it works

The Telegram runtime lives inside i_node.
The gateway does not duplicate bot logic.
It wraps the runtime with task-aware start, stop, and status APIs.
```

API cards:

```text
POST /api/telegram/start
POST /api/telegram/stop
GET  /api/telegram/status
```

Important: since your README says Telegram can occupy the single-worker slot because it is long-lived, show that as a transparent technical note:

```text
Design note

Telegram currently shares the same task framework. Since it is long-lived, it can occupy the single-worker queue slot. This is acceptable for the current single-machine setup, but future versions could isolate Telegram into a separate worker.
```

That kind of honesty is attractive in a portfolio.

## Media page

This should be the richest page.

The media engine has several workflows:

* YouTube audio transcription
* public Instagram user scraping
* Instagram post scraping
* private Instagram collection scraping
* bulk Instagram processing
* clean bulk output

Do not expose all of them as one ugly form. Make it a tabbed interface using HTMX.

### Top section

```text
Media Engine
Turn social/video sources into structured, downloadable artifacts.
```

Subtext:

```text
Supports YouTube transcription, Instagram scraping, OCR, Whisper passes, and LLM cleanup through queued FastAPI jobs.
```

### Workflow selector

Use tabs or segmented buttons:

```text
YouTube
Instagram Post
Public User
Private Collection
Bulk URLs
Clean Output
```

With HTMX, clicking a tab should replace only the form area:

```html
<button hx-get="/ui/media/forms/youtube" hx-target="#media-form">
  YouTube
</button>
```

### YouTube form

```text
YouTube URL or input
[ textarea ]

Output mode
( ) Temporary artifact
( ) Persistent outdir

[ Submit Job ]
```

For recruiters, default to ephemeral mode. It is safer and cleaner.

### Instagram post form

```text
Instagram Post URL
[ input ]

[ Process Post ]
```

### Private collection form

This needs a technical note because it uses Playwright session reuse:

```text
Private collection scraping uses a stable browser session directory. First run may require login. Later runs reuse the session.
```

### After submit

Do not redirect immediately. Use HTMX to show an inline task card:

```text
Task submitted

task_id: 7f3...
status: queued
queue position: 1

[ View Task ]
```

Then poll using HTMX:

```html
<div 
  hx-get="/ui/tasks/{{ task_id }}/card"
  hx-trigger="load, every 2s"
  hx-swap="outerHTML">
</div>
```

When complete, show:

```text
Completed

Artifacts
[ transcript.txt ] [ Download ]
[ cleaned_output.json ] [ Download ]
```

## Music page

The music page should feel like a resolver and downloader.

### Page title

```text
Music Engine
Resolve Spotify and YouTube sources into local audio artifacts.
```

### Workflow selector

```text
Song Search
YouTube Link
Spotify Link
Spotify Library
```

Again, use HTMX partials for each form.

### Song search form

```text
Search query
[ input: "joji slow dancing in the dark" ]

Batch mode
[ textarea optional ]

Output mode
Temporary artifact / Persistent directory

[ Download Audio ]
```

### YouTube link form

```text
YouTube URL
[ input ]

[ Resolve and Download ]
```

### Spotify link form

```text
Spotify track / playlist URL
[ input ]

[ Resolve Through YouTube ]
```

### Spotify user library flow

This should be a guided mini-flow.

Step 1:

```text
Connect Spotify
[ Start Spotify Auth ]
```

Button calls:

```text
POST /api/music/user/auth/start
```

Response gives:

* `auth_session_id`
* `authorization_url`
* `poll_url`

Then show:

```text
1. Open Spotify authorization
2. Complete login
3. Return here
4. Waiting for authorization...
```

Use HTMX polling against your backend auth session endpoint.

Once authorized:

```text
Authorized

[ Fetch Playlists ]
```

Then list playlists as selectable cards:

```text
[ ] Liked Songs
[ ] Gym
[ ] Late Night Coding

[ Download Selected ]
```

This page can really impress because it shows you handled OAuth in a UI-friendly way instead of keeping a terminal-based flow.

## Generic task status page

This is important. Since your whole backend uses a shared task model, your frontend should also have a shared task viewer.

Route:

```text
/tasks/{task_id}
```

Display:

```text
Task
7f3c...

Service
media

Status
running

Submitted
10:42 AM

Started
10:43 AM

Queue position
0
```

Use a visual state component:

```text
Queued       Running       Completed
●────────────●────────────○
```

For failed tasks:

```text
Failed

Error
Playwright session expired. Re-authenticate and retry.
```

For completed tasks:

```text
Result

Artifacts
transcript.txt      42 KB     [Download]
summary.json        3 KB      [Download]
```

Your README already defines statuses as `queued`, `running`, `completed`, `failed`, and `cancelled`, so directly reflect those in the UI. 

## Technical explanation cards

You mentioned technical explanations may be provided as cards. Yes, that is the right choice.

Do not write long paragraphs on the main interaction surface. Use cards like this:

```text
Why queued execution?

Heavy jobs like transcription, scraping, and downloads are serialized through a single-worker FIFO queue. This protects the machine from running multiple GPU, browser, or yt-dlp jobs at once.
```

```text
What are artifacts?

When no output directory is provided, the gateway creates a temporary job directory, stores generated files there, and exposes them through task artifact endpoints.
```

```text
Why server-rendered HTML?

The UI does not need a frontend SPA. FastAPI templates plus HTMX are enough because most interactions are form submissions, polling, and partial updates.
```

Each page should have 3 to 5 explanation cards.

## UI personality

The UI copy should sound confident and technical, but not corporate.

Good:

```text
One gateway. Three engines. Local-first automation.
```

```text
Submit a job, watch the queue, download the artifacts.
```

```text
Built for a single machine, designed with real service boundaries.
```

Avoid:

```text
Revolutionary AI-powered platform
```

```text
Seamless next-generation productivity solution
```

That kind of copy sounds fake.

## Suggested design name

Give the UI a name. It makes the portfolio feel complete.

Some good options:

```text
Automation Lab
LocalOps
NodeDeck
Gateway Console
Sanath's Automation Console
```

My pick:

```text
Gateway Console
```

It matches the architecture: one gateway over multiple nodes.

## Recommended stack structure

Since you want server-rendered HTML, HTMX, minimal JS, and lightweight CSS, structure your FastAPI app like this:

```text
app_node/
  main.py
  routers/
    music.py
    media.py
    telegram.py
    ui.py
  templates/
    base.html
    index.html
    pages/
      music.html
      media.html
      telegram.html
      task.html
    partials/
      task_card.html
      artifact_list.html
      media_youtube_form.html
      media_post_form.html
      media_private_form.html
      music_song_form.html
      music_spotify_auth.html
  static/
    css/
      app.css
    js/
      app.js
```

Your `ui.py` router should only return HTML pages and partials. Your existing API routers should remain API-first.

That separation matters:

```text
/api/*
  JSON API used by Postman, Swagger, and HTMX form actions

/ui/*
  HTML partials used by HTMX

/
  rendered homepage
```

## HTMX interaction model

Use HTMX for four things only:

1. Loading forms dynamically
2. Submitting jobs
3. Polling task status
4. Refreshing artifact lists

Avoid using HTMX for everything. Keep it disciplined.

Example interaction:

```text
User submits media URL
        ↓
HTMX posts form to /ui/media/youtube/submit
        ↓
Server calls internal API/service function
        ↓
Returns task_card.html
        ↓
task_card polls /ui/tasks/{task_id}/card
        ↓
When completed, card includes artifact downloads
```

This keeps frontend complexity very low.

## CSS approach

Use plain CSS first. You do not need Tailwind unless you already like it.

Create a small design system:

```css
:root {
  --bg: #0b0d10;
  --panel: #11151a;
  --panel-soft: #151a21;
  --text: #f4f7fa;
  --muted: #8a94a3;
  --border: #252c35;
  --accent: #7c5cff;
  --danger: #ff5c7a;
  --success: #3ddc97;
}
```

Use reusable classes:

```text
.shell
.nav
.hero
.grid-3
.card
.card-title
.muted
.button
.button-secondary
.input
.textarea
.status-pill
.api-chip
```

This gives you a consistent UI without a component framework.

## Homepage wireframe

```text
----------------------------------------------------
Gateway Console                         Docs  GitHub
----------------------------------------------------

Local Automation Gateway

Telegram agent, media processor, and music resolver
behind one FastAPI orchestration layer.

[ Open API Docs ] [ View Architecture ]

----------------------------------------------------

[ Telegram Agent ]
Runtime control for your Telegram bot.
Start / Stop / Status
POST /api/telegram/start
[ Open ]

[ Media Engine ]
Scrape, transcribe, OCR, and clean media sources.
YouTube / Instagram / LLM cleanup
POST /api/media/youtube
[ Open ]

[ Music Engine ]
Resolve Spotify and YouTube sources into audio.
Spotify Auth / yt-dlp / Artifacts
POST /api/music/yt
[ Open ]

----------------------------------------------------

Architecture

Client UI → FastAPI Gateway → Queue → Nodes → Artifacts

----------------------------------------------------

Why this project is interesting

[ Unified API Gateway ]
[ Single-worker Queue ]
[ Ephemeral Artifacts ]
[ Spotify Browser Auth ]
[ Local-first Processing ]
[ Server-rendered UI ]
```

## Media page wireframe

```text
Media Engine

Turn YouTube and Instagram sources into structured files.

[ YouTube ] [ Post ] [ Public User ] [ Private Collection ] [ Bulk ]

----------------------------------------------------
Form panel

YouTube URL
[________________________________]

Output mode
[x] Temporary artifacts
[ ] Persistent outdir

[ Submit Job ]
----------------------------------------------------

Task panel

Status: running
Queue: 0
Started: 10:42 AM

Polling every 2 seconds...

----------------------------------------------------

Technical notes

[ Input normalization ]
Supports direct input, multiple inputs, .txt files, and .json files.

[ Artifact mode ]
Omitting outdir creates downloadable temporary outputs.

[ Processing stack ]
Whisper, OCR, scraping, and LLM cleaning are orchestrated behind the gateway.
```

## Music page wireframe

```text
Music Engine

Resolve tracks, links, playlists, and YouTube sources into audio files.

[ Song Search ] [ YouTube ] [ Spotify Link ] [ Spotify Library ]

----------------------------------------------------
Song Search

Query
[________________________________]

Batch queries
[________________________________]
[________________________________]

Output mode
[x] Temporary artifacts

[ Submit Download Job ]
----------------------------------------------------

Spotify Library

[ Start Spotify Auth ]

Waiting for authorization...
```

## Telegram page wireframe

```text
Telegram Agent

Control the Telegram runtime through the same gateway task model.

----------------------------------------------------

Current status
Stopped

[ Start Agent ] [ Stop Agent ] [ Refresh ]

----------------------------------------------------

How this is wired

[ i_node owns runtime ]
Telegram logic stays inside i_node.

[ app_node owns controls ]
The gateway exposes start, stop, and status routes.

[ Known tradeoff ]
The long-lived Telegram task can occupy the single-worker queue.
```

## What to build first

Build in this order:

### Phase 1: Static shell

Create:

```text
base.html
index.html
app.css
```

Get the homepage looking excellent before wiring functionality.

### Phase 2: UI router

Add:

```text
app_node/routers/ui.py
```

Routes:

```text
GET /
GET /music
GET /media
GET /telegram
GET /tasks/{task_id}
```

### Phase 3: HTMX partials

Add form partials:

```text
GET /ui/media/forms/youtube
GET /ui/media/forms/post
GET /ui/music/forms/song
GET /ui/music/forms/spotify-auth
```

### Phase 4: Job submission

Add HTML submission endpoints:

```text
POST /ui/media/youtube/submit
POST /ui/music/song/submit
POST /ui/telegram/start
```

These should return rendered HTML task cards, not JSON.

### Phase 5: Polling

Add:

```text
GET /ui/tasks/{task_id}/card
```

This route should internally check task state and return the right HTML.

### Phase 6: Artifacts

Add artifact list partial:

```text
GET /ui/tasks/{task_id}/artifacts
```

Render download buttons using existing artifact URLs:

```text
/api/tasks/{task_id}/artifacts/{artifact_id}
```

## Best creative touch

Add a small “execution timeline” to every completed task.

Example:

```text
Submitted     10:42:11
Started       10:42:13
Finished      10:43:02
Duration      49s
Mode          Ephemeral artifacts
```

This makes the app feel serious and observable without building a full monitoring dashboard.

## Another strong touch

On every subpage, add a small “API equivalent” collapsible card.

Example on Music:

```text
API equivalent

POST /api/music/song

{
  "queries": ["daft punk instant crush"],
  "outdir": null
}
```

This is excellent for recruiters and backend reviewers because it shows that the UI is not hiding the API. It is a clean layer over it.

## Final product description

The UI should feel like this:

> Gateway Console is a minimal server-rendered interface for a local automation backend. The homepage presents three engines, Telegram, Media, and Music, as clean interactive cards. Each engine page exposes only the workflows that matter, submits jobs through the FastAPI gateway, shows live queue-aware task status using HTMX polling, and surfaces generated files as downloadable artifacts. Technical explanations are shown as compact cards beside the interaction flow, so recruiters can both test the system and understand the architecture without reading the whole codebase.

