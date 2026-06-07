# App Node Implementation Plan

## Goal

This plan covers the next API evolution for `app_node` with five concrete goals:

1. Queue heavy requests so only one processing job runs at a time.
2. Make omitted `outdir` consistently mean ephemeral output mode.
3. Standardize payload-vs-file input handling across relevant endpoints.
4. Extend singular music inputs to batch-capable variants.
5. Redesign Spotify user auth and playlist selection so it works cleanly from Postman now and UI later.

The priority is the simplest design that solves the actual problem on one machine without overengineering.


## Current State Summary

- `app_node` already has a task model and polling endpoint.
- Long-running routes already return `202 Accepted`.
- Task polling now distinguishes in-progress vs completed work.
- Media/music job functions now return structured summaries.

What is still missing:

- there is no real queue, only background execution
- there is no universal artifact/download model for ephemeral outputs
- input contracts differ across endpoints
- some routes only support singular inputs
- Spotify auth is still CLI-driven and log-driven, not API-driven


## Design Principles

- Keep one in-process orchestrator.
- Avoid Redis, Celery, websockets, and distributed infrastructure.
- Prefer one consistent contract over many route-specific exceptions.
- Preserve current routes where possible and evolve them compatibly.
- Build for Postman first, but make the same model UI-friendly.


## Phase 1: Central Job Orchestrator

### Objective

Replace "fire background task immediately" with "submit job to a central queue manager".

### Why

Your hardware only supports one heavy request at a time. The root problem is compute scheduling, not just HTTP request rate.

### Proposed Module

Create an application-level service, for example:

- `app_node/job_manager.py`

Responsibilities:

- accept submitted jobs
- assign job ids
- store queue state
- run exactly one heavy job at a time
- expose queue position
- update task/job status lifecycle
- support cancellation where possible

### Job States

Use these statuses everywhere:

- `queued`
- `waiting_for_user`
- `running`
- `completed`
- `failed`
- `cancelled`

### Internal Model

Each job should store:

- `id`
- `service`
- `status`
- `created_at`
- `started_at`
- `finished_at`
- `queue_position`
- `error`
- `result`
- `submitted_by`
- `artifacts`
- `meta`

### Execution Policy

- heavy-job concurrency: `1`
- queue policy: FIFO
- queue size: configurable, default something small like `10`

### Endpoint Behavior

All heavy routes should:

- enqueue a job
- return `202 Accepted`
- include:
  - `task_id`
  - `status`
  - `queue_position`
  - `poll_url`

### Polling Behavior

`GET /api/tasks/{task_id}`

- `202` if `queued`, `waiting_for_user`, or `running`
- `200` if `completed`, `failed`, or `cancelled`

### Lightweight vs Heavy Work

Treat these as heavy:

- media processing routes
- music download/resolve routes
- Spotify playlist fetch if it triggers auth or substantial remote work

Telegram agent can remain special-cased if needed, but ideally it should also be visible through the same status model.


## Phase 2: HTTP Rate Limiting

### Objective

Protect the API from abuse without confusing rate limiting with job scheduling.

### Proposed Design

Add a simple middleware or dependency-based limit for submission endpoints only.

Good starting policy:

- per IP: `10` create-job requests per minute
- queue-full response: `429` or `503`

### Important Separation

- rate limiting controls how often users may submit
- queueing controls when compute-heavy jobs actually run

Do not rely on middleware alone for the "one job at a time" requirement.


## Phase 3: Standard Output Mode

### Objective

Make all `outdir`-based routes support both:

- persistent mode
- ephemeral mode

### Contract

If `outdir` is provided:

- `output_mode = "persistent"`

If `outdir` is omitted:

- `output_mode = "ephemeral"`

### Ephemeral Design

When ephemeral:

- create a job-scoped temp directory
- write outputs there
- register generated files as job artifacts
- expose download URLs
- delete files after a TTL

### Proposed Artifact Structure

Each completed job may return:

