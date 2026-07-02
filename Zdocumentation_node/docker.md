# Docker, WSL, Docker Compose, and Debugging Notes

These notes summarize useful software engineering concepts learned while working with Docker inside WSL, Docker Compose, container logs, development vs stable Compose files, and basic runtime debugging.

---

# 1. WSL Basics

## Starting WSL from PowerShell

To open the default WSL distribution:

```powershell
wsl
```

To list installed WSL distributions:

```powershell
wsl -l -v
```

Example output:

```text
NAME            STATE           VERSION
Ubuntu-26.04    Running         2
```

To start a specific distribution:

```powershell
wsl -d Ubuntu-26.04
```

Important: this is correct:

```powershell
wsl -d Ubuntu-26.04
```

This is incorrect:

```powershell
wsl Ubuntu-26.04
```

Because WSL treats `Ubuntu-26.04` as a Linux command to run inside the default distro, not as the distro name.

---

# 2. Windows Paths Inside WSL

Windows drives are mounted inside WSL under `/mnt`.

A Windows path like:

```text
C:\Users\ADMIN\Desktop\Projects\me\Code
```

becomes:

```bash
/mnt/c/Users/ADMIN/Desktop/Projects/me/Code
```

To switch into that folder inside Ubuntu/WSL:

```bash
cd /mnt/c/Users/ADMIN/Desktop/Projects/me/Code
```

To verify the current directory:

```bash
pwd
```

To list files:

```bash
ls
```

---

# 3. Stopping and Restarting WSL

To stop all WSL distributions:

```powershell
wsl --shutdown
```

There is no command like:

```powershell
wsl --force shutdown
```

That is invalid.

To stop only one WSL distro:

```powershell
wsl --terminate Ubuntu-26.04
```

or:

```powershell
wsl -t Ubuntu-26.04
```

## Important Docker warning

If Docker Engine is installed inside WSL, then shutting down WSL also stops Docker and all running containers inside it.

So before running:

```powershell
wsl --shutdown
```

check whether containers are running:

```bash
docker ps
```

If needed, stop containers gracefully before shutting down WSL:

```bash
docker stop <container_name_or_id>
```

---

# 4. Docker Containers vs Docker Images

A Docker image is like a blueprint/template.

A Docker container is a running or stopped instance created from an image.

Example:

```text
IMAGE          NAMES
code-gateway   code-gateway-1
```

Here:

```text
code-gateway
```

is the image name.

```text
code-gateway-1
```

is the container name.

So this may fail:

```bash
docker start code-gateway
```

because `code-gateway` is the image, not the container.

Correct:

```bash
docker start code-gateway-1
```

---

# 5. Checking Running and Stopped Containers

To see only running containers:

```bash
docker ps
```

To see all containers, including stopped ones:

```bash
docker ps -a
```

Example:

```text
CONTAINER ID   IMAGE          STATUS                      NAMES
305416abbf90   code-gateway   Exited (137) 1 minute ago   code-gateway-1
```

## Exit code 137

Exit code `137` usually means the container was killed. Common causes:

* WSL was shut down.
* Docker was stopped.
* The process was killed due to memory pressure.
* The system forcibly terminated the container.

If WSL was restarted, an exit code 137 is expected.

---

# 6. Starting Containers

Start a stopped container:

```bash
docker start <container_name_or_id>
```

Example:

```bash
docker start code-gateway-1
```

Check if it is running:

```bash
docker ps
```

---

# 7. Viewing Container Logs

To view logs of a running or stopped container:

```bash
docker logs <container_name>
```

Example:

```bash
docker logs code-gateway-1
```

To follow logs live:

```bash
docker logs -f code-gateway-1
```

To show only the last 100 lines:

```bash
docker logs --tail 100 code-gateway-1
```

To include timestamps:

```bash
docker logs -f --timestamps code-gateway-1
```

To exit live logs:

```text
Ctrl + C
```

When using `docker logs -f`, pressing `Ctrl + C` stops watching logs. It does not stop the container.

---

# 8. Docker Attach vs Docker Logs

If using:

```bash
docker logs -f <container>
```

then:

```text
Ctrl + C
```

stops log streaming.

If using:

```bash
docker attach <container>
```

then use:

```text
Ctrl + P
Ctrl + Q
```

to detach without stopping the container.

This distinction matters because `docker attach` connects directly to the container process, while `docker logs -f` only follows the log output.

---

# 9. Docker Compose Basics

Docker Compose lets you define and run multi-container applications using YAML files.

Common command:

```bash
docker compose up
```

This starts services defined in `docker-compose.yml`.

To run in the background:

```bash
docker compose up -d
```

To stop Compose services:

```bash
docker compose down
```

To see Compose services:

```bash
docker compose ps
```

To view Compose logs:

```bash
docker compose logs -f
```

