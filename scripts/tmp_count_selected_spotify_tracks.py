from pathlib import Path


PATH = Path(r"C:\Users\Dell\Desktop\code\var\music_config\spotify_selected_track_lists.txt")


def main() -> None:
    if not PATH.exists():
        raise SystemExit(f"File not found: {PATH}")

    total = 0
    current_playlist = None
    counts: dict[str, int] = {}

    for raw_line in PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("Playlist: "):
            current_playlist = line.removeprefix("Playlist: ").strip()
            counts.setdefault(current_playlist, 0)
            continue
        if current_playlist and line and line[0].isdigit():
            prefix = line.split(".", 1)[0]
            if prefix.isdigit():
                counts[current_playlist] += 1
                total += 1

    print(f"File: {PATH}")
    print(f"Playlists: {len(counts)}")
    print(f"Total songs: {total}")
    print()
    for playlist, count in counts.items():
        print(f"{count:5d}  {playlist}")


if __name__ == "__main__":
    main()
