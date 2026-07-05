"""Persistent download history for the music engine."""

import sqlite3
import threading
from pathlib import Path

from sift.engines.music.config import DB_FILE, get_logger

logger = get_logger("Database")


class DownloadHistoryDB:
    """Small SQLite wrapper used to skip already-downloaded videos per playlist.

    The downloader runs several worker threads. SQLite connections can be shared
    with ``check_same_thread=False``, but writes must still be serialized to
    avoid surprising lock errors and inconsistent commits.
    """

    def __init__(self, db_file: str | Path = DB_FILE):
        self.db_file = Path(db_file)
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.db_file, check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        with self.lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    video_id TEXT,
                    playlist_name TEXT,
                    title TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (video_id, playlist_name)
                )
                """
            )
            self.conn.commit()

    def exists(self, video_id: str, playlist_name: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM downloads WHERE video_id=? AND playlist_name=?",
                (video_id, playlist_name),
            ).fetchone()
            return row is not None

    def add(self, video_id: str, playlist_name: str, title: str) -> None:
        with self.lock:
            try:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO downloads (video_id, playlist_name, title)
                    VALUES (?, ?, ?)
                    """,
                    (video_id, playlist_name, title),
                )
                self.conn.commit()
            except sqlite3.Error as exc:
                logger.error("DB write error: %s", exc)

    def close(self) -> None:
        with self.lock:
            self.conn.close()