To view logs for a specific service:

```bash
docker compose logs -f gateway
```

---

# 10. Using Multiple Compose Files

Command:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Meaning:

* Use `docker-compose.yml` as the base config.
* Apply `docker-compose.dev.yml` on top of it.
* Build images if needed.
* Start the containers.
* Keep logs attached in the terminal.

The second file overrides or extends the first file.

So:

```bash
-f docker-compose.yml -f docker-compose.dev.yml
```

means:

```text
base compose file + development override file
```

To inspect the final merged Compose config:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml config
```

To list final service names:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml config --services
```

Each service usually becomes one container.

---

# 11. Foreground vs Background Compose

This runs in the foreground:

```bash
docker compose up
```

Logs appear directly in the terminal.

If you press:

```text
Ctrl + C
```

Compose may stop the containers.

This runs in the background:

```bash
docker compose up -d
```

The terminal is released, and containers keep running.

To view logs later:

```bash
docker compose logs -f
```

---

# 12. Development Compose vs Stable Compose

A development Compose file often has:

```yaml
volumes:
  - .:/app
```

This means:

```text
local project folder -> container /app
```

So the container uses your live local code.

If you edit files on your machine, the container immediately sees those changes.

Development Compose may also use:

```yaml
command:
  [
    "python",
    "-m",
    "uvicorn",
    "app_node.main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
    "--reload"
  ]
```

The important part is:

```bash
--reload
```

This tells Uvicorn to auto-restart when code changes.

## Dev Compose usually means:

```text
Run using local source code.
Auto-reload when files change.
Good for active development.
```

---

# 13. Stable Compose

A stable Compose file may not include:

```yaml
volumes:
  - .:/app
```

If it does not mount local code, then the container runs the code that was copied into the image during Docker build.

Stable Compose may use:

```yaml
command:
  [
    "python",
    "-m",
    "uvicorn",
    "app_node.main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8000"
  ]
```

Notice there is no:

```bash
--reload
```

## Stable Compose usually means:

```text
Run from built Docker image.
No live local code binding.
No auto-reload.
More production-like.
```

---

# 14. Important Difference Between Dev and Stable

Development:

```yaml
volumes:
  - .:/app
```

Stable:

```yaml
# no local code mount
```

That means:

| Concept                           | Dev Compose | Stable Compose |
| --------------------------------- | ----------- | -------------- |
| Uses local code directly          | Yes         | Usually no     |
| Auto reload                       | Usually yes | Usually no     |
| Good for coding                   | Yes         | No             |
| Good for stable/prod-like run     | No          | Yes            |
| Changes visible immediately       | Yes         | No             |
| Requires rebuild for code changes | Usually no  | Yes            |

---

# 15. Docker Compose Build Command

Command:

```bash
docker compose -f docker-compose.yml -f docker-compose.stable.yml build --no-cache gateway
```

Meaning:

```text
Build only the gateway service image using the base compose file plus stable override file, and do not use Docker cache.
```

Breakdown:

```bash
docker compose
```

Use Docker Compose.

```bash
-f docker-compose.yml
```

Use the base Compose file.

```bash
-f docker-compose.stable.yml
```

Apply the stable override file.

```bash
build
```

Build the image, but do not start the container.

```bash
--no-cache
```

Ignore previous cached Docker layers and rebuild everything from scratch.

```bash
gateway
```

Build only the `gateway` service.

Important: this command only builds. It does not run the container.

To run after building:

```bash
docker compose -f docker-compose.yml -f docker-compose.stable.yml up -d gateway
```

Or build and run together:

```bash
docker compose -f docker-compose.yml -f docker-compose.stable.yml up -d --build gateway
```

---

# 16. Docker Build Cache

Docker normally caches build steps.

Example Dockerfile steps:

```dockerfile
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
```

If nothing changed, Docker may reuse previous layers to build faster.

Using:

```bash
--no-cache
```

forces Docker to rerun every build step.

This is useful when:

* dependencies are stale
* cached layers are causing confusing behavior
* you want a clean build
* the app works locally but not in the image

But it is slower.

---

# 17. Ports in Docker

Example from `docker ps`:

```text
0.0.0.0:6080->6080/tcp
0.0.0.0:8000->8000/tcp
```

This means:

```text
host port 6080 -> container port 6080
host port 8000 -> container port 8000
```

So from the browser, you can access:

```text
http://localhost:8000
```

or:

```text
http://localhost:6080
```

In this project:

* `8000` is likely the FastAPI/Uvicorn app.
* `6080` is likely the noVNC browser UI.

---

# 18. noVNC / Virtual Browser Setup

The logs showed:

```text
Starting virtual display on DISPLAY=:99
Virtual browser stack started:
DISPLAY=:99
noVNC=http://localhost:6080/vnc.html?autoconnect=true&resize=scale
```

