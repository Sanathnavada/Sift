import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

from create_spotify_playlists_from_md import (
    Candidate,
    ResolutionRecord,
    candidate_from_item,
    score_result,
)
from sift.engines.music.services.spotify import SpotifyProvider


REPORT_PATH = Path("var/music_config/spotify_md_resolve_report.json")
OUT_PATH = Path("var/music_config/spotify_md_manual_candidates.json")


def clean(value: str) -> str:
    value = value.replace("â€™", "'").replace("â€“", "-").replace("Ã¸", "ø")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def strip_parenthetical(value: str) -> str:
    value = re.sub(r"\s*[-–—]\s*From\s+\".*?\".*$", "", value, flags=re.I)
    value = re.sub(r"\s*-\s*Remastered\s+\d{4}.*$", "", value, flags=re.I)
    value = re.sub(r"\s*-\s*\d{4}\s*Remaster.*$", "", value, flags=re.I)
    value = re.sub(r"\s*\(From\s+.*?\)", "", value, flags=re.I)
    return clean(value)


def query_variants(record: dict) -> list[str]:
    artist = clean(record["source_artist"])
    title = clean(record["source_title"])
    album = clean(record["source_album"])
    simple_title = strip_parenthetical(title)
    variants = [
        f'track:"{title}" artist:"{artist}"',
        f'{title} {artist}',
        f'track:"{simple_title}" artist:"{artist}"',
        f'{simple_title} {artist}',
    ]
    if album:
        variants.extend([
            f'{simple_title} {artist} {album}',
            f'{title} {artist} {album}',
        ])
    if artist.lower() == "various artists":
        variants.extend([title, simple_title])
    return list(dict.fromkeys(q for q in variants if q.strip()))


def boosted_score(record: dict, candidate: Candidate) -> int:
    base = candidate.score
    title = clean(record["source_title"]).casefold()
    simple_title = strip_parenthetical(record["source_title"]).casefold()
    artists = clean(record["source_artist"]).casefold()
    candidate_title = candidate.name.casefold()
    candidate_artists = candidate.artists.casefold()
    if simple_title and simple_title == candidate_title:
        base += 25
    elif simple_title and (simple_title in candidate_title or candidate_title in simple_title):
        base += 15
    if artists and artists != "various artists" and artists in candidate_artists:
        base += 20
    if "feat." in title and simple_title in candidate_title:
        base += 10
    return base


def search_record(sp, record: dict) -> dict:
    all_candidates: list[Candidate] = []
    seen = set()
    queries = query_variants(record)
    for query in queries:
        try:
            result = sp.search(q=query, type="track", limit=10)
        except Exception as exc:
            return {"record": record, "queries": queries, "error": repr(exc), "candidates": []}
        items = ((result or {}).get("tracks") or {}).get("items") or []
        for item in items:
            candidate = candidate_from_item(
                type("Track", (), {
                    "title": clean(record["source_title"]),
                    "artist": clean(record["source_artist"]),
                    "album": clean(record["source_album"]),
                })(),
                item,
            )
            if candidate.uri in seen:
                continue
            seen.add(candidate.uri)
            candidate.score = boosted_score(record, candidate)
            all_candidates.append(candidate)
        time.sleep(0.08)
    all_candidates.sort(key=lambda c: c.score, reverse=True)
    return {
        "record": record,
        "queries": queries,
        "error": "",
        "candidates": [asdict(c) for c in all_candidates[:10]],
    }


def main() -> int:
    payload = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    records = [
        record for record in payload["records"]
        if record["status"] in {"low_confidence", "no_results"}
    ]
    provider = SpotifyProvider(use_user_auth=True)
    results = []
    for index, record in enumerate(records, 1):
        result = search_record(provider.sp, record)
        results.append(result)
        best = result["candidates"][0] if result["candidates"] else None
        if best:
            print(
                f"{index}/50 {record['source_artist']} - {record['source_title']} "
                f"=> {best['score']} {best['name']} / {best['artists']} / {best['album']}"
            )
        else:
            print(f"{index}/50 {record['source_artist']} - {record['source_title']} => NO CANDIDATES")
    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
