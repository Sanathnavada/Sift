#!/usr/bin/env python3
import os
import sys
import hashlib
import argparse
import ctypes
import platform
from subprocess import check_call

# --- Configuration: file extensions to consider ---
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif'}
VIDEO_EXTS = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv'}

# --- Permission Handling ---
def is_admin():
    """
    Return True if the script is running with administrative/root privileges.
    """
    if os.name == 'nt':  # Windows
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            return False
    else:  # Unix/Linux/Mac
        return os.geteuid() == 0

def elevate_windows():
    """
    Relaunch the current script with admin rights on Windows.
    """
    script = os.path.abspath(sys.argv[0])
    params = " ".join([f'"{arg}"' for arg in sys.argv[1:]])
    # ShellExecuteW returns >32 on success
    hinstance = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{script}" {params}', None, 1
    )
    if hinstance <= 32:
        print("Failed to elevate privileges. Please run as Administrator.")
    sys.exit(0)

# --- File Hashing & Deduplication ---
def file_hash(path, chunk_size=8192):
    """Compute SHA-256 hash of a file, reading in chunks."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()

def find_and_delete_dupes(root_dir):
    seen = {}
    deleted = []

    for dirpath, _, filenames in os.walk(root_dir):
        for name in filenames:
            _, ext = os.path.splitext(name.lower())
            if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
                full_path = os.path.join(dirpath, name)
                try:
                    h = file_hash(full_path)
                except Exception as e:
                    print(f"⚠️ Could not hash '{full_path}': {e}")
                    continue

                if h in seen:
                    # duplicate — delete
                    try:
                        os.remove(full_path)
                        deleted.append(full_path)
                        print(f"🗑 Deleted duplicate: {full_path}")
                    except Exception as e:
                        print(f"❌ Failed to delete '{full_path}': {e}")
                else:
                    seen[h] = full_path

    print(f"\nDone. {len(deleted)} duplicate(s) removed.")

# --- Main ---
def main():
    parser = argparse.ArgumentParser(
        description="Recursively find & delete duplicate image/video files."
    )
    parser.add_argument(
        "directory", help="Root directory to scan", type=str
    )
    args = parser.parse_args()

    root = os.path.abspath(args.directory)
    if not os.path.isdir(root):
        print(f"Error: '{root}' is not a directory.")
        sys.exit(1)

    # Check/admin elevation
    if not is_admin():
        if os.name == 'nt':
            print("⚠️  Need Administrator privileges on Windows. Trying to elevate...")
            elevate_windows()
        else:
            print("⚠️  Please rerun with root privileges, e.g.:")
            print(f"    sudo {sys.executable} {' '.join(sys.argv)}")
            sys.exit(1)

    print(f"Scanning & deduplicating under: {root}")
    find_and_delete_dupes(root)

if __name__ == "__main__":
    main()
