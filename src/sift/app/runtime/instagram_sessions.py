"""Browser-tab scoped storage for Instagram authentication cookies."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path


SESSION_HEADER = "X-Client-Session-ID"
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
_SESSION_TTL_SECONDS = 12 * 60 * 60


class InstagramSessionStore:
    def __init__(self, root: Path):
        self.root = root

    def resolve(self, session_id: str | None) -> Path:
        value = (session_id or "").strip()
        if not _SESSION_ID_RE.fullmatch(value):
            raise ValueError("A valid browser session ID is required.")

        self.cleanup_expired()
        session_dir = self.root / value
        session_dir.mkdir(parents=True, exist_ok=True)
        session_dir.touch()
        return session_dir

    def cleanup_expired(self) -> None:
        if not self.root.exists():
            return
        cutoff = time.time() - _SESSION_TTL_SECONDS
        for session_dir in self.root.iterdir():
            if not session_dir.is_dir():
                continue
            try:
                if session_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(session_dir)
            except OSError:
                continue