This means the container starts a virtual graphical environment.

Key concepts:

```text
DISPLAY=:99
```

means GUI apps inside Linux are using a virtual display.

```text
noVNC
```

lets you access that virtual GUI/browser through a web browser.

Typical URL:

```text
http://localhost:6080/vnc.html?autoconnect=true&resize=scale
```

This is useful when a Dockerized app needs a browser for login, scraping, automation, or visual interaction.

---

# 19. Understanding Runtime Errors in Logs

A key error appeared:

```text
NameError: name 'INSTAGRAM_AUTH_TASK_COOKIE' is not defined
```

This is a Python application error, not a Docker error.

It means the code tried to use a variable called:

```python
INSTAGRAM_AUTH_TASK_COOKIE
```

but Python could not find it in the current scope.

Possible causes:

* the variable was never defined
* it was defined in another file but not imported
* there is a typo in the variable name
* a refactor removed or renamed it
* dev/stable Compose is using a different version of code than expected

To search for the variable:

```bash
grep -R "INSTAGRAM_AUTH_TASK_COOKIE" -n app_node
```

To search for similar constants:

```bash
grep -R "AUTH_TASK_COOKIE" -n app_node
```

A `NameError` means the app started successfully, but a specific endpoint failed when that code path was executed.

---

# 20. Docker Is Not Always the Problem

When something fails inside a container, separate the layers:

## Docker/container layer

Examples:

* container not running
* port not mapped
* image not built
* volume not mounted
* command failed
* container exited

Check with:

```bash
docker ps
docker ps -a
docker logs <container>
```

## Application layer

Examples:

* Python `NameError`
* FastAPI 500 error
* missing environment variable
* dependency issue
* route handler bug

Check with:

```bash
docker logs -f <container>
```

and read the traceback.

In the observed case, Docker worked. The app returned:

```text
500 Internal Server Error
```

because of a Python `NameError`.

---

#

---

# 22. Environment Variables in Compose

Compose can define environment variables:

```yaml
environment:
  DISPLAY: ":99"
  NOVNC_ENABLED: "true"
  INSTAGRAM_AUTH_BROWSER_ENABLED: "true"
  INSTAGRAM_AUTH_TIMEOUT_SECONDS: "300"
```

These are passed into the container.

They can control app behavior without changing code.

Example:

```yaml
INSTAGRAM_AUTH_BROWSER_ENABLED: "true"
```

may enable a browser-based Instagram login flow.

Example:

```yaml
HF_HOME: "/app/data/model_cache/huggingface"
HF_HUB_CACHE: "/app/data/model_cache/huggingface/hub"
```

sets Hugging Face model cache locations.

---

# 23. env_file in Compose

Example:

```yaml
env_file:
  - path: .env.docker
    required: false
```

This means Docker Compose loads environment variables from `.env.docker`.

Because:

```yaml
required: false
```

the file is optional. If it does not exist, Compose will not fail.

This is useful for local secrets/configuration.

---

# 24. Volumes in Compose

Example:

```yaml
volumes:
  - .:/app
  - gateway-data:/app/data
  - gateway-downloads:/app/downloads
```

Meaning:

```yaml
- .:/app
```

Mount current local project folder into the container at `/app`.

```yaml
- gateway-data:/app/data
```

Create/use a named Docker volume called `gateway-data` and mount it at `/app/data`.

```yaml
- gateway-downloads:/app/downloads
```

Create/use a named Docker volume called `gateway-downloads` and mount it at `/app/downloads`.

## Types of volumes

Bind mount:

```yaml
- .:/app
```

Connects a local host folder to the container.

Named volume:

```yaml
- gateway-data:/app/data
```

Managed by Docker and persists data across container restarts/recreates.

---

# 25. Docker Compose Service Naming

If the Compose project folder is named `code` and the service is `gateway`, the container may be named:

```text
code-gateway-1
```

In logs, Compose may show:

```text
gateway-1
```

Container names often follow this pattern:

```text
<project>-<service>-<number>
```

or in older formats:

```text
<project>_<service>_<number>
```

The service name is:

```text
gateway
```

The image name may be:

```text
code-gateway
```

The container name may be:

```text
code-gateway-1
```

These are related but not identical.

---

# 26. Useful Debugging Commands

## Docker

```bash
docker ps
```

Show running containers.

```bash
docker ps -a
```

Show all containers.

```bash
docker logs -f <container>
```

Follow logs.

```bash
docker start <container>
```

Start stopped container.

```bash
docker stop <container>
```

Stop running container.

```bash
docker inspect <container>
```

Show detailed container configuration.

```bash
docker exec -it <container> bash
```

Open a shell inside a running container.

If bash is unavailable:

```bash
docker exec -it <container> sh
```

---

## Docker Compose

```bash
docker compose ps
```

Show Compose containers.

