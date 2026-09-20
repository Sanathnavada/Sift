import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sift.engines.music.services.spotify import SpotifyProvider


HEADING_RE = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s+[—-]\s+(\d+)\s+tracks\s*$")
ARTIST_RE = re.compile(r"^-\s+\*\*(.+?)\*\*\s+[—-]\s+(.+)$")
TRACK_RE = re.compile(r"\*\*(.+?)\*\*\s+\[(.*?)\]")
DEFAULT_REPORT_DIR = Path("var/music_config")


@dataclass
class TrackSpec:
    artist: str
    title: str
    album: str


@dataclass
class PlaylistSpec:
    index: int
    name: str
    expected_count: int
    tracks: list[TrackSpec]


@dataclass
class Candidate:
    score: int
    name: str
    artists: str
    album: str
    uri: str
    url: str


@dataclass
class ResolutionRecord:
    playlist_index: int
    playlist_name: str
    source_index: int
    source_artist: str
    source_title: str
    source_album: str
    status: str
    score: int
    spotify_uri: str
    spotify_url: str
    matched_title: str
    matched_artists: str
    matched_album: str
    query_with_album: str
    query_without_album: str
    query_used: str
    error: str
    candidates: list[Candidate]


def parse_markdown(path: Path) -> list[PlaylistSpec]:
    playlists: list[PlaylistSpec] = []
    current: PlaylistSpec | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        heading = HEADING_RE.match(line)
        if heading:
            current = PlaylistSpec(
                index=int(heading.group(1)),
                name=heading.group(2).strip(),
                expected_count=int(heading.group(3)),
                tracks=[],
            )
            playlists.append(current)
            continue

        if current is None or not line.startswith("- "):
            continue

        artist_match = ARTIST_RE.match(line)
        if not artist_match:
            continue
        artist = artist_match.group(1).strip()
        rest = artist_match.group(2)
        for track_match in TRACK_RE.finditer(rest):
            current.tracks.append(
                TrackSpec(
                    artist=artist,
                    title=track_match.group(1).strip(),
                    album=track_match.group(2).strip(),
                )
            )

    return playlists


def spotify_search_query(track: TrackSpec, include_album: bool = True) -> str:
    query = f'track:"{track.title}" artist:"{track.artist}"'
    if include_album and track.album:
        query += f' album:"{track.album}"'
    return query


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def score_result(track: TrackSpec, item: dict[str, Any]) -> int:
    source_title = normalize_text(track.title)
    source_artist = normalize_text(track.artist)
    source_album = normalize_text(track.album)
    title = normalize_text(item.get("name") or "")
    artists = normalize_text(" ".join(artist.get("name", "") for artist in item.get("artists", [])))
    album = normalize_text(((item.get("album") or {}).get("name") or ""))
    score = 0
    if source_title == title:
        score += 50
    elif source_title in title or title in source_title:
        score += 25
    if source_artist and source_artist in artists:
        score += 35
    if source_album and source_album == album:
        score += 15
    elif source_album and (source_album in album or album in source_album):
        score += 8
    return score


def candidate_from_item(track: TrackSpec, item: dict[str, Any]) -> Candidate:
    album = item.get("album") or {}
    return Candidate(
        score=score_result(track, item),
        name=item.get("name") or "",
        artists=", ".join(artist.get("name", "") for artist in item.get("artists", [])),
        album=album.get("name") or "",
        uri=item.get("uri") or "",
        url=(item.get("external_urls") or {}).get("spotify", ""),
    )


def search_candidates(sp, track: TrackSpec, query: str, limit: int) -> list[Candidate]:
    result = sp.search(q=query, type="track", limit=limit)
    items = ((result or {}).get("tracks") or {}).get("items") or []
    candidates = [candidate_from_item(track, item) for item in items]
    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


