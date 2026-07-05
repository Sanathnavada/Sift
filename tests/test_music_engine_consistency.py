import concurrent.futures
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sift.engines.music.database import DownloadHistoryDB
from sift.engines.music.services import downloader as downloader_module
from sift.engines.music.services.downloader import MusicDownloader


def test_download_history_is_playlist_scoped_and_idempotent():
    with tempfile.TemporaryDirectory() as temp_dir:
        db = DownloadHistoryDB(Path(temp_dir) / "history.db")
        try:
            db.add("video-1", "Playlist A", "Song")
            db.add("video-1", "Playlist A", "Song Duplicate")

            assert db.exists("video-1", "Playlist A") is True
            assert db.exists("video-1", "Playlist B") is False
        finally:
            db.close()


def test_music_downloader_stats_updates_are_thread_safe():
    dl = MusicDownloader(ephemeral=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(dl._increment_stat, "downloaded") for _ in range(500)]
        for future in futures:
            future.result()

    assert dl.stats["downloaded"] == 500


class _FakeYoutubeDL:
    captured_opts = None

    def __init__(self, opts):
        type(self).captured_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        return {"id": "abc123", "title": "Example"}

    def download(self, urls):
        output_template = self.captured_opts["outtmpl"]
        output_dir = Path(output_template).parent
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "Example [abc123].m4a").write_text("audio", encoding="utf-8")


def test_music_downloader_does_not_pass_missing_cookiefile_to_ytdlp():
    with tempfile.TemporaryDirectory() as temp_dir:
        missing_cookie_file = Path(temp_dir) / "missing-cookies.txt"
        outdir = Path(temp_dir) / "out"
        dl = MusicDownloader(ephemeral=True, outdir=outdir)

        with (
            patch.object(downloader_module, "COOKIES_FILE", missing_cookie_file),
            patch.object(downloader_module, "_resolve_ffmpeg_tools", return_value=(None, False)),
            patch.object(downloader_module, "yt_dlp", SimpleNamespace(YoutubeDL=_FakeYoutubeDL)),
        ):
            result = dl.download_all({"Playlist": ["https://youtu.be/example"]})

        assert "cookiefile" not in _FakeYoutubeDL.captured_opts
        assert result["stats"]["downloaded"] == 1
        assert len(result["files"]) == 1
