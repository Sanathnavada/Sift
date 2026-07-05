import os
import logging
from ..utils import ensure_dir

logger = logging.getLogger(__name__)

try:
    import yt_dlp
except ImportError:  # optional until a YouTube job actually runs
    yt_dlp = None

class YoutubeFetcher:
    """Handles downloading audio directly from YouTube using yt-dlp."""
    def __init__(self, outdir):
        self.outdir = outdir
        ensure_dir(self.outdir)

    @staticmethod
    def _first_known(*values):
        for value in values:
            if value not in (None, "", "unknown"):
                return value
        return "unknown"

    def download_audio(self, url):
        if yt_dlp is None:
            raise RuntimeError("yt-dlp is required for YouTube media downloads. Install yt-dlp and retry.")
        logger.info(f"Fetching audio from YouTube: {url}")
        
        # CHANGED: We removed the FFmpeg MP3 converter. 
        # We now just download the raw, pure audio stream YouTube provides.
        outtmpl = os.path.join(self.outdir, '%(id)s.%(ext)s')
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': outtmpl,
            'quiet': True,
            'no_warnings': True
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                video_id = info_dict.get('id', 'unknown')
                title = info_dict.get('title', 'Unknown Title')
                duration = info_dict.get('duration', 0)
                
                # Extract Audio Quality Metrics
                fmt_info = info_dict.get('requested_formats', [info_dict])[0]
                acodec = self._first_known(fmt_info.get('acodec'), info_dict.get('acodec'))
                abr = self._first_known(fmt_info.get('abr'), info_dict.get('abr'))
                asr = self._first_known(fmt_info.get('asr'), info_dict.get('asr'))
                
                quality_str = f"Codec: {acodec}"
                if isinstance(abr, (int, float)):
                    quality_str += f" | Bitrate: {round(abr)} kbps"
                elif abr != 'unknown':
                    quality_str += f" | Bitrate: {abr} kbps"
                if asr != 'unknown':
                    quality_str += f" | Sample Rate: {asr} Hz"

                logger.info(f"✓ Fetched Native Audio Quality: {quality_str}")

                # CHANGED: Let yt-dlp tell us exactly what the final filename is 
                # (since it might be .webm or .m4a instead of .mp3)
                audio_path = ydl.prepare_filename(info_dict)
                
                if os.path.exists(audio_path):
                    logger.info(f"✓ Native audio ready for transcription (No conversion needed)")
                    return audio_path, title, quality_str, duration
                else:
                    raise FileNotFoundError("Audio file not found after download completion.")
        except Exception as e:
            logger.error(f"Failed to download YouTube audio: {e}")
            raise
