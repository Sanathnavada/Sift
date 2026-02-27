import os
from pathlib import Path

# Config matching your setup
ROOT_DIR = Path("D:/Music")

def create_m3u_playlists():
    print(f"📂 Scanning {ROOT_DIR} for folders...")
    
    if not ROOT_DIR.exists():
        print("❌  folder not found!")
        return

    count = 0
    # Iterate over every folder (which represents a playlist)
    for folder in ROOT_DIR.iterdir():
        if folder.is_dir():
            playlist_name = folder.name
            
            # Find all audio files
            audio_files = [f.name for f in folder.glob("*") if f.suffix.lower() in ['.m4a', '.webm', '.mp3', '.opus']]
            
            if not audio_files:
                continue

            # Create the .m3u file inside the folder
            # Navidrome will find this and name the playlist after the filename
            m3u_path = folder / f"{playlist_name}.m3u"
            
            with open(m3u_path, "w", encoding="utf-8") as f:
                # #EXTM3U is the header required
                f.write("#EXTM3U\n")
                # Write filenames line by line
                for file in audio_files:
                    f.write(file + "\n")
            
            print(f"✅ Created Playlist: {playlist_name} ({len(audio_files)} tracks)")
            count += 1

    print(f"\n✨ Generated {count} playlists. Rescan Navidrome now!")

if __name__ == "__main__":
    create_m3u_playlists()