def resolve_track(sp, playlist: PlaylistSpec, source_index: int, track: TrackSpec, min_score: int, limit: int) -> ResolutionRecord:
    query_with_album = spotify_search_query(track, include_album=True)
    query_without_album = spotify_search_query(track, include_album=False)
    all_candidates: list[Candidate] = []
    query_used = ""
    error = ""

    try:
        for query in (query_with_album, query_without_album):
            query_used = query
            candidates = search_candidates(sp, track, query, limit)
            all_candidates.extend(candidates)
            if candidates:
                best = candidates[0]
                status = "matched" if best.score >= min_score else "low_confidence"
                return ResolutionRecord(
                    playlist_index=playlist.index,
                    playlist_name=playlist.name,
                    source_index=source_index,
                    source_artist=track.artist,
                    source_title=track.title,
                    source_album=track.album,
                    status=status,
                    score=best.score,
                    spotify_uri=best.uri if status == "matched" else "",
                    spotify_url=best.url if status == "matched" else "",
                    matched_title=best.name,
                    matched_artists=best.artists,
                    matched_album=best.album,
                    query_with_album=query_with_album,
                    query_without_album=query_without_album,
                    query_used=query_used,
                    error="",
                    candidates=all_candidates[:limit],
                )
        status = "no_results"
    except Exception as exc:
        status = "api_error"
        error = repr(exc)

    return ResolutionRecord(
        playlist_index=playlist.index,
        playlist_name=playlist.name,
        source_index=source_index,
        source_artist=track.artist,
        source_title=track.title,
        source_album=track.album,
        status=status,
        score=0,
        spotify_uri="",
        spotify_url="",
        matched_title="",
        matched_artists="",
        matched_album="",
        query_with_album=query_with_album,
        query_without_album=query_without_album,
        query_used=query_used,
        error=error,
        candidates=all_candidates[:limit],
    )


