import sqlite3
import threading
from music1.config.config import DB_FILE, get_logger

logger = get_logger("Database")

class DownloadHistoryDB:
    def __init__(self):
        # THREAD SAFETY: Only one thread can write at a time
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        with self.lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    video_id TEXT PRIMARY KEY,
                    title TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.commit()

    def exists(self, video_id: str) -> bool:
        # Reads are safe to run concurrently, but we lock just to be safe with SQLite
        with self.lock:
            cur = self.conn.execute("SELECT 1 FROM downloads WHERE video_id=?", (video_id,))
            return cur.fetchone() is not None

    def add(self, video_id: str, title: str):
        with self.lock:
            try:
                self.conn.execute("INSERT OR IGNORE INTO downloads (video_id, title) VALUES (?, ?)", (video_id, title))
                self.conn.commit()
            except sqlite3.Error as e:
                logger.error(f"DB Write Error: {e}")

    def close(self):
        self.conn.close()