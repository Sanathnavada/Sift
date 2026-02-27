#!/usr/bin/env python3
"""
Directory tree printer (subdirectory-aware).

Usage:
    python tree.py              # print from project root
    python tree.py <subdir>     # print from specific subdirectory

Example:
    python tree.py src
"""
from pathlib import Path
import sys

DEFAULT_IGNORES = {
    "__pycache__",
    ".git",
    "venv",
    ".venv",
    "node_modules",
    ".idea",
    ".pytest_cache",
    "cache",
    "archive",
    "mind_archive",
}

# Folders that should be listed but not traversed into.
# Default includes "post_" as you requested (folders that begin with post_).
SKIP_TRAVERSE_PREFIXES = {"post_"}
SKIP_TRAVERSE_SUFFIXES = set()  # add suffixes if you want to skip by ending, e.g. {"_bak"}

def _matches_any_prefix(name: str, prefixes: set[str]) -> bool:
    return any(name.startswith(pref) for pref in prefixes)

def _matches_any_suffix(name: str, suffixes: set[str]) -> bool:
    return any(name.endswith(suf) for suf in suffixes)

def tree(
    dir_path: Path,
    prefix: str = "",
    ignore_names: set[str] = DEFAULT_IGNORES,
    skip_traverse_prefixes: set[str] = SKIP_TRAVERSE_PREFIXES,
    skip_traverse_suffixes: set[str] = SKIP_TRAVERSE_SUFFIXES,
):
    try:
        entries = sorted(
            (p for p in dir_path.iterdir() if p.name not in ignore_names),
            key=lambda p: (p.is_file(), p.name.lower()),
        )
    except PermissionError:
        print(prefix + "└── [permission denied]")
        return

    for i, p in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "

        if p.is_dir():
            print(prefix + connector + p.name + "/")
            # Do not recurse if it's a symlink or its name matches skip patterns.
            if p.is_symlink():
                continue
            if _matches_any_prefix(p.name, skip_traverse_prefixes) or _matches_any_suffix(p.name, skip_traverse_suffixes):
                # intentionally do not recurse into this directory
                continue
            # otherwise recurse
            new_prefix = prefix + ("    " if is_last else "│   ")
            tree(p, new_prefix, ignore_names, skip_traverse_prefixes, skip_traverse_suffixes)
        else:
            print(prefix + connector + p.name)

def find_subdir(root: Path, name: str) -> Path | None:
    for p in root.iterdir():
        if p.is_dir() and p.name == name:
            return p
    return None

def main():
    root = Path(".").resolve()

    # No subdir provided → print from root
    if len(sys.argv) == 1:
        print(root.name + "/")
        tree(root)  # uses defaults including SKIP_TRAVERSE_PREFIXES
        return

    # Subdir provided → behave as before
    if len(sys.argv) == 2:
        subdir_name = sys.argv[1]
        target = find_subdir(root, subdir_name)

        if target is None:
            print(f"Subdirectory '{subdir_name}' not found in {root}")
            sys.exit(1)

        print(target.name + "/")
        tree(target)
        return

    print("Usage: python tree.py [subdirectory_name]")
    sys.exit(1)

if __name__ == "__main__":
    main()
