import concurrent.futures
import difflib
import json
import re
import subprocess
import sys
import unicodedata
from typing import Dict, List, Optional, Tuple

from config.config import (
    CACHE_FILE,
    MAX_WORKERS,
    MIN_CONFIDENCE_SCORE,
    YT_SEARCH_LIMIT,
    get_logger,
)
from models import Track, VideoResult


logger = get_logger("YouTubeService")

_MATCHER_VERSION = "v5"
_DUMMY_MIN_CONFIDENCE_SCORE = 60
_IGNORED_WORDS = {
    "a", "an", "and", "audio", "feat", "featuring", "from", "ft", "official",
    "original", "song", "the", "video", "with",
}
_BAD_KEYWORDS = {
    "acoustic", "bootleg", "cover", "dance", "demo", "instrumental", "karaoke",
    "live", "making of", "making video", "mashup", "orchestra", "orchestral",
    "parody", "podcast", "reaction", "rehearsal", "remake", "remix", "reverb",
    "review", "rework", "slowed", "sped up", "symphony", "tribute", "tutorial",
    "unboxing",
}
_PREFERRED_PHRASES = {
    "official audio": 12,
    "official video": 8,
    "provided to youtube by": 12,
}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _meaningful_tokens(value: str) -> list[str]:
    return [
        token for token in _normalize(value).split()
        if len(token) > 1 and token not in _IGNORED_WORDS
    ]


def _tokens(value: str) -> set[str]:
    return set(_meaningful_tokens(value))


def _coverage(expected: set[str], actual: set[str]) -> float:
    if not expected:
        return 0.0
    matched = 0
    for expected_token in expected:
        if any(
            expected_token == actual_token
            or difflib.SequenceMatcher(None, expected_token, actual_token).ratio() >= 0.82
            for actual_token in actual
        ):
            matched += 1
    return matched / len(expected)


def _longest_ordered_match(expected: list[str], actual: list[str]) -> int:
    longest = 0
    for start in range(len(expected)):
        matched = 0
        for token in actual:
            if start + matched < len(expected) and token == expected[start + matched]:
                matched += 1
                longest = max(longest, matched)
    return longest


