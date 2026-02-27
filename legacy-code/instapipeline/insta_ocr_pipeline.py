#!/usr/bin/env python3
"""
insta_ocr_pipeline.py (refactored)

Refactored from original pipeline to expose a clean API function:
    process_post_with_reader(L, reader, shortcode, outdir, ...)

Supports:
 - Standalone CLI usage (same as before): provide an Instagram post URL and outdir
 - Import & reuse: create a single Instaloader instance and an OCR reader, then call
   process_post_with_reader repeatedly (recommended for batch runs / collection processing)

Outputs (per post folder):
 - ocr_results.json  (detailed)
 - pages_text.json   ({filename: text})
 - combined_text.txt (concatenated clean text + caption)

Dependencies:
 - instaloader, easyocr (or pytesseract), opencv-python, pillow, numpy
 - pip examples: pip install instaloader easyocr opencv-python-headless pillow numpy
"""

import os
import sys
import json
import argparse
import logging
import re
import statistics
import time
from pathlib import Path

import glob
from PIL import Image
import numpy as np
import cv2

# choose backend via env var (default easyocr)
OCR_BACKEND = os.environ.get("OCR_BACKEND", "easyocr")

if OCR_BACKEND == "easyocr":
    import easyocr
else:
    import pytesseract

import instaloader
from instaloader import Post

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ==========================================
# 1. Utilities & Instaloader Logic
# ==========================================
def shortcode_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def download_post(shortcode: str, target_folder: str, username=None, password=None, L: instaloader.Instaloader = None):
    """
    Download post to target_folder. If an Instaloader instance L is provided, reuse it.
    Otherwise create a temporary Instaloader instance (login optional).
    Returns the instaloader.Post object.
    """
    created_local_L = False
    if L is None:
        L = instaloader.Instaloader(dirname_pattern=target_folder, download_video_thumbnails=False, quiet=True)
        created_local_L = True
        if username and password:
            try:
                logger.info("Attempting login for Instaloader (temporary instance)...")
                L.login(username, password)
            except Exception as e:
                logger.warning("Instaloader login failed (temporary): %s", e)

    try:
        post = Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target=target_folder)
    except instaloader.exceptions.QueryReturnedNotFoundException as e:
        logger.error("Post not found or shortcode invalid: %s", e)
        raise
    except Exception as e:
        logger.error("Instaloader download error: %s", e)
        raise
    finally:
        # if we created an ephemeral L, try to cleanup (no real session saved)
        if created_local_L:
            pass

    return post


def list_media_files(folder):
    """
    Lists all media files and sorts them by filename to ensure
    Instagram carousel order (1, 2, 3...) is preserved regardless of extension.
    """
    exts = ("*.jpg", "*.jpeg", "*.png", "*.mp4", "*.mov")
    files = []
    for ext in exts:
        files.extend([str(p) for p in Path(folder).rglob(ext)])
    return sorted(files)


def extract_frames_from_video(video_path, out_dir, every_n_frames=10, max_frames=50):
    os.makedirs(out_dir, exist_ok=True)
    vidcap = cv2.VideoCapture(video_path)
    frames = []
    idx = 0
    saved = 0
    while vidcap.isOpened():
        success, image = vidcap.read()
        if not success:
            break
        if idx % every_n_frames == 0:
            fname = os.path.join(out_dir, f"frame_{idx:06d}.jpg")
            cv2.imwrite(fname, image)
            frames.append(fname)
            saved += 1
            if saved >= max_frames:
                break
        idx += 1
    vidcap.release()
    return frames


# ==========================================
# 2. Advanced Image Preprocessing
# ==========================================
def preprocess_image_cv(img):
    h, w = img.shape[:2]
    # Avoid zero-dimension issues
    if h == 0 or w == 0:
        return img
    # Upscale
    img = cv2.resize(img, (w*2, h*2), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # CLAHE
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    gray = clahe.apply(gray)
    # Denoise
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    # Adaptive Threshold
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 15, 11)
    # Morph Close
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3,3))
    closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
    return closed


