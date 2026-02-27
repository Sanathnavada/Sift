#!/usr/bin/env python3
"""
remove_empty_dirs.py

Usage:
  python remove_empty_dirs.py /path/to/folder        # real run
  python remove_empty_dirs.py /path/to/folder --dry  # dry-run (no deletion)
  python remove_empty_dirs.py /path/to/folder -v     # verbose
"""

import os
import argparse
import sys

def is_root_path(path):
    path = os.path.abspath(path)
    drive, tail = os.path.splitdrive(path)
    # Windows: drive like 'C:' and tail '\' -> root
    if drive and tail in ('\\', '/'):
        return True
    # Unix-like
    if path == os.path.abspath(os.sep):
        return True
    return False

def remove_empty_dirs(root, dry_run=False, verbose=False):
    if not os.path.exists(root):
        raise FileNotFoundError(f"Path does not exist: {root}")
    if not os.path.isdir(root):
        raise NotADirectoryError(f"Not a directory: {root}")

    if is_root_path(root):
        raise ValueError("Refusing to run on root path for safety.")

    deleted = []
    # os.walk with topdown=False -> children processed before parents (bottom-up)
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        try:
            # Treat directory as empty only if it has no files and no subdirectories
            if not dirnames and not filenames:
                if dry_run:
                    if verbose:
                        print(f"[DRY] would remove: {dirpath}")
                    deleted.append(dirpath)
                else:
                    try:
                        os.rmdir(dirpath)
                        print(f"removed: {dirpath}")
                        deleted.append(dirpath)
                    except OSError as e:
                        # Could be permission or race condition
                        print(f"skipped (rmdir failed): {dirpath} -> {e}")
        except Exception as e:
            print(f"error inspecting {dirpath}: {e}")

    return deleted

def main():
    p = argparse.ArgumentParser(description="Recursively remove empty subdirectories (bottom-up).")
    p.add_argument("folder", help="Root folder to scan")
    p.add_argument("--dry", action="store_true", help="Dry-run: show what would be removed")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = p.parse_args()

    folder = os.path.abspath(args.folder)
    try:
        deleted = remove_empty_dirs(folder, dry_run=args.dry, verbose=args.verbose)
        print(f"\nDone. {'Would remove' if args.dry else 'Removed'} {len(deleted)} directories.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
