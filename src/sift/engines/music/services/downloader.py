import concurrent.futures
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yt_dlp
except ImportError:  # optional until a download job is executed
    yt_dlp = None

from sift.engines.music.config import (
    ROOT_OUTPUT_DIR,
    COOKIES_FILE,
    MUSIC_DOWNLOAD_WORKERS,
    MUSIC_DOWNLOAD_WORKERS_EPHEMERAL,
    get_logger,
)
from sift.engines.music.database import DownloadHistoryDB

logger = get_logger("Downloader")


def _resolve_ffmpeg_tools() -> tuple[Optional[str], bool]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg:
        return ffmpeg, bool(ffprobe)
    try:
        import imageio_ffmpeg
    except ImportError:
        return None, False
    return imageio_ffmpeg.get_ffmpeg_exe(), False


class MusicDownloader:
    def __init__(self, ephemeral: bool = False, outdir: Optional[Path] = None):
        """
        :param ephemeral: If True, no DB writes and no m3u generation (temp/browser mode).
        :param outdir:    Custom output directory. When set, overrides ROOT_OUTPUT_DIR and
                          the ephemeral temp path. DB and m3u behaviour still follows ephemeral.
        """
        self.ephemeral = ephemeral
        self.outdir = outdir
        self.stats = {"downloaded": 0, "skipped": 0, "failed": 0}
        self._stats_lock = threading.Lock()

        # Only initialize DB if we are preserving history
        if not self.ephemeral:
            self.db = DownloadHistoryDB()
        else:
            self.db = None

    def _increment_stat(self, name: str) -> None:
        with self._stats_lock:
            self.stats[name] += 1

    def _get_output_path(self, folder_name: str) -> Path:
        if self.outdir:
            # outdir is the exact destination — don't append folder_name as a subfolder
            path = self.outdir
        elif self.ephemeral:
            # Browser / temp mode
            path = Path(tempfile.gettempdir()) / "music_browser_mode" / folder_name
        else:
            # Persistent library mode
            path = ROOT_OUTPUT_DIR / folder_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _generate_m3u(self, folder_name: str):
        """ 
        [NEW] Automatically generates/updates the .m3u playlist file 
        for Navidrome compatibility.
        """
        # 1. Skip if in Browser Mode (We don't need playlists for temp files)
        if self.ephemeral:
            return
        
        folder_path = self._get_output_path(folder_name)
        
        # Safety check: ensure folder exists
        if not folder_path.exists():
            return
        
        # 2. Define the playlist file path (e.g. "My Playlist.m3u")
        m3u_path = folder_path / f"{folder_name}.m3u"
        
        # 3. Scan for audio files (Just like your script did)
        audio_files = [
            f.name for f in folder_path.glob("*") 
            if f.suffix.lower() in ['.m4a', '.webm', '.mp3', '.opus']
        ]
        
        if not audio_files:
            return

        try:
            # 4. Write the file
            with open(m3u_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for file in audio_files:
                    f.write(file + "\n")
            
            logger.info(f"📝 Playlist Updated: {folder_name}.m3u ({len(audio_files)} tracks)")
            
        except Exception as e:
            logger.error(f"❌ Failed to generate m3u for {folder_name}: {e}")

    def _download_task(self, url: str, folder_name: str) -> Optional[Path]:
        output_dir = self._get_output_path(folder_name)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        output_tmpl = str(output_dir / "%(title)s [%(id)s].%(ext)s")

        # [FIX] Force yt-dlp cache to live in config folder (or disable it)
        # We assume config is 2 levels up from services: services -> root -> config
        # Or simpler: just let it be None if we want to disable persistence
        
        opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': output_tmpl,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'overwrites': self.ephemeral,
            # Optional: Redirect yt-dlp cache to prevent root folder creation
            # 'cachedir': False, 
        }
        if COOKIES_FILE.exists():
            opts["cookiefile"] = str(COOKIES_FILE)

        ffmpeg_location, has_ffprobe = _resolve_ffmpeg_tools()
        if ffmpeg_location:
            opts["ffmpeg_location"] = ffmpeg_location
        if has_ffprobe:
            opts.update({
                "writethumbnail": True,
                "addmetadata": True,
                "postprocessors": [
                    {"key": "FFmpegMetadata"},
                    {"key": "EmbedThumbnail"},
                ],
            })
        else:
            logger.warning(
                "FFprobe not found; downloading audio without embedded metadata or thumbnail."
            )

        if yt_dlp is None:
            logger.error("yt-dlp is not installed; cannot download %s", url)
            self._increment_stat("failed")
            return None

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None

                vid_id = info['id']
                title = info.get('title', vid_id)

                if not self.ephemeral and self.db.exists(vid_id, folder_name):
                    self._increment_stat("skipped")
                    logger.info(f"⏭️  Skipped (Exists): {title}")
                    return None

                ydl.download([url])
                
                downloaded_file = next(output_dir.glob(f"*{vid_id}*"), None)

                self._increment_stat("downloaded")
                logger.info(f"✅ Downloaded: {title}")
                
                if not self.ephemeral:
                    self.db.add(vid_id, folder_name, title)
                
                return downloaded_file

        except Exception as e:
            logger.error(f"❌ Failed {url}: {e}")
            self._increment_stat("failed")
            return None

    def download_all(self, url_map: Dict[str, List[str]]) -> Dict[str, Any]:
        tasks = [(u, p) for p, urls in url_map.items() for u in urls]
        logger.info(f"💾 Processing {len(tasks)} items (Ephemeral: {self.ephemeral})...")
        
        results = []
        workers = MUSIC_DOWNLOAD_WORKERS_EPHEMERAL if self.ephemeral else MUSIC_DOWNLOAD_WORKERS
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            future_map = {ex.submit(self._download_task, u, p): u for u, p in tasks}
            for future in concurrent.futures.as_completed(future_map):
                try:
                    file_path = future.result()
                except Exception as exc:
                    logger.error("Download worker crashed: %s", exc)
                    self._increment_stat("failed")
                    continue
                if file_path:
                    results.append(str(file_path))
            
        if not self.ephemeral:
            self.db.close()
            # [NEW] Trigger Playlist Generation for every folder we touched
            for folder_name in url_map.keys():
                self._generate_m3u(folder_name)
            
        return {
            "stats": self.stats,
            "files": results
        }