# ==========================================
# 3. Text Reconstruction Logic
# ==========================================
def x_left(box): return min([p[0] for p in box])
def x_right(box): return max([p[0] for p in box])
def y_center(box): return sum([p[1] for p in box]) / len(box)

def is_noise_token(tok):
    t = tok.strip()
    if not t: return True
    if re.fullmatch(r"[\W_]+", t) and len(t) <= 3:
        return True
    return False

def merge_contractions(tokens):
    merged = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if i+1 < len(tokens):
            nxt = tokens[i+1]
            if (t.endswith("'") or t.endswith("’")) and re.match(r"^[A-Za-z]{1,3}$", nxt):
                merged.append(t + nxt); i += 2; continue
            if re.match(r"^[A-Za-z]+$", t) and (nxt.startswith("'") or nxt.startswith("’")):
                merged.append(t + nxt); i += 2; continue
        merged.append(t); i += 1
    return merged

def reconstruct_from_readtext(read_results, y_tol=30, gap_threshold_factor=0.45):
    items = []
    for box, text, conf in read_results:
        txt = (text or "").strip()
        if not txt: continue
        try:
            yc = y_center(box); xl = x_left(box); xr = x_right(box); w = xr - xl
        except Exception:
            yc = 0; xl = 0; xr = 0; w = 0
        items.append({"box": box, "text": txt, "yc": yc, "xl": xl, "xr": xr, "w": w, "conf": conf})
    
    if not items: return ""
    
    items = sorted(items, key=lambda x: (x['yc'], x['xl']))
    lines = []
    cur = [items[0]]
    for it in items[1:]:
        if abs(it['yc'] - cur[-1]['yc']) <= y_tol:
            cur.append(it)
        else:
            lines.append(cur); cur = [it]
    if cur: lines.append(cur)
    
    out_lines = []
    for line in lines:
        widths = [max(1, itm['w']) for itm in line]
        median_w = statistics.median(widths) if widths else 20
        line_sorted = sorted(line, key=lambda x: x['xl'])
        pieces = []
        for idx, it in enumerate(line_sorted):
            txt = it['text']
            if is_noise_token(txt): continue
            if idx == 0:
                pieces.append({"text": txt, "xl": it['xl'], "xr": it['xr']})
            else:
                prev = pieces[-1]
                gap = it['xl'] - prev['xr']
                if gap < median_w * gap_threshold_factor or txt.startswith("'") or prev['text'].endswith("-"):
                    prev['text'] = prev['text'] + txt
                    prev['xr'] = it['xr']
                else:
                    pieces.append({"text": txt, "xl": it['xl'], "xr": it['xr']})
        
        tokens = [p['text'] for p in pieces]
        tokens = merge_contractions(tokens)
        line_text = " ".join(tokens)
        line_text = re.sub(r"\s+([,.:;!?])", r"\1", line_text)
        line_text = line_text.replace("’", "'").strip()
        out_lines.append(line_text)
    
    full = "\n".join(out_lines)
    full = re.sub(r"\byou\s+'?ve\b", "you've", full, flags=re.IGNORECASE)
    full = re.sub(r"\bThat\s+'?s\b", "That's", full, flags=re.IGNORECASE)
    return full

def make_box_json_safe(box):
    try:
        arr = np.array(box).astype(int)
        return arr.tolist()
    except Exception:
        return box