def write_reports(records: list[ResolutionRecord], json_path: Path, csv_path: Path, source_path: Path, playlists: list[PlaylistSpec]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": str(source_path),
        "summary": summarize(records),
        "playlists": [
            {
                "index": playlist.index,
                "name": playlist.name,
                "expected_count": playlist.expected_count,
                "parsed_count": len(playlist.tracks),
            }
            for playlist in playlists
        ],
        "records": [
            {
                **{key: value for key, value in asdict(record).items() if key != "candidates"},
                "candidates": [asdict(candidate) for candidate in record.candidates],
            }
            for record in records
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "playlist_index",
        "playlist_name",
        "source_index",
        "source_artist",
        "source_title",
        "source_album",
        "status",
        "score",
        "spotify_uri",
        "spotify_url",
        "matched_title",
        "matched_artists",
        "matched_album",
        "query_used",
        "error",
        "top_candidates",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = {field: getattr(record, field) for field in fieldnames if field != "top_candidates"}
            row["top_candidates"] = " | ".join(
                f"{candidate.score}: {candidate.name} - {candidate.artists} [{candidate.album}]"
                for candidate in record.candidates[:3]
            )
            writer.writerow(row)


def append_progress_record(progress_path: Path, record: ResolutionRecord) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **{key: value for key, value in asdict(record).items() if key != "candidates"},
        "candidates": [asdict(candidate) for candidate in record.candidates],
    }
    with progress_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def candidate_from_payload(payload: dict[str, Any]) -> Candidate:
    return Candidate(
        score=int(payload.get("score") or 0),
        name=payload.get("name") or "",
        artists=payload.get("artists") or "",
        album=payload.get("album") or "",
        uri=payload.get("uri") or "",
        url=payload.get("url") or "",
    )


def record_from_payload(payload: dict[str, Any]) -> ResolutionRecord:
    return ResolutionRecord(
        playlist_index=int(payload.get("playlist_index") or 0),
        playlist_name=payload.get("playlist_name") or "",
        source_index=int(payload.get("source_index") or 0),
        source_artist=payload.get("source_artist") or "",
        source_title=payload.get("source_title") or "",
        source_album=payload.get("source_album") or "",
        status=payload.get("status") or "api_error",
        score=int(payload.get("score") or 0),
        spotify_uri=payload.get("spotify_uri") or "",
        spotify_url=payload.get("spotify_url") or "",
        matched_title=payload.get("matched_title") or "",
        matched_artists=payload.get("matched_artists") or "",
        matched_album=payload.get("matched_album") or "",
        query_with_album=payload.get("query_with_album") or "",
        query_without_album=payload.get("query_without_album") or "",
        query_used=payload.get("query_used") or "",
        error=payload.get("error") or "",
        candidates=[
            candidate_from_payload(candidate)
            for candidate in payload.get("candidates", [])
            if isinstance(candidate, dict)
        ],
    )


def load_progress_records(progress_path: Path) -> list[ResolutionRecord]:
    if not progress_path.exists():
        return []
    records: list[ResolutionRecord] = []
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(record_from_payload(json.loads(line)))
    return records


def summarize(records: list[ResolutionRecord]) -> dict[str, int]:
    summary = {"total": len(records), "matched": 0, "low_confidence": 0, "no_results": 0, "api_error": 0}
    for record in records:
        summary[record.status] = summary.get(record.status, 0) + 1
    return summary


def validate_parse(playlists: list[PlaylistSpec]) -> bool:
    mismatches = [
        playlist for playlist in playlists
        if playlist.expected_count != len(playlist.tracks)
    ]
    if not mismatches:
        return True
    for playlist in mismatches:
        print(f"Count mismatch: {playlist.name}: expected {playlist.expected_count}, parsed {len(playlist.tracks)}")
    return False


def resolve_all(
    sp,
    playlists: list[PlaylistSpec],
    min_score: int,
    candidate_limit: int,
    sleep_seconds: float,
    progress_path: Path,
    resume: bool,
) -> list[ResolutionRecord]:
    records: list[ResolutionRecord] = load_progress_records(progress_path) if resume else []
    resolved_keys = {
        (record.playlist_index, record.source_index)
        for record in records
    }
    total = sum(len(playlist.tracks) for playlist in playlists)
    done = len(records)
    if progress_path.exists() and not resume:
        progress_path.unlink()
    if resume and records:
        summary = summarize(records)
        print(
            f"Resuming from {progress_path}: {done}/{total} already resolved | "
            f"matched {summary['matched']} | low {summary['low_confidence']} | "
            f"none {summary['no_results']} | errors {summary['api_error']}",
            flush=True,
        )
    for playlist in playlists:
        print(f"Resolving playlist {playlist.index}: {playlist.name} ({len(playlist.tracks)} tracks)", flush=True)
        for source_index, track in enumerate(playlist.tracks, 1):
            if (playlist.index, source_index) in resolved_keys:
                continue
            record = resolve_track(sp, playlist, source_index, track, min_score, candidate_limit)
            records.append(record)
            append_progress_record(progress_path, record)
            resolved_keys.add((playlist.index, source_index))
            done += 1
            if done % 25 == 0:
                summary = summarize(records)
                print(
                    f"  resolved {done}/{total} | matched {summary['matched']} | "
                    f"low {summary['low_confidence']} | none {summary['no_results']} | errors {summary['api_error']}",
                    flush=True,
                )
            if sleep_seconds:
                time.sleep(sleep_seconds)
    return records


def chunks(values: list[str], size: int = 100):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def create_playlist(sp, user_id: str, name: str, public: bool, description: str) -> dict:
    return sp.user_playlist_create(
        user_id,
        name,
        public=public,
        collaborative=False,
        description=description[:300],
    )


def import_from_report(report_path: Path, prefix: str, public: bool, sleep_seconds: float) -> int:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    provider = SpotifyProvider(use_user_auth=True)
    records = payload.get("records", [])
    playlist_names = []
    for record in records:
        name = record["playlist_name"]
        if name not in playlist_names:
            playlist_names.append(name)

    import_report_path = DEFAULT_REPORT_DIR / "spotify_md_import_report.txt"
    with import_report_path.open("w", encoding="utf-8") as report:
        for playlist_index, playlist_name in enumerate(playlist_names, 1):
            playlist_records = [
                record for record in records
                if record["playlist_name"] == playlist_name and record["status"] == "matched" and record["spotify_uri"]
            ]
            target_name = f"{prefix}{playlist_name}"
            print(f"[{playlist_index}/{len(playlist_names)}] Creating {target_name} ({len(playlist_records)} matched tracks)")
            created = create_playlist(
                provider.sp,
                provider.user_id,
                target_name,
                public,
                f"Imported from resolved report {report_path.name} by Sift music CLI.",
            )
            playlist_id = created["id"]
            uris = [record["spotify_uri"] for record in playlist_records]
            for uri_chunk in chunks(uris, 100):
                provider.sp.playlist_add_items(playlist_id, uri_chunk)
                if sleep_seconds:
                    time.sleep(sleep_seconds)
            url = created.get("external_urls", {}).get("spotify", "")
            print(f"  added {len(uris)} tracks")
            print(f"  {url}")
            report.write(f"Playlist: {target_name}\n")
            report.write(f"URL: {url}\n")
            report.write(f"Added: {len(uris)}\n\n")
            report.flush()
    print(f"Import report written to: {import_report_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve or create Spotify playlists from the 23-bucket Markdown export.")
    parser.add_argument("markdown_path", type=Path, nargs="?")
    parser.add_argument("--prefix", default="Sift - ")
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--resume-progress", action="store_true")
    parser.add_argument("--import-from-report", type=Path)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_DIR / "spotify_md_resolve_report.json")
    parser.add_argument("--report-csv", type=Path, default=DEFAULT_REPORT_DIR / "spotify_md_resolve_report.csv")
    parser.add_argument("--progress-jsonl", type=Path, default=DEFAULT_REPORT_DIR / "spotify_md_resolve_progress.jsonl")
    parser.add_argument("--min-score", type=int, default=55)
    parser.add_argument("--candidate-limit", type=int, default=5)
    parser.add_argument("--limit-playlists", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.12)
    args = parser.parse_args()

    if args.import_from_report:
        return import_from_report(args.import_from_report, args.prefix, args.public, args.sleep)

    if not args.markdown_path:
        parser.error("markdown_path is required unless --import-from-report is used")

    playlists = parse_markdown(args.markdown_path)
    if args.limit_playlists:
        playlists = playlists[:args.limit_playlists]
    total = sum(len(playlist.tracks) for playlist in playlists)
    print(f"Parsed {len(playlists)} playlists and {total} tracks.", flush=True)
    if not validate_parse(playlists):
        return 2

    if args.dry_run:
        for playlist in playlists:
            print(f"{playlist.index}. {playlist.name}: {len(playlist.tracks)}", flush=True)
        return 0

    if not args.resolve_only:
        print("Refusing to create playlists directly from Markdown. Run --resolve-only first, review the report, then use --import-from-report.", flush=True)
        return 2

    provider = SpotifyProvider(use_user_auth=True)
    records = resolve_all(
        provider.sp,
        playlists,
        args.min_score,
        args.candidate_limit,
        args.sleep,
        args.progress_jsonl,
        args.resume_progress,
    )
    write_reports(records, args.report_json, args.report_csv, args.markdown_path, playlists)
    summary = summarize(records)
    print(
        "Resolve complete: "
        f"total {summary['total']} | matched {summary['matched']} | "
        f"low_confidence {summary['low_confidence']} | no_results {summary['no_results']} | api_error {summary['api_error']}",
        flush=True,
    )
    print(f"JSON report: {args.report_json}", flush=True)
    print(f"CSV report:  {args.report_csv}", flush=True)
    print(f"Progress JSONL: {args.progress_jsonl}", flush=True)
    return 0 if summary["api_error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
