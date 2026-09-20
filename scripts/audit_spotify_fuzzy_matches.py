import csv
import json
import re
from pathlib import Path
from typing import Any


REPORT_JSON = Path("var/music_config/spotify_md_resolve_report.json")
AUDIT_CSV = Path("var/music_config/spotify_fuzzy_match_verification.csv")


MANUAL_OVERRIDES = {
    (2, 51): ("verified_manual", "same Ed Sheeran Hobbit track; Spotify omits source title suffix and special-edition album suffix"),
    (2, 99): ("verified_manual", "same Hozier track; featured artist is stored as an artist and album is base edition"),
    (2, 149): ("verified_manual", "same Michael Logen track; Spotify title/album include the SUITS placement suffix"),
    (6, 91): ("verified_manual", "same Queen track; Spotify selected base album while MD names the 2011 deluxe remaster"),
    (9, 54): ("verified_manual", "same Metallica track; Spotify selected remastered deluxe container"),
    (9, 56): ("verified_manual", "same Metallica track; Spotify selected remastered deluxe container"),
    (12, 101): ("verified_manual", "same Sam Tinnesz track; featured artist is stored as an artist"),
    (18, 99): ("verified_manual", "same Pop Smoke track; featured artist is stored as an artist"),
    (18, 119): ("verified_manual", "corrected to original LSD/Sia track; MD stores Sia as artist and single title as album"),
    (18, 120): ("verified_manual", "corrected to original LSD/Sia track; MD stores Sia as artist and single title as album"),
    (18, 121): ("verified_manual", "corrected to original LSD/Sia track; MD stores Sia as artist and single title as album"),
    (19, 13): ("verified_manual", "same Afroman song; Spotify canonical title includes Colt 45 subtitle and uses another album container"),
    (19, 121): ("verified_manual", "same SoulChef track; featured artist is stored as an artist"),
    (19, 124): ("verified_manual", "same Beatnuts song; Spotify canonical title includes Method Man feature and uses another album container"),
    (21, 44): ("verified_manual", "same Pop Smoke track; featured artist is stored as an artist"),
    (21, 46): ("verified_manual", "same Pop Smoke track; featured artists/producers are stored as artists"),
    (4, 24): ("verified_manual", "same Elle King track; Spotify uses parent album instead of single container"),
    (4, 47): ("verified_manual", "same Jack White track; Spotify uses parent album instead of single container"),
    (4, 51): ("verified_manual", "same Jesse Roper track; Spotify uses parent album instead of single container"),
    (4, 90): ("verified_manual", "same Black Keys track; Spotify uses deluxe remastered album container"),
    (5, 26): ("verified_manual", "same Electric Guest track; Spotify uses parent album instead of single container"),
    (5, 36): ("verified_manual", "same Hozier track; Spotify stores album as Unheard"),
    (6, 90): ("verified_manual", "same Queen track; remaster wording differs between MD and Spotify"),
    (6, 92): ("verified_manual", "same Queen track; remaster wording differs between MD and Spotify"),
    (6, 119): ("verified_manual", "same Cranberries track; Spotify selected a compilation container"),
    (6, 125): ("verified_manual", "same Kinks track; mono wording and album container differ"),
    (6, 138): ("verified_manual", "same ZZ Top track; remaster wording omitted and Spotify selected a compilation container"),
    (7, 72): ("verified_manual", "same Foreign Air track; Spotify uses parent album instead of single container"),
    (7, 117): ("verified_manual", "same Nick Varvaro track; Spotify uses another release container"),
    (7, 208): ("verified_manual", "same Will Paquin track; Spotify uses single container rather than MD album label"),
    (8, 21): ("verified_manual", "same Ben Howard track; Spotify uses parent album instead of single container"),
    (8, 90): ("verified_manual", "same Brian Jonestown Massacre track; Spotify selected singles collection container"),
    (9, 15): ("verified_manual", "same Chickenfoot track; Spotify uses bonus track edition container"),
    (9, 91): ("verified_manual", "same The HU track; featured artist is stored as an artist and album is deluxe edition"),
    (12, 62): ("verified_manual", "same Imagine Dragons/JID track; Spotify uses Mercury Acts 1 & 2 container"),
    (14, 89): ("verified_manual", "same Madjo track; Spotify selected soundtrack container"),
    (18, 29): ("verified_manual", "same Doja Cat track; featured artist is stored as an artist and album is parent album"),
    (18, 70): ("verified_manual", "corrected to Celebration deluxe track requested by the MD"),
    (18, 75): ("verified_manual", "same Maroon 5 track; featured artist is stored as an artist and album is parent deluxe album"),
    (18, 84): ("verified_manual", "corrected to original Miley Cyrus and Dua Lipa track, not the Jax Jones remix"),
    (18, 93): ("verified_manual", "corrected to Olivia Rodrigo original, not the cover version"),
    (19, 130): ("verified_manual", "same Young MC track; Spotify selected compilation container"),
}


