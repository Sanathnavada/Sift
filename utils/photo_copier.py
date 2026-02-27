#!/usr/bin/env python3
"""
photo_copier.py — adjacency-based sequential selector

Each token's provided order reflects real capture sequence.
When multiple matches exist for a token, choose the one that best preserves
temporal adjacency with its neighbors (previous and next tokens),
instead of enforcing global strictly-increasing timestamps.

Usage:
python photo_copier.py --src-dirs "<folder>" --tokens "<comma list>" --out ".\\out" --dry-run --verbose
"""
from pathlib import Path
import argparse, re, shutil, sys
from datetime import datetime
import math

# A set of common image and video file extensions
IMG_EXTS = {'.jpg','.jpeg','.png','.mp4','.mov','.heic','.tif','.tiff','.gif','.bmp'}

def parse_tokens(arg:str):
    """Parses tokens from a command-line string or a file."""
    p=Path(arg)
    raw=p.read_text(encoding='utf-8',errors='ignore') if p.exists() else arg
    return [x for x in re.split(r'[\s,]+',raw.strip()) if x]

def collect_files(src_dirs):
    """Recursively scans source directories for media files and their mtimes."""
    files=[]
    for d in src_dirs:
        p=Path(d)
        print(f"[debug] scan dir: {p} exists={p.exists()}")
        if not p.exists(): 
            print(f"[warn] directory does not exist: {p}", file=sys.stderr)
            continue
        # Use rglob("*") to find all files recursively
        for f in sorted(p.rglob('*')):
            if f.is_file() and f.suffix.lower() in IMG_EXTS:
                nums=re.findall(r'\d+',f.stem)
                try: 
                    mt=f.stat().st_mtime
                except Exception as e: 
                    print(f"[warn] could not stat file {f}: {e}", file=sys.stderr)
                    mt=0
                files.append({'path':f.resolve(),'nums':nums,'mtime':mt})
    # Sort all found files by modification time
    files.sort(key=lambda x:x['mtime'])
    return files

def endswith_match(tok,nums):
    """Checks if any number in 'nums' ends with the token, ignoring leading zeros."""
    t1=tok; t2=tok.lstrip('0') or '0' # handle '0' and '00' tokens
    return any(s.endswith(t1) or s.endswith(t2) for s in nums)

def build_candidates(files,tokens):
    """Builds a list where candidates[i] is a list of files matching tokens[i]."""
    out=[]
    for t in tokens:
        c=[f for f in files if endswith_match(t,f['nums'])]
        c.sort(key=lambda x:x['mtime']) # Sort candidates by mtime
        out.append(c)
    return out

def pick_sequence_adjacent(cands, tokens, verbose=False):
    """
    Picks a sequence by minimizing local temporal adjacency gaps.
    This is a greedy, forward-pass approach.
    
    Args:
        cands (list): List of candidate lists, one for each token.
        tokens (list): The list of token strings (used for error reporting).
        verbose (bool): If True, prints detailed picking logic.
        
    Returns:
        list: The list of chosen file dictionaries.
    """
    n=len(cands)
    chosen=[None]*n
    
    # --- THIS IS THE FIX ---
    # Pre-check for any tokens that have zero candidates
    for i, curset in enumerate(cands):
        if not curset: 
            # Use the 'tokens' list passed into the function
            raise ValueError(f"No matches found for token {i} ('{tokens[i]}')")
    # --- END FIX ---

    # simple forward local-fit selection
    for i in range(n):
        curset=cands[i]
        
        if i==0:
            # For the first token, just pick the earliest candidate
            chosen[i]=curset[0]
            continue
        
        # Get previous chosen time
        tprev=chosen[i-1]['mtime']

        if i==n-1:
            # For the last token, choose the one closest to the previous
            chosen[i]=min(curset,key=lambda x:abs(x['mtime']-tprev))
            continue
        
        # For a middle token: try to stay between previous and a median of next candidates
        tnexts=[f['mtime'] for f in cands[i+1]]
        # tnexts is guaranteed to not be empty because of our pre-check
        
        tnext_median=sorted(tnexts)[len(tnexts)//2]
        
        # Target time is halfway between previous and next-median
        # This handles both tprev < tnext and tprev > tnext
        target=(tprev+tnext_median)/2
        
        # Choose the candidate from our current set closest to this target time
        chosen[i]=min(curset,key=lambda x:abs(x['mtime']-target))
        
        if verbose:
            print(f"[pick-local] token#{i} target~{datetime.fromtimestamp(target)} -> {chosen[i]['path'].name} @ {datetime.fromtimestamp(chosen[i]['mtime']).isoformat()}")
            
    return chosen

def main():
    print("Running script:",Path(__file__).resolve())
    ap=argparse.ArgumentParser(description="Adjacency-based sequential photo selector.")
    ap.add_argument('--src-dirs',nargs='+',required=True, help='One or more source directories (recursive).')
    ap.add_argument('--tokens',required=True, help='Comma/space separated tokens or a path to a file with tokens.')
    ap.add_argument('--out',required=True, help='Output directory (created if missing).')
    ap.add_argument('--dry-run',action='store_true', help='Show choices; do not copy.')
    ap.add_argument('--verbose',action='store_true', help='Print detailed picks.')
    a=ap.parse_args()

    try:
        tokens=parse_tokens(a.tokens)
        files=collect_files(a.src_dirs)
        print(f"[info] tokens: {len(tokens)} | files scanned: {len(files)}")

        cands=build_candidates(files,tokens)
        for i,c in enumerate(cands):
            print(f"[info] token[{i}]='{tokens[i]}': {len(c)} candidate(s)")

        # --- THIS IS THE FIX ---
        # Pass the 'tokens' list into the solver function
        chosen=pick_sequence_adjacent(cands, tokens, verbose=a.verbose)
        # --- END FIX ---

    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"[fatal error] An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n=== FINAL SELECTION (in provided order) ===")
    unique_files = set()
    for i,f in enumerate(chosen):
        is_duplicate = f['path'] in unique_files
        unique_files.add(f['path'])
        print(f" {i:03d}. {tokens[i]} -> {f['path']} | {datetime.fromtimestamp(f['mtime']).isoformat()}{' (DUPLICATE PICK)' if is_duplicate else ''}")

    outdir=Path(a.out).resolve()
    outdir.mkdir(parents=True,exist_ok=True)
    
    if a.dry_run:
        print(f"\n[dry-run] Would copy {len(chosen)} files to: {outdir}")
        for f in chosen: 
            print(" WOULD COPY:",f['path'].name)
        return

    print(f"\n[copy] Copying {len(chosen)} files to: {outdir}")
    copied_names = set()
    for f in chosen:
        src = f['path']
        dest = outdir / src.name
        
        # Handle potential filename collisions if two different source files have same name
        if dest.exists() and src.name not in copied_names:
            print(f" WARN: Destination file exists, skipping: {dest.name}", file=sys.stderr)
            continue
        elif src.name in copied_names:
             # This file was chosen for two different tokens, but already copied
             print(f" INFO: Already copied (duplicate token pick): {src.name}")
             continue
             
        try: 
            shutil.copy2(src, dest) # copy2 preserves metadata like timestamps
            copied_names.add(src.name)
            print(f" COPIED: {src.name}")
        except Exception as e: 
            print(f" ERROR copying {src}: {e}", file=sys.stderr)

if __name__=="__main__": 
    main()