import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_node.insta.web_fetcher import WebCollectionFetcher


class InstagramCookieCacheTests(unittest.TestCase):
    def test_complete_cookie_cache_is_reused_without_network_probe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = Path(temp_dir) / "web_session.json"
            cached = {
                "sessionid": "session-token",
                "csrftoken": "csrf-token",
                "_ig_username": "cached_user",
            }
            session_file.write_text(json.dumps(cached), encoding="utf-8")
            fetcher = WebCollectionFetcher(
                outdir=temp_dir,
                session_dir=temp_dir,
            )

            with patch.object(fetcher, "_make_session") as make_session:
                result = fetcher._load_or_acquire_cookies()

            self.assertEqual(result, cached)
            self.assertEqual(fetcher.username, "cached_user")
            make_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