```bash
docker compose logs -f
```

Follow logs for all services.

```bash
docker compose logs -f gateway
```

Follow logs for one service.

```bash
docker compose config
```

Show final merged Compose config.

```bash
docker compose config --services
```

List services.

```bash
docker compose up -d
```

Start in background.

```bash
docker compose down
```

Stop and remove Compose containers/network.

```bash
docker compose build --no-cache gateway
```

Clean-build one service.

---

#

---

# 28. Florence-2 Model Resource Notes

Florence-2 has multiple model sizes.

Approximate practical understanding:

```text
Florence-2-base = smaller, easier to run
Florence-2-large = larger, better accuracy, more RAM/VRAM needed
```

General estimates:

```text
Florence-2-base: roughly 2–4 GB minimum, 4–6 GB comfortable
Florence-2-large: roughly 6–8 GB minimum, 8–12 GB comfortable
```

When running inside WSL/Docker, system RAM should be higher because the OS, Docker, Python, browser automation, and model runtime all consume memory.

Practical recommendation:

```text
Base model: at least 8 GB system RAM
Large model: 16 GB+ system RAM preferred
```

Engineering lesson:

```text
Model parameter count is not the only memory cost.
Runtime, framework overhead, image tensors, batch size, precision, cache, and container/browser processes also matter.
```

---

#

---

## 4. Use detached mode for long-running services

Instead of:

```bash
docker compose up
```

prefer:

```bash
docker compose up -d
```

Then watch logs separately:

```bash
docker compose logs -f
```

This avoids accidentally stopping containers with `Ctrl + C`.

---

## 5. Dev and stable environments behave differently

A dev environment may run directly from local code.

A stable environment may run from code copied into the image at build time.

Therefore:

* in dev, changing local files may immediately affect the container
* in stable, changing local files may require rebuilding the image

---

## 6. Use `docker compose config` to remove confusion

When multiple Compose files are used, always inspect the final result:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml config
```

This shows the actual final configuration after overrides.

---

##

---

## 8. `--no-cache` is powerful but slower

Use it when you suspect stale build layers.

Avoid using it every time unless needed, because it makes builds slower.

---

```text
```

---

# 30. Personal Command Cheat Sheet

## Start WSL

```powershell
wsl -d Ubuntu-26.04
```

## Go to project folder

```bash
cd /mnt/c/Users/ADMIN/Desktop/Projects/me/Code
```

## Check running containers

```bash
docker ps
```

## Check all containers

```bash
docker ps -a
```

## Start gateway container

```bash
docker start code-gateway-1
```

## View gateway logs

```bash
docker logs -f code-gateway-1
```

## Run Compose dev

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

## Run Compose dev in background

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

## Build stable gateway cleanly

```bash
docker compose -f docker-compose.yml -f docker-compose.stable.yml build --no-cache gateway
```

## Run stable gateway

```bash
docker compose -f docker-compose.yml -f docker-compose.stable.yml up -d gateway
```

## See final merged Compose config

```bash
docker compose -f docker-compose.yml -f docker-compose.stable.yml config
```

## Stop WSL

```powershell
wsl --shutdown
```

## Stop only Ubuntu distro

```powershell
wsl --terminate Ubuntu-26.04
```

---

# 31. Debugging Mindset

When something breaks, do not immediately assume the last command destroyed everything.

Follow this order:

1. Check if WSL is running.
2. Check if Docker daemon responds.
3. Check running containers.
4. Check stopped containers.
5. Check logs.
6. Identify whether it is Docker, Compose, WSL, or application code.
7. Read the last meaningful traceback line.
8. Fix the smallest confirmed issue first.

Example:

```text
NameError: name 'INSTAGRAM_AUTH_TASK_COOKIE' is not defined
```

This points to a missing Python variable/import, not a Docker networking issue.

Good debugging is mostly about narrowing the problem layer by layer.

---

# 32. Summary

Key concepts learned:

* How to enter WSL from PowerShell.
* How Windows paths map to WSL paths.
* How WSL shutdown affects Docker containers.
* Difference between Docker image, container, and Compose service.
* How to view logs of running containers.
* Difference between `docker logs -f` and `docker attach`.
* How Docker Compose uses multiple YAML files.
* Difference between dev and stable Compose setups.
* Meaning of bind mounts like `.:/app`.
* Meaning of named volumes.
* Meaning of `--reload` in Uvicorn.
* Meaning of `--no-cache` in Docker builds.
* How to detect whether an issue is Docker-level or app-level.
* How to interpret FastAPI/Uvicorn logs.
* How to respond to Python runtime errors inside containers.
* Why third-party tools like `yt-dlp` can be fragile.
* Why ML model RAM requirements depend on more than just model size.

These are reusable software engineering skills because they apply to many real-world backend, DevOps, ML, scraping, and deployment workflows.