```json
{
  "artifacts": [
    {
      "artifact_id": "audio-1",
      "name": "track.mp3",
      "content_type": "audio/mpeg",
      "download_url": "/api/tasks/{task_id}/artifacts/audio-1"
    }
  ],
  "expires_at": "2026-05-16T17:00:00Z"
}
```

### New Endpoints

- `GET /api/tasks/{task_id}/artifacts`
- `GET /api/tasks/{task_id}/artifacts/{artifact_id}`

### Scope

Apply this to all endpoints that currently accept or derive output directories:

- `media/youtube`
- `media/public-user`
- `media/post`
- `media/ig-bulk`
- `media/private-user`
- `media/clean-bulk`
- `music/yt`
- `music/song`
- `music/link`
- `music/user/download`

### Internal Rule

Routers should not each invent their own temp behavior. They should ask one shared utility for:

- persistent path resolution
- ephemeral path resolution
- artifact registration
- expiry cleanup


## Phase 4: Standard Input Source Model

### Objective

Support direct payload input and file-backed input in a consistent way.

### Problem Today

Some endpoints take a string, some take arrays, and some implicitly accept a local txt path through an overloaded field.

### Proposed Contract

For single-input routes:

- `input`
- or `input_file`

For multi-input routes:

- `items`
- or `input_file`

Exactly one source must be provided.

### Examples

Single value:

```json
{
  "input": "https://youtu.be/example"
}
```

or

```json
{
  "input_file": "./data/urls.txt"
}
```

Multi value:

```json
{
  "items": [
    "https://open.spotify.com/playlist/1",
    "https://open.spotify.com/playlist/2"
  ]
}
```

or

```json
{
  "input_file": "./data/spotify_links.txt"
}
```

### Shared Utility

Create a small helper module, for example:

- `app_node/input_resolver.py`

Responsibilities:

- validate only one source was provided
- read txt/json input files
- normalize to a list of strings
- strip blanks
- reject empty final input

### File Parsing Rules

For `.txt`:

- one item per line
- ignore blank lines

For `.json`:

- accept a list of strings

Keep it simple. Avoid trying to infer too many file formats.


## Phase 5: Batch-Capable Music Inputs

### Objective

Extend singular music operations without breaking existing clients.

### Current Routes

- `POST /api/music/song`
- `POST /api/music/link`

### Proposed Evolution

Keep the routes, but extend the request schemas:

For song:

- `query`
- or `queries`
- or `input_file`

For link:

- `url`
- or `urls`
- or `input_file`

### Internal Normalization

Normalize all accepted input forms into a list.

Examples:

- one query -> list of one item
- many queries -> list
- file -> list

### Processing Model

One API request should create one job.

That job processes its normalized list sequentially and returns per-item results.

### Result Shape

```json
{
  "message": "Completed batch song download",
  "items": [
    {
      "input": "Bohemian Rhapsody Queen",
      "status": "completed",
      "resolved_url": "..."
    },
    {
      "input": "Blinding Lights The Weeknd",
      "status": "failed",
      "error": "No match found"
    }
  ],
  "stats": {
    "submitted_count": 2,
    "completed_count": 1,
    "failed_count": 1
  }
}
```

### Why This Is Better

- compatible with current routes
- efficient for one-machine processing
- easy to show in UI later
- avoids spawning many tiny concurrent tasks


## Phase 6: Spotify Auth Redesign

### Objective

Replace CLI/log-based OAuth prompts with an explicit API interaction flow.

### Current Problem

The current user playlist mode depends on terminal/browser text prompts from Spotipy. That is not suitable for Postman or frontend UI.

### Design Direction

Split Spotify auth from playlist fetch/download.

Do not bury OAuth inside a background job.

### Proposed Endpoints

1. Start auth session

- `POST /api/music/user/auth/start`

Response:

```json
{
  "auth_session_id": "abc123",
  "status": "waiting_for_user",
  "authorization_url": "https://accounts.spotify.com/authorize?...",
  "poll_url": "/api/music/user/auth/abc123"
}
```

2. Spotify callback

- `GET /api/music/user/auth/callback`

Responsibilities:

- validate state
- exchange code for token
- store token
- mark auth session as complete

