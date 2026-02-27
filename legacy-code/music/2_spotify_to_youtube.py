import subprocess
import json
import re
import difflib
from pathlib import Path

# ======================= CONFIG =======================
SPOTIFY_BACKUP = Path("spotify_playlists_backup.txt")
URLS_FILE = Path("urls.txt")
CACHE_FILE = Path("youtube_matches.json")

# Number of results to fetch from YouTube search
YT_SEARCH_RESULTS = 5 
# ======================================================

def clean_file_content(content):
    """
    Removes the tags that appear in the uploaded file content
    so they don't interfere with parsing.
    """
    # Remove , , etc.
    return re.sub(r'\\"+', '', content)

def parse_playlists():
    """
    Parses the text file based on the rule: 
    - Lines starting with "Playlist:" are new playlists.
    - Lines starting with numbers (e.g., "1. ", "146. ") are songs.
    """
    if not SPOTIFY_BACKUP.exists():
        print(f"❌ Error: {SPOTIFY_BACKUP} not found.")
        return []

    with SPOTIFY_BACKUP.open("r", encoding="utf-8") as f:
        raw_content = f.read()

    # Clean artifacts first
    clean_content = clean_file_content(raw_content)
    lines = clean_content.splitlines()

    playlists = []
    current_playlist = None

    # Regex to handle: "146. Song Name — Artist Name [Album] (spotify:track:id)"
    # Captures: Group 1 (Number), Group 2 (Title), Group 3 (Artist)
    # We allow for '—' (em-dash) or '-' (hyphen) as separator.
    track_pattern = re.compile(r"^(\d+)\.\s+(.+?)\s+[—\-]\s+(.+?)(?:\s+\[.*\])?\s*\(spotify:track:")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # --- 1. Detect Playlist Header ---
        if line.startswith("Playlist:"):
            # If a playlist was already being built, save it
            if current_playlist:
                playlists.append(current_playlist)
            
            current_playlist = {
                "header_lines": [line],
                "tracks": []
            }
            continue

        # --- 2. Detect Metadata Lines (ID, Owner) ---
        # These usually follow the playlist line immediately
        if current_playlist and (line.startswith("ID:") or "Owner:" in line or "Tracks" in line):
            current_playlist["header_lines"].append(line)
            continue

        # --- 3. Detect Tracks (Numbered lines) ---
        match = track_pattern.match(line)
        if match and current_playlist:
            track_num = match.group(1)
            title = match.group(2).strip()
            # Artist might contain "feat.", clean it later
            artist_raw = match.group(3).strip()
            
            # Remove [Album] if it got caught in the artist group (backup regex safety)
            artist_clean = re.sub(r"\s*\[.*?\]", "", artist_raw)
            # Remove trailing spotify id if it got caught
            artist_clean = re.sub(r"\(spotify:track:.*\)", "", artist_clean)

            # Extract main artist (remove feat.)
            artist_search = re.split(r"\(?feat\.", artist_clean, flags=re.IGNORECASE)[0].strip()
            
            current_playlist["tracks"].append({
                "number": track_num,
                "title": title,
                "artist": artist_search,
                "original_line": line
            })

    # Append the final playlist
    if current_playlist:
        playlists.append(current_playlist)

    return playlists

def yt_search(query):
    """
    Use yt-dlp to search YouTube.
    """
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--flat-playlist", 
        "--no-warnings",
        f"ytsearch{YT_SEARCH_RESULTS}:{query}"
    ]

    try:
        # errors='ignore' prevents crashes on weird characters
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore"
        )
    except Exception as e:
        print(f"    ❌ System Error: {e}")
        return []

    videos = []
    for line in result.stdout.splitlines():
        try:
            videos.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return videos

def get_similarity(s1, s2):
    return difflib.SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

def score_video(video, song_title, song_artist):
    """
    Heuristic scoring to ensure accuracy.
    """
    score = 0
    vid_title = video.get("title", "").lower()
    vid_channel = (video.get("channel") or video.get("uploader") or "").lower()
    duration = video.get("duration", 0)

    target_title = song_title.lower()
    target_artist = song_artist.lower()

    # 1. Title Match
    if target_title in vid_title:
        score += 10
    elif get_similarity(target_title, vid_title) > 0.7:
        score += 7

    # 2. Artist Match (Crucial)
    # If artist is not in title OR channel, heavy penalty
    if target_artist in vid_title or target_artist in vid_channel:
        score += 5
    else:
        # If artist is missing, only allow if title is very unique/exact match
        if get_similarity(target_title, vid_title) < 0.9:
            score -= 5

    # 3. Official Source
    if "official audio" in vid_title or "official video" in vid_title:
        score += 5
    elif "lyrics" in vid_title:
        score += 4
    elif "topic" in vid_channel: # "Artist - Topic" channels are high quality
        score += 4

    # 4. Duration Safety (Songs are rarely < 1 min or > 10 min)
    if duration is not None and isinstance(duration, (int, float)):
        if 120 <= duration <= 480:
            score += 2
        elif duration < 60:
            score -= 10 #

    # 5. Penalize "Live"/"Cover" unless requested
    bad_keywords = ["live", "cover", "remix", "reaction", "review"]
    for word in bad_keywords:
        if word in vid_title and word not in target_title:
            if word == "cover": score -= 20
            else: score -= 5

    return score

def find_best_video(track):
    # Query: "Title Artist Official Audio"
    query = f"{track['title']} {track['artist']} official audio"
    videos = yt_search(query)

    best_v = None
    best_s = -50

    for v in videos:
        s = score_video(v, track['title'], track['artist'])
        if s > best_s:
            best_s = s
            best_v = v

    # You can adjust this threshold. 
    # If score < 0, it's likely a bad match, but we return it to ensure a link exists.
    return best_v

def main():
    print("🎧 Spotify → YouTube Link Resolver")
    print("===================================")

    playlists = parse_playlists()
    
    # Load cache to resume if stopped
    cache = {}
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except:
            pass

    # Open urls.txt in Write mode to start clean
    with URLS_FILE.open("w", encoding="utf-8") as f_out:
        
        for idx, pl in enumerate(playlists):
            # Write Header
            header_block = "\n".join(pl["header_lines"])
            f_out.write(header_block + "\n")
            
            print(f"\n📂 Playlist {idx+1}/{len(playlists)}: {pl['header_lines'][0]}")
            print(f"   (Found {len(pl['tracks'])} numbered tracks)")

            for track in pl["tracks"]:
                track_key = f"{track['title']} - {track['artist']}"
                
                # Try Cache
                if track_key in cache:
                    url = cache[track_key]
                    # Print log for cached item too, to show progress
                    print(f"   Song {track['number']}: {track['title']} [CACHED]")
                else:
                    # Search
                    video = find_best_video(track)
                    
                    if video:
                        url = video.get("webpage_url") or video.get("url")
                        if url and "youtu" not in url:
                             url = f"https://www.youtube.com/watch?v={video.get('id')}"
                        
                        cache[track_key] = url
                        print(f"   Song {track['number']}: {track['title']} -> Found: {url}")
                    else:
                        url = "NO_MATCH_FOUND"
                        print(f"   Song {track['number']}: {track['title']} -> ❌ No Match")

                    # Save cache frequently
                    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
                
                # Write to file immediately
                if url and url != "NO_MATCH_FOUND":
                    f_out.write(url + "\n")
            
            # Separator between playlists
            f_out.write("\n" + "-"*60 + "\n\n")

    print("\n=================================")
    print(f"✅ Finished. Results saved to {URLS_FILE}")

if __name__ == "__main__":
    main()