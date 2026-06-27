# Docker runtime

The image contains Python 3.12, FFmpeg, Tesseract, Chromium with its shared
libraries, common fonts, the backend dependencies, and the server-rendered
frontend assets. No Node.js build is required.

## Build and run

Copy `.env.docker.example` to `.env.docker`, fill in the required credentials,
then run:

```bash
docker compose up --build
```

Open `http://localhost:8000`.

The compose volumes preserve:

- generated artifacts and Instagram browser sessions under `/app/data`;
- persistent downloads under `/app/downloads`;
- Spotify tokens, YouTube match cache, and music history under
  `/app/data/music_config`.

## Runtime model downloads

Models are not baked into the image. They download on first use and are cached
under `/app/data/model_cache`:

- `microsoft/Florence-2-large` through Hugging Face Transformers;
- OpenAI Whisper `medium`;
- sentence-transformer models used by optional preprocessing flows.

The first model-backed request therefore needs network access and can take
several minutes. Persist the `gateway-data` volume to avoid downloading models
again.

## Instagram browser authentication

Playwright Chromium is installed in the image. Instagram cookies are stored in
the persistent data volume. The container starts a lightweight virtual desktop
with noVNC for interactive Instagram login flows.

Open `http://localhost:6080/vnc.html` while the container is running, then start
the Instagram task that requires login. Chromium windows opened by Playwright
will appear in the noVNC desktop.

## GPU

The default image is portable and CPU-compatible. NVIDIA GPU acceleration
requires a CUDA-compatible PyTorch image/runtime and Docker's NVIDIA container
toolkit; it is intentionally not assumed by this reproducible baseline.
