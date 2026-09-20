import json
from pathlib import Path


REPORT_JSON = Path("var/music_config/spotify_md_resolve_report.json")
REPORT_CSV = Path("var/music_config/spotify_md_resolve_report.csv")
MANUAL_CANDIDATES = Path("var/music_config/spotify_md_manual_candidates.json")
AUDIT_PATH = Path("var/music_config/spotify_md_manual_resolution_audit.json")


EXPLICIT_FIXES = {
    # playlist_index, source_index -> manually verified Spotify candidate
    (9, 33): {
        "spotify_uri": "spotify:track:1L1Zd1zmoSeC1ettFrZBhE",
        "spotify_url": "https://open.spotify.com/track/1L1Zd1zmoSeC1ettFrZBhE",
        "matched_title": "Stray Bullet Woman",
        "matched_artists": "Greenleaf",
        "matched_album": "Agents Of Ahriman",
        "score": 90,
        "note": "Manual Spotify lookup: source has Bullit typo, Spotify stores title as Stray Bullet Woman.",
    },
    (9, 105): {
        "spotify_uri": "spotify:track:2OEn1QMWGnPIqjj4ea3BkB",
        "spotify_url": "https://open.spotify.com/track/2OEn1QMWGnPIqjj4ea3BkB",
        "matched_title": "Special Lady",
        "matched_artists": "Wolfmother",
        "matched_album": "Rock'n'Roll Baby",
        "score": 100,
        "note": "Manual Spotify album lookup found exact Wolfmother track.",
    },
    (10, 4): {
        "spotify_uri": "spotify:track:64gyxc6DgaFUyuipGQ7Mn0",
        "spotify_url": "https://open.spotify.com/track/64gyxc6DgaFUyuipGQ7Mn0",
        "matched_title": "Born in the North",
        "matched_artists": "Blue in Tokio",
        "matched_album": "Dark Sky Nights",
        "score": 100,
        "note": "Manual Spotify playlist lookup found exact title/artist/album match.",
    },
    (14, 93): {
        "spotify_uri": "spotify:track:61c96X4Th7oYdSDxIOEV3k",
        "spotify_url": "https://open.spotify.com/track/61c96X4Th7oYdSDxIOEV3k",
        "matched_title": "Astronaut (Something About Your Love)",
        "matched_artists": "Mansionair",
        "matched_album": "Shadowboxer",
        "score": 95,
        "note": "Manual Spotify lookup: artist:Mansionair track:Astronaut album:Shadowboxer.",
    },
    (14, 135): {
        "spotify_uri": "spotify:track:5Bopcl6UEcAwJqFOGagI8C",
        "spotify_url": "https://open.spotify.com/track/5Bopcl6UEcAwJqFOGagI8C",
        "matched_title": "Requiem",
        "matched_artists": "Tasogare, Amnesia",
        "matched_album": "Requiem",
        "score": 95,
        "note": "Manual web/Spotify lookup found exact Tasogare Requiem track.",
    },
    (15, 1): {
        "spotify_uri": "spotify:track:3NuzLWsRZXO1QsBUEWseGd",
        "spotify_url": "https://open.spotify.com/track/3NuzLWsRZXO1QsBUEWseGd",
        "matched_title": "Dancin (feat. Luvli) - Krono Remix",
        "matched_artists": "Aaron Smith, Krono, Luvli",
        "matched_album": "Moody Ibiza 2017",
        "score": 95,
        "note": "Manual web lookup found exact title and artists.",
    },
    (16, 19): {
        "spotify_uri": "spotify:track:6kTWBiPWlrSSkhBpeiEStK",
        "spotify_url": "https://open.spotify.com/track/6kTWBiPWlrSSkhBpeiEStK",
        "matched_title": "Lavender Lemonade",
        "matched_artists": "Garren Sean",
        "matched_album": "Lavender Lemonade",
        "score": 100,
        "note": "Manual Spotify playlist lookup found exact Garren Sean track.",
    },
    (16, 21): {
        "spotify_uri": "spotify:track:1llt3c1mYEU4wkpBbib3Yq",
        "spotify_url": "https://open.spotify.com/track/1llt3c1mYEU4wkpBbib3Yq",
        "matched_title": "Old Friend",
        "matched_artists": "Hoodlem",
        "matched_album": "Hoodlem",
        "score": 100,
        "note": "Manual Spotify playlist lookup found exact Hoodlem track.",
    },
    (17, 12): {
        "spotify_uri": "spotify:track:0rg7szhgC0xD1JLvjbDSSa",
        "spotify_url": "https://open.spotify.com/track/0rg7szhgC0xD1JLvjbDSSa",
        "matched_title": "Wildest Dreams",
        "matched_artists": "Beth",
        "matched_album": "Love Songs",
        "score": 95,
        "note": "Manual album lookup of Spotify album Love Songs.",
    },
    (18, 45): {
        "spotify_uri": "spotify:track:5h2nC1QYVpK0AJra70Ja3p",
        "spotify_url": "https://open.spotify.com/track/5h2nC1QYVpK0AJra70Ja3p",
        "matched_title": "L.A.LOVE (la la) [feat. YG]",
        "matched_artists": "Fergie, YG",
        "matched_album": "Double Dutchess",
        "score": 90,
        "note": "Manual Spotify candidate has exact artist/title/album with bracket punctuation difference.",
    },
    (19, 80): {
        "spotify_uri": "spotify:track:5XnAEMfcOXv0tzn3oPb3JS",
        "spotify_url": "https://open.spotify.com/track/5XnAEMfcOXv0tzn3oPb3JS",
        "matched_title": "Publicity",
        "matched_artists": "GZA",
        "matched_album": "Beneath The Surface",
        "score": 100,
        "note": "Manual Spotify album lookup found exact GZA track.",
    },
    (20, 4): {
        "spotify_uri": "spotify:track:6NhF2CXTIR6dnzvQ5eCWAD",
        "spotify_url": "https://open.spotify.com/track/6NhF2CXTIR6dnzvQ5eCWAD",
        "matched_title": "The Recipe (feat. Kendrick Lamar, Schoolboy Q, Ab-Soul & Jay Rock)",
        "matched_artists": "Black Hippy, Kendrick Lamar, Ab-Soul, Jay Rock, ScHoolboy Q",
        "matched_album": "Black Hippy",
        "score": 95,
        "note": "Manual web lookup found exact Black Hippy track.",
    },
    (20, 5): {
        "spotify_uri": "spotify:track:2jWMDQyrH7imrlEte4p16i",
        "spotify_url": "https://open.spotify.com/track/2jWMDQyrH7imrlEte4p16i",
        "matched_title": "Maari Kannu (Side-Ig Hogro)",
        "matched_artists": "Brodha V, Sukruth Mallesh",
        "matched_album": "Maari Kannu (Side-Ig Hogro)",
        "score": 100,
        "note": "Manual Spotify playlist lookup found exact Brodha V track.",
    },
    (22, 20): {
        "spotify_uri": "spotify:track:4jvavQ2CKORi3g0hQMzrnu",
        "spotify_url": "https://open.spotify.com/track/4jvavQ2CKORi3g0hQMzrnu",
        "matched_title": "Skyfall",
        "matched_artists": "F8L",
        "matched_album": "The Franky Tape",
        "score": 100,
        "note": "Manual web lookup found track id; Spotify API verified exact F8L track.",
    },
    (22, 44): {
        "spotify_uri": "spotify:track:3nINDQOE5wc0Ns9lL66JYI",
        "spotify_url": "https://open.spotify.com/track/3nINDQOE5wc0Ns9lL66JYI",
        "matched_title": "Sweaters",
        "matched_artists": "R3N5",
        "matched_album": "Sweaters",
        "score": 100,
        "note": "Manual web lookup found track id; Spotify API verified exact R3N5 track.",
    },
}