3. Poll auth session

- `GET /api/music/user/auth/{auth_session_id}`

Response examples:

- `waiting_for_user`
- `authorized`
- `failed`

4. Fetch user playlists

- `POST /api/music/user/playlists`

5. Download selected playlists

- `POST /api/music/user/download`

### Why Separate Auth From Fetch

- Postman can use it
- frontend can use it
- logs are no longer part of the user experience
- future auth flows can follow the same interaction model

### Future-Friendly Status

The orchestrator should support `waiting_for_user` not only for Spotify, but for any future multi-step workflows.


## Phase 7: API Contract Standardization

### Submission Response

All async job-starting routes should return:

```json
{
  "task_id": "uuid",
  "status": "queued",
  "queue_position": 1,
  "poll_url": "/api/tasks/uuid"
}
```

### Poll Response While In Progress

```json
{
  "id": "uuid",
  "service": "media.public_user",
  "status": "running",
  "queue_position": 0,
  "error": null,
  "result": null
}
```

### Poll Response On Completion

```json
{
  "id": "uuid",
  "service": "media.public_user",
  "status": "completed",
  "error": null,
  "result": {
    "message": "Completed public user scrape",
    "input": {
      "username": "gingerpotter21",
      "first_n": 3
    },
    "output": {
      "output_mode": "ephemeral"
    },
    "artifacts": [],
    "stats": {
      "fetched_item_count": 3
    }
  }
}
```

### Validation Rules

Across all routes:

- reject ambiguous input combinations
- reject empty lists after normalization
- reject missing required interaction state
- always return structured errors


## Proposed Internal Modules

Keep the implementation small and local to `app_node`.

Suggested additions:

- `app_node/job_manager.py`
- `app_node/artifacts.py`
- `app_node/input_resolver.py`
- `app_node/auth_sessions.py`

Suggested responsibilities:

- `job_manager.py`: queue, worker slot, status transitions
- `artifacts.py`: temp dirs, artifact metadata, download lookup, cleanup
- `input_resolver.py`: inline/file normalization
- `auth_sessions.py`: Spotify auth session state

This is enough structure without fragmenting the codebase too much.


## Backward Compatibility Plan

### Preserve Existing Routes

Keep these routes:

- `/api/music/song`
- `/api/music/link`
- `/api/music/yt`
- `/api/music/user/playlists`
- `/api/music/user/download`
- `/api/media/*`

### Extend Schemas, Do Not Break Them

- existing singular fields should still work
- new plural/file fields should be additive
- current Postman calls should remain valid

### Spotify Exception

The Spotify user flow should be adjusted more carefully because it is currently not API-correct. This is the one area where behavior should intentionally change.


## Rollout Order

Recommended order of implementation:

1. Build the central job queue and update task lifecycle to support `queued`.
2. Add artifact management and ephemeral output download endpoints.
3. Standardize input source parsing for file-backed and inline inputs.
4. Extend music `song` and `link` to batch-capable processing.
5. Redesign Spotify auth into explicit API sessions.
6. Update `endpoints.txt` with the new request/response contracts.

This order reduces regression risk because infrastructure comes before feature expansion.


## Testing Strategy

### Queueing

- submit one heavy request -> runs immediately
- submit second heavy request -> enters `queued`
- verify queue position changes after first completes

### Ephemeral Output

- omit `outdir`
- verify artifact metadata returned
- verify download endpoint works
- verify cleanup after TTL

### Input Resolution

- inline payload works
- txt file works
- json file works
- both input and input_file together fail validation

### Batch Music

- one query works exactly as before
- multiple queries produce one job with per-item results
- partial failures are reported cleanly

### Spotify

- auth session start returns authorization URL
- callback completes auth
- playlist fetch works after auth
- download works after selection


## Final Recommendation

The cleanest implementation is a small, in-process orchestration layer that standardizes:

- how jobs are queued
- how outputs are exposed
- how inputs are normalized
- how user interaction steps are modeled

That gives you a stable platform for Postman now and a UI later, without introducing unnecessary infrastructure.
