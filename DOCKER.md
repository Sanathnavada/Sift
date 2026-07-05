# Docker runtime

This project now keeps one Docker runtime path:

```text
Dockerfile
docker-compose.yml
docker-entrypoint.sh
.env.docker
requirements.txt
```

There are no separate development/stable Compose files and no separate Docker requirements file.

## Build and run

Edit `.env.docker` with your local credentials and run:

```bash
docker compose up --build
```

Open the application at:

```text
http://localhost:8000
```

The noVNC browser surface for Instagram login is available at:

```text
http://localhost:6080/vnc_clean.html?autoconnect=true&resize=scale&quality=9&compression=0
```

## What the image contains

The image includes:

- Python 3.12;
- the Sift `src/` package;
- FFmpeg;
- Tesseract;
- Chromium through Playwright;
- Xvfb, x11vnc, noVNC, websockify, and fluxbox for browser-based Instagram login;
- CPU PyTorch installed from the official PyTorch CPU wheel index;
- the remaining Python dependencies from `requirements.txt`.

No Node.js build is required because the UI is server-rendered by FastAPI.

## Volumes

The Compose file keeps mutable runtime state outside the image:

```text
gateway-var        -> /app/var
gateway-downloads  -> /app/downloads
```

These preserve generated artifacts, Instagram browser/session data, music history, Spotify tokens, YouTube match cache, and downloaded outputs.

## Model downloads

Large ML models are not baked into the image. They download on first use and are cached under:

```text
/app/var/model_cache
```

The first model-backed request can take longer. Reusing the `gateway-var` volume avoids repeated downloads.

## GPU note

The default image is CPU-compatible and portable. GPU acceleration would require a CUDA-compatible PyTorch base/runtime and Docker NVIDIA container support. That is intentionally not part of the baseline Compose setup.