class YouTubeResolver:
    def __init__(self):
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict[str, object]:
        if not CACHE_FILE.exists():
            return {}
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self) -> None:
        CACHE_FILE.write_text(json.dumps(self.cache, indent=2), encoding="utf-8")

    def _score_candidate(self, track: Track, video: VideoResult) -> dict:
        if video.duration and (video.duration < 45 or video.duration > 600):
            return {"score": 0, "reasons": ["implausible duration"]}

        video_title = _normalize(video.title)
        channel = _normalize(video.channel)
        description = _normalize(video.description)
        if any(f" {keyword} " in f" {video_title} " for keyword in _BAD_KEYWORDS):
            return {"score": 0, "reasons": ["alternate or non-original version"]}

        if track.is_dummy:
            query_tokens = _meaningful_tokens(track.sanitized_title)
            title_tokens = _meaningful_tokens(video.title)
            coverage = _coverage(set(query_tokens), set(title_tokens))
            ordered = _longest_ordered_match(query_tokens, title_tokens)
            rank_bonus = max(12 - (video.rank - 1) * 3, 0) if video.rank else 0
            return {
                "score": round(coverage * 60) + min(ordered * 8, 24) + rank_bonus,
                "reasons": [f"query coverage {coverage:.0%}", f"rank {video.rank}"],
            }

        title_tokens = _tokens(track.sanitized_title)
        artist_tokens = _tokens(track.artist)
        album_tokens = _tokens(track.sanitized_album)
        video_title_tokens = _tokens(video.title)
        identity_tokens = (
            video_title_tokens | _tokens(video.channel) | _tokens(video.description)
        )

        title_coverage = _coverage(title_tokens, video_title_tokens)
        artist_coverage = _coverage(artist_tokens, identity_tokens)
        album_coverage = _coverage(album_tokens, identity_tokens)
        if title_coverage < 0.8:
            return {"score": 0, "reasons": ["title mismatch"]}

        score = round(title_coverage * 45)
        reasons = [f"title {title_coverage:.0%}"]
        if artist_tokens:
            score += round(artist_coverage * 25)
            reasons.append(f"artist {artist_coverage:.0%}")
        if album_tokens:
            score += round(album_coverage * 10)
            reasons.append(f"album {album_coverage:.0%}")

        if channel.endswith(" topic") or " topic " in f" {channel} ":
            score += 12
            reasons.append("Topic channel")
        elif artist_coverage >= 0.8 and ("official" in channel or "vevo" in channel):
            score += 10
            reasons.append("official artist channel")

        for phrase, bonus in _PREFERRED_PHRASES.items():
            if phrase in video_title or phrase in description:
                score += bonus
                reasons.append(phrase)
                break

        if track.duration_ms and video.duration:
            difference = abs(video.duration - track.duration_ms / 1000)
            if difference <= 3:
                score += 18
                reasons.append("duration within 3s")
            elif difference <= 8:
                score += 12
                reasons.append("duration within 8s")
            elif difference <= 15:
                score += 5
                reasons.append("duration within 15s")
            elif difference > 30:
                score -= 20
                reasons.append("duration differs by over 30s")

        return {"score": max(score, 0), "reasons": reasons}

    def _calculate_score(self, track: Track, video: VideoResult) -> int:
        return self._score_candidate(track, video)["score"]

    def _run_yt_command(self, query: str, limit: int) -> List[VideoResult]:
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            f"ytsearch{limit}:{query}",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
            )
        except OSError:
            return []

        candidates = []
        for line in result.stdout.splitlines():
            try:
                data = json.loads(line)
                video_id = data.get("id")
                if not video_id:
                    continue
                candidates.append(
                    VideoResult(
                        id=video_id,
                        title=data.get("title", ""),
                        channel=data.get("channel") or data.get("uploader", ""),
                        duration=data.get("duration", 0) or 0,
                        url=(
                            data.get("webpage_url")
                            or f"https://www.youtube.com/watch?v={video_id}"
                        ),
                        rank=data.get("playlist_index", 0) or 0,
                        description=data.get("description", "") or "",
                    )
                )
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
        return candidates

    def _resolve_track(self, track: Track) -> dict:
        cache_key = (
            f"{_MATCHER_VERSION}|{track.title}|{track.artist}|"
            f"{track.album}|{track.duration_ms}"
        )
        cached = self.cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("url"):
            return {"url": cached["url"], "error": None, "candidates": []}

        candidates = self._run_yt_command(track.search_query, YT_SEARCH_LIMIT)
        scored = [
            (self._score_candidate(track, candidate), candidate)
            for candidate in candidates
        ]
        scored.sort(
            key=lambda item: (item[0]["score"], -item[1].rank),
            reverse=True,
        )
        threshold = (
            _DUMMY_MIN_CONFIDENCE_SCORE if track.is_dummy else MIN_CONFIDENCE_SCORE
        )
        best_evidence, best_video = scored[0] if scored else ({"score": 0}, None)

        if best_video and best_evidence["score"] >= threshold:
            self.cache[cache_key] = {
                "url": best_video.url,
                "score": best_evidence["score"],
                "video_id": best_video.id,
            }
            logger.info(
                "Selected '%s' by '%s' for '%s' by '%s' (score %s)",
                best_video.title,
                best_video.channel,
                track.title,
                track.artist,
                best_evidence["score"],
            )
            return {"url": best_video.url, "error": None, "candidates": []}

        suggestions = [
            {
                "title": candidate.title,
                "channel": candidate.channel,
                "duration": candidate.duration,
                "url": candidate.url,
                "score": evidence["score"],
                "reasons": evidence["reasons"],
            }
            for evidence, candidate in scored[:5]
        ]
        return {
            "url": None,
            "error": f"Low confidence (best score: {best_evidence['score']})",
            "candidates": suggestions,
        }

    def _search_single_with_retry(
        self,
        track: Track,
    ) -> Tuple[Optional[str], Optional[str]]:
        resolution = self._resolve_track(track)
        return resolution["url"], resolution["error"]

    def resolve_all(self, playlists: Dict[str, List[Track]]) -> Dict[str, dict]:
        results = {}
        for name, tracks in playlists.items():
            logger.info("Resolving %s tracks for '%s'...", len(tracks), name)
            valid_urls = []
            failed_tracks = []

            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {
                    executor.submit(self._resolve_track, track): track
                    for track in tracks
                }
                for future in concurrent.futures.as_completed(futures):
                    track = futures[future]
                    try:
                        resolution = future.result()
                    except Exception as exc:
                        failed_tracks.append({
                            "track": track,
                            "error": str(exc),
                            "candidates": [],
                        })
                        continue

                    if resolution["url"]:
                        valid_urls.append(resolution["url"])
                    else:
                        failed_tracks.append({
                            "track": track,
                            "error": resolution["error"],
                            "candidates": resolution["candidates"],
                        })

            results[name] = {"urls": valid_urls, "failed": failed_tracks}
            self._save_cache()

        return results
