FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HF_HOME=/app/data/model_cache/huggingface \
    TORCH_HOME=/app/data/model_cache/torch \
    XDG_CACHE_HOME=/app/data/model_cache \
    MUSIC_CONFIG_DIR=/app/data/music_config \
    NAVIDROME_ENABLED=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        fonts-liberation \
        fluxbox \
        novnc \
        tesseract-ocr \
        websockify \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.docker.txt .

RUN python -m pip install --upgrade pip \
    && python -m pip install --timeout 300 --retries 10 -r requirements.docker.txt \
    && python -m pip install --timeout 300 --retries 10 torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cpu \
    && python -m playwright install --with-deps chromium

COPY . .
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p \
    /app/data/app_node_artifacts \
    /app/data/app_node_sessions \
    /app/data/music_config \
    /app/data/model_cache \
    /app/downloads \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000 6080

CMD ["docker-entrypoint.sh"]
