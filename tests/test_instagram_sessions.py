import os
import tempfile
import time
import unittest
from pathlib import Path

from app_node.instagram_sessions import InstagramSessionStore


class InstagramSessionStoreTests(unittest.TestCase):
    def test_resolve_is_stable_and_isolated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = InstagramSessionStore(Path(temp_dir))

            first = store.resolve("a" * 32)
            same = store.resolve("a" * 32)
            other = store.resolve("b" * 32)

            self.assertEqual(first, same)
            self.assertNotEqual(first, other)
            self.assertTrue(first.is_dir())
            self.assertTrue(other.is_dir())

    def test_rejects_unsafe_session_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = InstagramSessionStore(Path(temp_dir))

            with self.assertRaises(ValueError):
                store.resolve("../shared-session")

    def test_removes_expired_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = InstagramSessionStore(Path(temp_dir))
            expired = Path(temp_dir) / ("c" * 32)
            expired.mkdir()
            old_time = time.time() - (13 * 60 * 60)
            os.utime(expired, (old_time, old_time))

            store.cleanup_expired()

            self.assertFalse(expired.exists())


if __name__ == "__main__":
    unittest.main()