def top_candidate_by_key() -> dict[tuple[int, int], dict]:
    data = json.loads(MANUAL_CANDIDATES.read_text(encoding="utf-8"))
    output = {}
    for item in data:
        record = item["record"]
        candidates = item.get("candidates") or []
        if candidates:
            output[(record["playlist_index"], record["source_index"])] = candidates[0]
    return output


def apply() -> None:
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    candidates = top_candidate_by_key()
    audit = []

    for record in report["records"]:
        if record["status"] not in {"low_confidence", "no_results"}:
            continue
        key = (record["playlist_index"], record["source_index"])
        candidate = candidates.get(key)
        fix = EXPLICIT_FIXES.get(key)
        reason = ""
        if fix is None and candidate and int(candidate.get("score") or 0) >= 90:
            fix = {
                "spotify_uri": candidate["uri"],
                "spotify_url": candidate["url"],
                "matched_title": candidate["name"],
                "matched_artists": candidate["artists"],
                "matched_album": candidate["album"],
                "score": int(candidate["score"]),
                "note": "Accepted top manual candidate with score >= 90.",
            }
        elif fix is None and candidate:
            src_artist = (record["source_artist"] or "").replace("â€™", "'")
            src_title = (record["source_title"] or "").replace("â€™", "'")
            cand_artist = candidate.get("artists") or ""
            cand_title = candidate.get("name") or ""
            same_artist = src_artist.casefold() in cand_artist.casefold()
            same_title = src_title.casefold() == cand_title.casefold()
            punctuation_title = src_title.replace("’", "'").casefold() == cand_title.replace("’", "'").casefold()
            if same_artist and (same_title or punctuation_title) and int(candidate.get("score") or 0) >= 70:
                fix = {
                    "spotify_uri": candidate["uri"],
                    "spotify_url": candidate["url"],
                    "matched_title": candidate["name"],
                    "matched_artists": candidate["artists"],
                    "matched_album": candidate["album"],
                    "score": int(candidate["score"]),
                    "note": "Accepted exact title/artist manual candidate with punctuation normalization.",
                }

        if not fix:
            continue

        old_status = record["status"]
        record["status"] = "matched"
        record["score"] = fix["score"]
        record["spotify_uri"] = fix["spotify_uri"]
        record["spotify_url"] = fix["spotify_url"]
        record["matched_title"] = fix["matched_title"]
        record["matched_artists"] = fix["matched_artists"]
        record["matched_album"] = fix["matched_album"]
        record["error"] = f"manual_resolution: {fix['note']}"
        audit.append({
            "playlist_index": record["playlist_index"],
            "playlist_name": record["playlist_name"],
            "source_index": record["source_index"],
            "source_artist": record["source_artist"],
            "source_title": record["source_title"],
            "source_album": record["source_album"],
            "old_status": old_status,
            "new_status": "matched",
            **fix,
        })

    summary = {"total": len(report["records"]), "matched": 0, "low_confidence": 0, "no_results": 0, "api_error": 0}
    for record in report["records"]:
        summary[record["status"]] = summary.get(record["status"], 0) + 1
    report["summary"] = summary
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    AUDIT_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Applied {len(audit)} manual resolutions")
    print(summary)
    print(f"Audit: {AUDIT_PATH}")


if __name__ == "__main__":
    apply()
