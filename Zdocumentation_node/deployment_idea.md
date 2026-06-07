We decided to postpone solving “true production persistence” and instead optimize for:

```text
Free public deployment + recruiter-friendly demo + low operational complexity
```

The main conclusions were:

---

# 1. Keep the current architecture

We decided to keep:

```text
FastAPI
Jinja2
HTMX
Minimal JavaScript
Lightweight CSS
```

because it matches your actual project characteristics:

* backend-centric
* API-first
* task/job oriented
* queue-based workflows
* artifact downloads
* not a frontend-heavy SaaS product

A React/Vue SPA would add complexity without improving the recruiter experience.

The current architecture is better because:

* simpler deployment
* faster development
* easier debugging
* fewer moving parts
* easier recruiter comprehension
* easier HTMX integration
* cleaner backend ownership

---

# 2. Build a “control-room portfolio UI”

We decided the UI should feel like:

```text
A lightweight orchestration console over a real backend system
```

instead of:

```text
A startup landing page
```

or:

```text
A CRUD admin dashboard
```

The reasoning:

Recruiters and technical interviewers care more about:

* architecture clarity
* workflow visibility
* system thinking
* API design
* orchestration quality
* backend integration
* observability

than flashy frontend animations.

So the UI direction became:

```text
Minimal terminal-energy
Recruiter-friendly
Technical but understandable
```

---

# 3. Public deployment should initially be “demo-first”

This was the biggest architectural conclusion.

Your backend contains workflows that are:

* long-running
* credential-dependent
* browser-session dependent
* resource-heavy
* potentially unsafe publicly

Examples:

```text
Telegram runtime
Spotify auth
Private Instagram scraping
yt-dlp downloads
Playwright session reuse
persistent output directories
```

So we concluded:

```text
Do NOT initially expose unrestricted real execution publicly.
```

Instead:

```text
Build a recruiter-safe public demo.
```

Meaning:

* real workflows where safe
* seeded/demo outputs where needed
* disabled local-only features where risky
* transparent capability indicators
* architecture visibility preserved

This keeps the UI reliable and professional.

---

# 4. Render Free was chosen as the likely first deployment target

We discussed several deployment options.

## Chosen direction

Most likely:

```text
Render Free Web Service
```

because it:

* supports FastAPI directly
* is simple to deploy
* supports Python well
* supports GitHub deploys
* provides HTTPS automatically
* works fine with server-rendered apps
* fits HTMX architecture naturally

---

# 5. We accepted Render’s limitations temporarily

We identified the major Render Free constraints:

## Cold starts

Free services sleep after inactivity.

Result:

```text
First request may take time.
```

We accepted this because:

* recruiter traffic is low
* portfolio/demo apps tolerate cold starts
* simplicity matters more right now

---

## Ephemeral filesystem

Render Free does not guarantee persistent local disk storage.

That means:

```text
SQLite persistence is unsafe long-term on free Render.
```

This was the critical deployment conclusion.

---

# 6. SQLite is acceptable for now, but only as demo/local persistence

We concluded:

```text
SQLite is fine during development and for local execution.
```

But for free public deployment:

```text
SQLite should initially be treated as disposable/demo storage.
```

Why:

Render Free can restart/redeploy/spin down.

So:

* uploaded/generated data may disappear
* task history may reset
* persistent artifacts are unreliable

We accepted this temporarily because your immediate goal is:

```text
Showcase the architecture and workflows to recruiters
```

not:

```text
Run a production SaaS system
```

---

# 7. Real persistence was postponed intentionally

We explicitly decided:

```text
Do not solve production-grade persistence yet.
```

because:

* it adds unnecessary complexity right now
* it is not required for portfolio value
* the UI implementation itself is higher priority

We said persistence can later evolve into:

```text
FastAPI + external Postgres
```

using:

```text
Supabase Free
or
Neon Free
```

if needed.

But we intentionally avoided adding:

* cloud databases
* auth systems
* production infra
* object storage
* CDN layers
* worker clusters

before the UI itself exists.

---

# 8. The deployment philosophy became “portfolio-first”

This is the final core conclusion.

The project is currently optimized for:

```text
Showing engineering quality
```

not:

```text
Operating a scalable internet platform
```

So every deployment decision was filtered through:

```text
Will this help recruiters understand the system quickly?
```

That led to these choices:

| Decision                    | Reason                                       |
| --------------------------- | -------------------------------------------- |
| Server-rendered HTML        | Simpler and clearer                          |
| HTMX                        | Dynamic enough without SPA complexity        |
| Minimal JS                  | Lower maintenance                            |
| FastAPI templates           | Natural fit                                  |
| Render Free                 | Simplest zero-cost deployment                |
| Demo-safe execution         | Avoid public instability                     |
| SQLite for now              | Good enough temporarily                      |
| Shared task viewer          | Shows architecture maturity                  |
| Technical explanation cards | Helps recruiters understand backend thinking |
| Architecture strip          | Communicates system design fast              |

---

# Final deployment direction

What we effectively decided is:

```text
Phase 1
-------
Build a polished recruiter-facing UI
using FastAPI + Jinja2 + HTMX.

Deploy cheaply/free using Render.

Treat persistence as non-critical initially.

Focus on:
- architecture clarity
- workflow visibility
- task orchestration
- artifact flow
- system design quality

NOT:
- production scaling
- multi-user persistence
- enterprise infra
- complex frontend frameworks
```

Then later:

```text
Phase 2
-------
If needed:
- move persistence to Postgres
- improve deployment infra
- add safer public execution
- separate worker/runtime services
- harden long-running workflows
```