def clean_encoding(value: str) -> str:
    replacements = {
        "Ã©": "é",
        "Ã¡": "á",
        "Ã³": "ó",
        "Ã¼": "ü",
        "Ã¶": "ö",
        "Ã±": "ñ",
        "â€™": "'",
        "â€œ": '"',
        "â€": '"',
        "â€“": "-",
        "â€”": "-",
        "â€¦": "...",
    }
    for bad, good in replacements.items():
        value = value.replace(bad, good)
    return value


def normalize(value: str) -> str:
    value = clean_encoding(value or "").casefold()
    value = re.sub(r"\b(remaster(?:ed)?|mono|stereo|explicit|single|version|edit)\b", "", value)
    value = re.sub(r"\b(from|theme from|soundtrack|original motion picture soundtrack)\b", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def token_set(value: str) -> set[str]:
    return {token for token in normalize(value).split() if len(token) > 1}


def overlap_ratio(left: str, right: str) -> float:
    left_tokens = token_set(left)
    right_tokens = token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def classify(record: dict[str, Any]) -> tuple[str, str]:
    source_title = record["source_title"]
    source_artist = record["source_artist"]
    source_album = record["source_album"]
    matched_title = record["matched_title"]
    matched_artists = record["matched_artists"]
    matched_album = record["matched_album"]

    title_exact = normalize(source_title) == normalize(matched_title)
    artist_contained = normalize(source_artist) in normalize(matched_artists)
    album_exact = normalize(source_album) == normalize(matched_album)
    album_overlap = overlap_ratio(source_album, matched_album)
    title_overlap = overlap_ratio(source_title, matched_title)

    if title_exact and artist_contained and (album_exact or album_overlap >= 0.5):
        return "verified", "title/artist match; album exact or strongly overlapping"
    if title_exact and artist_contained and record["score"] >= 85:
        return "likely_ok", "title/artist match; album differs but resolver score is high"
    if title_overlap >= 0.7 and artist_contained and record["score"] >= 85:
        return "likely_ok", "title strongly overlaps; artist matches"
    return "needs_review", "source and selected Spotify metadata differ enough to inspect manually"


def main() -> None:
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    rows = []
    for record in report["records"]:
        if record["status"] != "matched":
            continue
        is_original_fuzzy = record["score"] < 100 and not str(record["error"]).startswith("manual_resolution:")
        is_manual_auto = "Accepted top manual candidate" in str(record["error"]) or "Accepted exact title/artist manual candidate" in str(record["error"])
        if not (is_original_fuzzy or is_manual_auto):
            continue

        verdict, reason = classify(record)
        override = MANUAL_OVERRIDES.get((record["playlist_index"], record["source_index"]))
        if override:
            verdict, reason = override
        rows.append({
            "verdict": verdict,
            "reason": reason,
            "playlist_index": record["playlist_index"],
            "source_index": record["source_index"],
            "playlist_name": record["playlist_name"],
            "source_artist": clean_encoding(record["source_artist"]),
            "source_title": clean_encoding(record["source_title"]),
            "source_album": clean_encoding(record["source_album"]),
            "score": record["score"],
            "matched_artists": clean_encoding(record["matched_artists"]),
            "matched_title": clean_encoding(record["matched_title"]),
            "matched_album": clean_encoding(record["matched_album"]),
            "spotify_url": record["spotify_url"],
            "error": clean_encoding(record["error"]),
            "title_overlap": f"{overlap_ratio(record['source_title'], record['matched_title']):.2f}",
            "album_overlap": f"{overlap_ratio(record['source_album'], record['matched_album']):.2f}",
        })

    AUDIT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    counts = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    print(f"Wrote {AUDIT_CSV}")
    print(counts)


if __name__ == "__main__":
    main()
