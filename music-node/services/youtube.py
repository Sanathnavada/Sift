import json
import difflib
import subprocess
import concurrent.futures
from typing import List, Dict, Optional, Tuple
from config.config import CACHE_FILE, YT_SEARCH_LIMIT, MIN_CONFIDENCE_SCORE, MAX_WORKERS, get_logger
from models import Track, VideoResult

logger = get_logger("YouTubeService")

class YouTubeResolver:
    def __init__(self):
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict[str, str]:
        if CACHE_FILE.exists():
            try: return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except: return {}
        return {}

    def _save_cache(self):
        CACHE_FILE.write_text(json.dumps(self.cache, indent=2), encoding="utf-8")

    def _calculate_score(self, track: Track, video: VideoResult) -> int:
        score = 0
        t_norm = track.sanitized_title.lower()
        v_norm = video.title.lower()
        
        if video.duration > 600: return 0

        # Song Mode
        if track.is_dummy:
            if t_norm in v_norm: score += 60
            elif v_norm in t_norm: score += 40
            else: score += int(difflib.SequenceMatcher(None, t_norm, v_norm).ratio() * 50)
            return score

        # Playlist Mode
        sim = difflib.SequenceMatcher(None, t_norm, v_norm).ratio()
        if t_norm in v_norm: score += 40
        elif sim > 0.6: score += 30 
        
        album_norm = track.album.lower() if track.album else ""
        if len(album_norm) > 4 and album_norm.split(" -")[0] in v_norm: score += 20
        
        if track.artist.lower() in video.channel.lower(): score += 40
        elif "topic" in video.channel.lower() and track.artist.lower() in video.channel.lower(): score += 50
        
        if "official audio" in v_norm: score += 15
        elif "lyric" in v_norm: score += 10
        
        bad_keywords = ["reaction", "cover", "review", "tutorial", "unboxing","live","remix","parody","dance","karaoke","podcast"]
        for bad in bad_keywords:
            if bad in v_norm and bad not in t_norm: score -= 100

        return score

    def _run_yt_command(self, query: str, limit: int) -> List[VideoResult]:
        cmd = ["yt-dlp", "--dump-json", "--flat-playlist", "--no-warnings", f"ytsearch{limit}:{query}"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            candidates = []
            for line in res.stdout.splitlines():
                try:
                    d = json.loads(line)
                    candidates.append(VideoResult(
                        id=d.get("id"), title=d.get("title", ""), 
                        channel=d.get("uploader", ""), duration=d.get("duration", 0) or 0,
                        url=d.get("webpage_url") or f"https://www.youtube.com/watch?v={d['id']}"
                    ))
                except: pass
            return candidates
        except Exception:
            return []

    def _search_single_with_retry(self, track: Track) -> Tuple[Optional[str], Optional[str]]:
        """
        Returns: (URL, None) if success
                 (None, ErrorReason) if failed
        """
        key = f"{track.title}|{track.artist}"
        if key in self.cache: return self.cache[key], None

        # 1. Standard Search
        candidates = self._run_yt_command(track.search_query, YT_SEARCH_LIMIT)
        best_s = -100
        best_vid = None

        for vid in candidates:
            s = self._calculate_score(track, vid)
            if s > best_s:
                best_s = s
                best_vid = vid
        
        if best_s >= MIN_CONFIDENCE_SCORE and best_vid:
            self.cache[key] = best_vid.url
            return best_vid.url, None

        # 2. INTELLIGENT RETRY (Relaxed)
        # If score was "okay" (e.g. 20-39), maybe it was just a strict penalty.
        # Or try searching simpler query: "Title Artist" (No 'official audio')
        retry_query = f"{track.sanitized_title} {track.artist}"
        retry_candidates = self._run_yt_command(retry_query, 5)
        
        best_s_retry = -100
        best_vid_retry = None
        
        for vid in retry_candidates:
            # Recalculate score but leniently
            s = self._calculate_score(track, vid)
            if s > best_s_retry:
                best_s_retry = s
                best_vid_retry = vid
        
        # Lower threshold for retry (30 instead of 40)
        if best_s_retry >= 30 and best_vid_retry:
            logger.info(f"   ⚠️  Retry Success for '{track.title}' (Score: {best_s_retry})")
            self.cache[key] = best_vid_retry.url
            return best_vid_retry.url, None

        # FAILURE
        reason = f"Low Score (Best: {best_s})"
        if best_vid:
            reason += f" - Reject: '{best_vid.title}'"
        return None, reason

    def resolve_all(self, playlists: Dict[str, List[Track]]) -> Dict[str, dict]:
        """
        Returns structure: 
        { "PlaylistName": { "urls": [...], "failed": [ (Track, Reason) ] } }
        """
        results = {}
        for name, tracks in playlists.items():
            logger.info(f"🔎 Resolving {len(tracks)} tracks for '{name}'...")
            
            valid_urls = []
            failed_tracks = []
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                # Map future to track
                futures = {executor.submit(self._search_single_with_retry, t): t for t in tracks}
                
                for f in concurrent.futures.as_completed(futures):
                    track = futures[f]
                    try:
                        url, error = f.result()
                        if url:
                            valid_urls.append(url)
                        else:
                            failed_tracks.append((track, error))
                    except Exception as e:
                        failed_tracks.append((track, str(e)))

            results[name] = {
                "urls": valid_urls,
                "failed": failed_tracks
            }
            self._save_cache()
            
        return results