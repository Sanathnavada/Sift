FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    SIFT_ROOT=/app \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HF_HOME=/app/var/model_cache/huggingface \
    TORCH_HOME=/app/var/model_cache/torch \
    XDG_CACHE_HOME=/app/var/model_cache \
    MUSIC_CONFIG_DIR=/app/var/music_config \
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

COPY requirements.txt .

RUN python -m pip install --upgrade pip \
    && python -m pip install --timeout 300 --retries 10 torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --timeout 300 --retries 10 -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p \
    /app/var/artifacts \
    /app/var/sessions \
    /app/var/music_config \
    /app/var/model_cache \
    /app/downloads \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000 6080

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

CMD ["python", "-m", "uvicorn", "sift.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