# ==========================================
# 4. Main OCR Pipelines (image/video)
# ==========================================
def run_smart_easyocr(path, reader):
    """
    Run two passes (original + preprocessed) using provided reader (easyocr.Reader),
    pick the better one by length/confidence, return (clean_text, formatted_raw).
    formatted_raw: list of {box, text, confidence}
    """
    img = cv2.imread(path)
    if img is None: return "", []
    
    # 1. Original Pass
    raw_orig = reader.readtext(img, detail=1)
    text_orig = reconstruct_from_readtext(raw_orig)
    
    # 2. Preprocessed Pass
    pre_img = preprocess_image_cv(img)
    raw_pre = reader.readtext(pre_img, detail=1)
    text_pre = reconstruct_from_readtext(raw_pre)

    def tot_conf(res): return sum([r[2] for r in res]) if res else 0.0
    
    # Preference logic
    if len(text_pre) >= len(text_orig) or tot_conf(raw_pre) >= tot_conf(raw_orig):
        best_text, best_raw = text_pre, raw_pre
    else:
        best_text, best_raw = text_orig, raw_orig

    formatted_raw = []
    for box, text, conf in best_raw:
        formatted_raw.append({
            "box": make_box_json_safe(box),
            "text": str(text),
            "confidence": float(conf) if conf is not None else None
        })
    return best_text.strip(), formatted_raw

def ocr_image_tesseract(path):
    pil = Image.open(path).convert("RGB")
    text = pytesseract.image_to_string(pil, config="--psm 6")
    return str(text).strip(), [{"box": None, "text": str(text), "confidence": None}]


# ==========================================
# 5. Output Formatters & Mapping
# ==========================================
class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer, np.floating)): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        return super().default(obj)

def build_pages_exact_text(media_entries):
    """
    Constructs {filename: text} map with robust fallbacks.
    """
    pages_map = {}
    for entry in media_entries:
        p = entry.get("path") or ""
        filename = os.path.basename(p) if p else "unknown"
        text = ""

        # Priority 1: Clean Text (Result of Smart OCR)
        if entry.get("clean_text"):
            text = entry["clean_text"]
        
        # Priority 2: Video Handling (if clean_text is empty/video)
        elif entry.get("type") == "video":
            frames = entry.get("ocr_frames", [])
            # Combine all frame texts
            text = "\n\n".join([f.get("clean_text","").strip() for f in frames if f.get("clean_text","").strip()])
            
            # Fallback: Representative frame (first available) if no text found in frames
            if not text:
                frames_dir = entry.get("frames_dir")
                if frames_dir and os.path.isdir(frames_dir):
                    files = sorted([os.path.join(frames_dir, f) for f in os.listdir(frames_dir) if f.lower().endswith(('.jpg','.png'))])
                    if files:
                        pass 

        # Priority 3: Raw OCR Fallback (if smart OCR failed but raw data exists)
        if not text:
            raw = entry.get("ocr", [])
            if isinstance(raw, list):
                collected = []
                for it in raw:
                    if isinstance(it, dict):
                        t = it.get("text","").strip()
                        if t: collected.append(t)
                text = "\n".join(collected)

        pages_map[filename] = text
    return pages_map

