import json
from pathlib import Path


REPORT_JSON = Path("var/music_config/spotify_md_resolve_report.json")
AUDIT_JSON = Path("var/music_config/spotify_fuzzy_verification_fixes.json")


FIXES = {
    (15, 59): {
        "spotify_uri": "spotify:track:2alijxLnR0RCiJ5JvzhDuT",
        "spotify_url": "https://open.spotify.com/track/2alijxLnR0RCiJ5JvzhDuT",
        "matched_title": "Wet - Snoop Dogg vs David Guetta Remix",
        "matched_artists": "Snoop Dogg, David Guetta",
        "matched_album": "Doggumentary",
        "score": 100,
        "note": "Fuzzy verification corrected plain Wet to the Doggumentary remix requested by the MD.",
    },
    (15, 80): {
        "spotify_uri": "spotify:track:0IvjvDJosXy8jhmZuVd6F9",
        "spotify_url": "https://open.spotify.com/track/0IvjvDJosXy8jhmZuVd6F9",
        "matched_title": "Money Rain (Phonk Remix)",
        "matched_artists": "VTORNIK",
        "matched_album": "Money Rain (Phonk Remix)",
        "score": 100,
        "note": "Fuzzy verification corrected original Money Rain to the Phonk Remix requested by the MD.",
    },
    (18, 84): {
        "spotify_uri": "spotify:track:2Oycxb8QbPkpHTo8ZrmG0B",
        "spotify_url": "https://open.spotify.com/track/2Oycxb8QbPkpHTo8ZrmG0B",
        "matched_title": "Prisoner (feat. Dua Lipa)",
        "matched_artists": "Miley Cyrus, Dua Lipa",
        "matched_album": "Plastic Hearts",
        "score": 90,
        "note": "Fuzzy verification corrected Jax Jones remix to the original Miley Cyrus/Dua Lipa track.",
    },
    (18, 93): {
        "spotify_uri": "spotify:track:4ZtFanR9U6ndgddUvNcjcG",
        "spotify_url": "https://open.spotify.com/track/4ZtFanR9U6ndgddUvNcjcG",
        "matched_title": "good 4 u",
        "matched_artists": "Olivia Rodrigo",
        "matched_album": "SOUR",
        "score": 90,
        "note": "Fuzzy verification corrected cover version to Olivia Rodrigo original.",
    },
    (18, 119): {
        "spotify_uri": "spotify:track:100eDEmpWV5YGVCqHI0leU",
        "spotify_url": "https://open.spotify.com/track/100eDEmpWV5YGVCqHI0leU",
        "matched_title": "Audio (feat. Sia, Diplo, and Labrinth)",
        "matched_artists": "Sia, Diplo, Labrinth, LSD",
        "matched_album": "LABRINTH, SIA & DIPLO PRESENT... LSD",
        "score": 90,
        "note": "Fuzzy verification corrected CID remix to the original LSD/Sia track.",
    },
    (18, 120): {
        "spotify_uri": "spotify:track:4xigPf2sigSPmuFH3qCelB",
        "spotify_url": "https://open.spotify.com/track/4xigPf2sigSPmuFH3qCelB",
        "matched_title": "Genius (feat. Sia, Diplo, and Labrinth)",
        "matched_artists": "Sia, Diplo, Labrinth, LSD",
        "matched_album": "LABRINTH, SIA & DIPLO PRESENT... LSD",
        "score": 90,
        "note": "Fuzzy verification corrected Banx & Ranx remix to the original LSD/Sia track.",
    },
    (18, 121): {
        "spotify_uri": "spotify:track:4lJNen4SMTIJMahALc3DcB",
        "spotify_url": "https://open.spotify.com/track/4lJNen4SMTIJMahALc3DcB",
        "matched_title": "Thunderclouds (feat. Sia, Diplo, and Labrinth)",
        "matched_artists": "Sia, Diplo, Labrinth, LSD",
        "matched_album": "LABRINTH, SIA & DIPLO PRESENT... LSD",
        "score": 90,
        "note": "Fuzzy verification corrected Lost Frequencies remix to the original LSD/Sia track.",
    },
    (18, 70): {
        "spotify_uri": "spotify:track:51ceegeWmD5lSKq1IfZ9pR",
        "spotify_url": "https://open.spotify.com/track/51ceegeWmD5lSKq1IfZ9pR",
        "matched_title": "4 Minutes (feat. Justin Timberlake & Timbaland)",
        "matched_artists": "Madonna, Justin Timberlake, Timbaland",
        "matched_album": "Celebration (Deluxe Version)",
        "score": 100,
        "note": "Fuzzy verification corrected remix-container match to Celebration deluxe track requested by the MD.",
    },
}

DEMOTE = {
    (22, 25): "Fuzzy verification found Spotify result Sweaters III, not the MD track Sweaters; no exact Spotify match was found.",
}


def main() -> None:
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    audit = []
    for record in report["records"]:
        key = (record["playlist_index"], record["source_index"])
        if key in FIXES:
            fix = FIXES[key]
            audit.append({"action": "corrected", "before": dict(record), "fix": fix})
            record["status"] = "matched"
            record["score"] = fix["score"]
            record["spotify_uri"] = fix["spotify_uri"]
            record["spotify_url"] = fix["spotify_url"]
            record["matched_title"] = fix["matched_title"]
            record["matched_artists"] = fix["matched_artists"]
            record["matched_album"] = fix["matched_album"]
            record["error"] = f"fuzzy_verification: {fix['note']}"
        elif key in DEMOTE:
            audit.append({"action": "demoted", "before": dict(record), "note": DEMOTE[key]})
            record["status"] = "no_results"
            record["score"] = 0
            record["spotify_uri"] = ""
            record["spotify_url"] = ""
            record["matched_title"] = ""
            record["matched_artists"] = ""
            record["matched_album"] = ""
            record["error"] = f"fuzzy_verification: {DEMOTE[key]}"

    summary = {"total": len(report["records"]), "matched": 0, "low_confidence": 0, "no_results": 0, "api_error": 0}
    for record in report["records"]:
        summary[record["status"]] = summary.get(record["status"], 0) + 1
    report["summary"] = summary
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    AUDIT_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Applied {len(audit)} fuzzy verification fixes")
    print(summary)
    print(f"Audit: {AUDIT_JSON}")


if __name__ == "__main__":
    main()