def save_combined_text(folder, results):
    """
    Saves 'combined_text.txt': All Pages (Sorted) + Caption
    """
    media_entries = results.get("media", [])
    caption = results.get("caption", "")

    # Sort strictly by path to ensure Page 1 -> Page 2 -> Page 3
    sorted_media = sorted(media_entries, key=lambda x: x.get("path", ""))

    combined_lines = []

    # 1. Add Image Texts
    for entry in sorted_media:
        text = entry.get("clean_text", "").strip()
        if text:
            combined_lines.append(text)
    
    # 2. Add Caption
    if caption and caption.strip():
        combined_lines.append("\n" + caption.strip())

    full_text = "\n".join(combined_lines)
    
    out_path = os.path.join(folder, "combined_text.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full_text)
    
    return out_path


# ==========================================
# 6. New: Modularized post processing function
# ==========================================
def process_post_with_reader(L: instaloader.Instaloader, reader, shortcode: str, outdir: str,
                             username: str = None, password: str = None,
                             ocr_backend: str = None,
                             max_video_frames: int = 80,
                             frame_step: int = 10,
                             sleep_between_frame_ocr: float = 0.1):
    """
    High-level function to:
      - ensure post is downloaded (reusing a provided Instaloader instance L)
      - run OCR on all media using provided 'reader' (for EasyOCR). If ocr_backend == 'pytesseract',
        uses pytesseract functions instead.
      - save per-post JSONs and combined text
    Returns: (results_dict, paths_dict)
      - results_dict: same structure as previously produced (detailed)
      - paths_dict: { "ocr_json": ..., "pages_json": ..., "combined_txt": ... }
    """
    if ocr_backend is None:
        ocr_backend = os.environ.get("OCR_BACKEND", "easyocr")

    folder = os.path.join(outdir, f"post_{shortcode}")
    os.makedirs(folder, exist_ok=True)

    # Download post if not present
    media_files = list_media_files(folder)
    if not media_files:
        logger.info("Downloading post %s into %s ...", shortcode, folder)
        # use provided L if available, else call download_post which will create a temp L
        try:
            if L is not None:
                post = Post.from_shortcode(L.context, shortcode)
                L.download_post(post, target=folder)
            else:
                post = download_post(shortcode, folder, username=username, password=password, L=None)
        except Exception as e:
            logger.exception("Failed to download post %s: %s", shortcode, e)
            raise

    # Try to extract caption / owner from instaloader-written json metadata (if any)
    results = {"shortcode": shortcode, "media": [], "caption": "", "owner": None}
    try:
        # Search for any JSON files in the folder that might be instaloader meta
        for jf in Path(folder).rglob("*.json"):
            try:
                with open(jf, "r", encoding="utf-8") as fh:
                    j = json.load(fh)
                if isinstance(j, dict):
                    # Instaloader metadata shape can vary; try common keys
                    caption = None
                    if "node" in j and isinstance(j["node"], dict):
                        # GraphQL node
                        eds = j["node"].get("edge_media_to_caption", {}).get("edges", [])
                        if eds:
                            caption = eds[0].get("node", {}).get("text", "")
                    if not caption and "caption" in j:
                        caption = j.get("caption")
                    if caption:
                        results["caption"] = caption
                    if not results["owner"] and "owner" in j:
                        results["owner"] = j.get("owner")
            except Exception:
                continue
    except Exception:
        pass

    # Prepare OCR backend
    ocr_backend = ocr_backend or OCR_BACKEND

    media_files = list_media_files(folder)
    logger.info("Processing media files: %s", media_files)

    for m in media_files:
        entry = {"path": str(m), "type": None, "ocr": [], "clean_text": ""}
        lower = str(m).lower()

        try:
            if lower.endswith((".jpg", ".jpeg", ".png")):
                entry["type"] = "image"
                if ocr_backend == "easyocr":
                    # reader expected to be an easyocr.Reader instance
                    clean_txt, raw_data = run_smart_easyocr(m, reader)
                else:
                    clean_txt, raw_data = ocr_image_tesseract(m)
                entry["ocr"] = raw_data
                entry["clean_text"] = clean_txt

            elif lower.endswith((".mp4", ".mov", ".mkv")):
                entry["type"] = "video"
                frames_dir = os.path.join(folder, "frames_" + Path(m).stem)
                entry["frames_dir"] = frames_dir

                frames = extract_frames_from_video(m, frames_dir, every_n_frames=frame_step, max_frames=max_video_frames)
                entry["frames"] = frames

                frame_results = []
                for f in frames:
                    if ocr_backend == "easyocr":
                        f_clean, f_raw = run_smart_easyocr(f, reader)
                    else:
                        f_clean, f_raw = ocr_image_tesseract(f)
                    frame_results.append({"frame": f, "clean_text": f_clean, "ocr_raw": f_raw})
                    time.sleep(sleep_between_frame_ocr)

                entry["ocr_frames"] = frame_results
                entry["clean_text"] = "\n".join([fr["clean_text"] for fr in frame_results if fr["clean_text"]])

                # fallback: first frame explicit attempt
                if not entry["clean_text"] and frames:
                    rep_frame = frames[0]
                    if ocr_backend == "easyocr":
                        rep_clean, rep_raw = run_smart_easyocr(rep_frame, reader)
                    else:
                        rep_clean, rep_raw = ocr_image_tesseract(rep_frame)
                    if rep_clean:
                        entry["clean_text"] = rep_clean
            else:
                entry["type"] = "unknown"
        except Exception as e:
            logger.exception("OCR failed on %s: %s", m, e)
            entry["ocr"] = [{"error": str(e)}]

        results["media"].append(entry)

    # Save outputs
    json_path = os.path.join(folder, "ocr_results.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, cls=NpEncoder)

    pages_map = build_pages_exact_text(results["media"])
    pages_path = os.path.join(folder, "pages_text.json")
    with open(pages_path, "w", encoding="utf-8") as fh:
        json.dump(pages_map, fh, ensure_ascii=False, indent=2)

    combined_path = save_combined_text(folder, results)

    paths = {"ocr_json": json_path, "pages_json": pages_path, "combined_txt": combined_path}
    return results, paths


# ==========================================
# 7. Backwards-compatible CLI (single URL)
# ==========================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", help="Instagram post URL")
    ap.add_argument("--shortcode", help="Instagram post shortcode (alternative to URL)")
    ap.add_argument("--outdir", default="./output", help="where to save downloads and text")
    ap.add_argument("--insta-user", default=None, help="instagram username (optional)")
    ap.add_argument("--insta-pass", default=None, help="instagram password (optional)")
    ap.add_argument("--ocr-backend", default=OCR_BACKEND, choices=["easyocr", "pytesseract"])
    ap.add_argument("--frame-step", type=int, default=10)
    ap.add_argument("--max-frames", type=int, default=80)
    args = ap.parse_args()

    if not args.url and not args.shortcode:
        ap.print_help()
        sys.exit(1)

    sc = args.shortcode or shortcode_from_url(args.url)
    folder = os.path.join(args.outdir, f"post_{sc}")
    os.makedirs(folder, exist_ok=True)

    # create a reusable Instaloader instance to avoid repeated login
    L = instaloader.Instaloader(dirname_pattern=args.outdir, download_video_thumbnails=False, quiet=True)
    session_file = os.path.join(args.outdir, "instaloader_session")
    try:
        # Attempt to reuse saved session if exists (best-effort)
        if args.insta_user and os.path.exists(session_file):
            try:
                L.load_session_from_file(args.insta_user, filename=session_file)
            except Exception:
                pass
        # Try to login (non-fatal if fails for public posts)
        if args.insta_user and args.insta_pass:
            try:
                L.login(args.insta_user, args.insta_pass)
                L.save_session_to_file(session_file)
            except Exception as e:
                logger.warning("Instaloader login failed (CLI): %s", e)
    except Exception:
        pass

    # Create OCR reader once (if easyocr)
    reader = None
    ocr_backend = args.ocr_backend or OCR_BACKEND
    if ocr_backend == "easyocr":
        try:
            reader = easyocr.Reader(["en"], gpu=False)
        except Exception as e:
            logger.exception("Failed to initialize EasyOCR reader: %s", e)
            raise

    # Run processing using the new modular function
    results, paths = process_post_with_reader(L=L, reader=reader, shortcode=sc, outdir=args.outdir,
                                             username=args.insta_user, password=args.insta_pass,
                                             ocr_backend=ocr_backend, max_video_frames=args.max_frames,
                                             frame_step=args.frame_step)

    # Print final JSON summary path (same as before)
    out = {
        "status": "ok",
        "json_detailed": paths["ocr_json"],
        "json_clean": paths["pages_json"],
        "txt_combined": paths["combined_txt"]
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
