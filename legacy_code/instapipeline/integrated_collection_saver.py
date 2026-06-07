#!/usr/bin/env python3
"""
integrated_collection_runner.py (v8: High-Quality Audio & Smart Filtering)

Features:
 1. GPU Acceleration (CUDA) for Audio.
 2. Reels/Videos -> Audio Transcript ONLY (No messy visual OCR).
 3. Images/Carousels -> Visual OCR (High accuracy).
 4. Upgraded Audio Model: Uses 'medium' Whisper model for better accuracy.

Usage:
  python integrated_collection_runner.py --username USER --password PASS --outdir ./mind_archive
"""


# No grand gestures.
# Just long walks, steady presence, warm hugs, and traveling side by side.
# If that sounds like enough to you,
# oh my dear anuuuuuuuu…
# will you be my Valentine? 🥰❤️

import os
import sys
import time
import json
import argparse
import logging
import requests
import warnings
import numpy as np
from pathlib import Path

# --- Third-party libraries ---
try:
    from instagrapi import Client
    from instagrapi.exceptions import TwoFactorRequired
except ImportError:
    sys.exit("Error: 'instagrapi' not installed. Run: pip install instagrapi")

try:
    import cv2
except ImportError:
    cv2 = None
    sys.exit("Error: 'opencv-python' not installed.")

import easyocr
try:
    import whisper
except ImportError:
    sys.exit("Error: 'openai-whisper' not installed. Run: pip install openai-whisper\nAlso ensure FFmpeg is installed.")

# Suppress warnings
warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ==========================================
# 1. Advanced Image Preprocessing
# ==========================================
def preprocess_image_cv(img):
    h, w = img.shape[:2]
    if h == 0 or w == 0: return img
    img = cv2.resize(img, (w*2, h*2), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    gray = clahe.apply(gray)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 15, 11)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3,3))
    closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
    return closed

# ==========================================
# 2. Text Reconstruction
# ==========================================
def reconstruct_from_readtext(read_results, y_tol=30):
    items = []
    for box, text, conf in read_results:
        txt = (text or "").strip()
        if not txt: continue
        try:
            yc = sum([p[1] for p in box]) / len(box)
            xl = min([p[0] for p in box])
        except: yc=0; xl=0
        items.append({"box": box, "text": txt, "yc": yc, "xl": xl, "conf": conf})
    
    if not items: return ""
    items = sorted(items, key=lambda x: (x['yc'], x['xl']))
    
    lines = []
    cur = [items[0]]
    for it in items[1:]:
        if abs(it['yc'] - cur[-1]['yc']) <= y_tol: cur.append(it)
        else: lines.append(cur); cur = [it]
    if cur: lines.append(cur)
    
    out_lines = []
    for line in lines:
        line_sorted = sorted(line, key=lambda x: x['xl'])
        tokens = [x['text'] for x in line_sorted]
        out_lines.append(" ".join(tokens))
    return "\n".join(out_lines)

def make_box_json_safe(box):
    try: return np.array(box).astype(int).tolist()
    except: return box

def run_smart_easyocr(path, reader):
    img = cv2.imread(path)
    if img is None: return "", []
    
    # 1. Original Pass
    raw_orig = reader.readtext(img, detail=1)
    text_orig = reconstruct_from_readtext(raw_orig)
    
    # 2. Preprocessed Pass
    pre_img = preprocess_image_cv(img)
    raw_pre = reader.readtext(pre_img, detail=1)
    text_pre = reconstruct_from_readtext(raw_pre)
    
    # Pick best
    best_text, best_raw = (text_pre, raw_pre) if len(text_pre) >= len(text_orig) else (text_orig, raw_orig)

    formatted_raw = [{"box": make_box_json_safe(box), "text": str(text), "confidence": float(conf)} for box, text, conf in best_raw]
    return best_text.strip(), formatted_raw

# ==========================================
# 3. Output Formatting
# ==========================================
class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer, np.floating)): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        return super().default(obj)

def save_outputs(folder, results):
    # Save JSON
    with open(os.path.join(folder, "ocr_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, cls=NpEncoder)
    
    combined_lines = []
    
    # 1. Audio Transcript (Reels/Videos)
    if results.get("audio_transcript"):
        combined_lines.append("--- AUDIO TRANSCRIPT ---")
        combined_lines.append(results["audio_transcript"])
        combined_lines.append("")
    
    # 2. Visual Text (Images/Carousels only)
    visual_lines = []
    for entry in sorted(results.get("media", []), key=lambda x: x.get("path", "")):
        txt = entry.get("clean_text", "")
        if txt: visual_lines.append(txt)
        
    if visual_lines:
        # Only add header if we actually have visual text
        combined_lines.append("--- VISUAL TEXT ---")
        combined_lines.append("\n".join(visual_lines))
    
    full_text = "\n".join(combined_lines)
    
    with open(os.path.join(folder, "combined_text.txt"), "w", encoding="utf-8") as f:
        f.write(full_text)
    return full_text

# ==========================================
# 4. Raw API & Download
# ==========================================
def init_instagrapi_client(username, password, outdir, verification_code=None):
    cl = Client()
    settings_file = os.path.join(outdir, "instagrapi_settings.json")
    if os.path.exists(settings_file):
        try: cl.load_settings(settings_file); logger.info("Loaded session.")
        except: pass
    try:
        if verification_code: cl.login(username, password, verification_code=verification_code)
        else: cl.login(username, password)
    except TwoFactorRequired:
        code = input("Enter 2FA Code: ").strip()
        cl.login(username, password, verification_code=code)
    cl.dump_settings(settings_file)
    return cl

def fetch_raw_collection_items(cl, collection_pk, max_pages=None):
    max_id = None
    page_count = 0
    while True:
        if max_pages and page_count >= max_pages: break
        try:
            params = {"include_igtv_preview": "false"}
            if max_id: params["max_id"] = max_id
            res = cl.private_request(f"feed/collection/{collection_pk}/", params=params)
            
            items = res.get("items", [])
            for item in items:
                yield item.get("media", item)
            
            next_max_id = res.get("next_max_id")
            if not next_max_id: break
            max_id = next_max_id
            page_count += 1
            time.sleep(1) 
        except Exception as e:
            logger.error(f"Error fetching collection page: {e}")
            break

def download_file_manual(url, target_path):
    try:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(target_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
        return False

def ensure_dir(path): Path(path).mkdir(parents=True, exist_ok=True)

# ==========================================
# 5. Type Detection & Processing
# ==========================================
def get_content_type(media_dict):
    media_type = media_dict.get("media_type") 
    product_type = media_dict.get("product_type") 
    if media_type == 8: return "carousel"
    elif product_type == "clips": return "reel"
    elif media_type == 2: return "video" 
    elif media_type == 1: return "image"
    return "unknown"

def process_raw_media_entry(media_dict, folder):
    ensure_dir(folder)
    pk = media_dict.get("pk") or media_dict.get("id")
    code = media_dict.get("code")
    caption = media_dict.get("caption", {}).get("text", "") if media_dict.get("caption") else ""
    user = media_dict.get("user", {}).get("username", "unknown")
    ctype = get_content_type(media_dict)
    downloaded_files = []

    def get_url(candidates): return candidates[0].get("url") if candidates else None

    if ctype == "image":
        url = get_url(media_dict.get("image_versions2", {}).get("candidates"))
        if url:
            fname = f"00_{pk}.jpg"
            if download_file_manual(url, os.path.join(folder, fname)): downloaded_files.append(fname)

    elif ctype in ["video", "reel"]:
        versions = media_dict.get("video_versions", [])
        if versions:
            url = versions[0].get("url")
            fname = f"00_{pk}.mp4"
            if download_file_manual(url, os.path.join(folder, fname)): downloaded_files.append(fname)
    
    elif ctype == "carousel":
        carousel_media = media_dict.get("carousel_media", [])
        for idx, child in enumerate(carousel_media):
            c_pk = child.get("pk", f"{pk}_{idx}")
            c_type = child.get("media_type")
            url = None; ext = "jpg"
            if c_type == 1: url = get_url(child.get("image_versions2", {}).get("candidates"))
            elif c_type == 2:
                url = child.get("video_versions", [{}])[0].get("url"); ext = "mp4"
            if url:
                fname = f"{idx:02d}_{c_pk}.{ext}"
                if download_file_manual(url, os.path.join(folder, fname)): downloaded_files.append(fname)

    return {"shortcode": code, "pk": pk, "caption": caption, "owner": user, "type": ctype, "files": downloaded_files}

# ==========================================
# 6. Main Driver
# ==========================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--collection", default="Mind")
    ap.add_argument("--outdir", default="./mind_archive")
    ap.add_argument("--checkpoint", default="checkpoint.json")
    ap.add_argument("--combined-file", default="mind_combined.txt")
    ap.add_argument("--max-posts", type=int, default=0)
    args = ap.parse_args()

    ensure_dir(args.outdir)
    checkpoint_path = os.path.join(args.outdir, args.checkpoint)
    
    processed = set()
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, 'r') as f: processed = set(json.load(f))
        except: pass

    # --- INIT MODELS (GPU) ---
    logger.info("Loading EasyOCR (GPU)...")
    try:
        reader = easyocr.Reader(["en"], gpu=True) 
    except Exception as e:
        logger.warning(f"EasyOCR GPU failed: {e}. Using CPU.")
        reader = easyocr.Reader(["en"], gpu=False)

    logger.info("Loading Whisper Audio Model (medium - GPU)...")
    try:
        # Changed from 'base' to 'medium' for higher accuracy
        # If 'medium' is too slow or OOMs, revert to 'base'
        audio_model = whisper.load_model("medium", device="cuda" if reader.device == "cuda" else "cpu")
    except Exception as e:
        logger.error(f"Whisper Load Failed: {e}")
        sys.exit(1)

    cl = init_instagrapi_client(args.username, args.password, args.outdir)
    logger.info(f"Resolving collection '{args.collection}'...")
    try:
        col_pk = cl.collection_pk_by_name(args.collection)
    except Exception as e:
        logger.error(f"Could not find collection: {e}")
        return

    logger.info("Fetching collection items...")
    raw_item_gen = fetch_raw_collection_items(cl, col_pk)
    
    count = 0
    combined_fh = open(os.path.join(args.outdir, args.combined_file), "a", encoding="utf-8")

    for media_dict in raw_item_gen:
        code = media_dict.get("code")
        if not code: continue
        if code in processed:
            logger.info(f"Skipping {code} (Processed)")
            continue
        if args.max_posts > 0 and count >= args.max_posts: break

        ctype = get_content_type(media_dict)
        folder = os.path.join(args.outdir, f"post_{code}")
        logger.info(f"Processing {code} [Type: {ctype.upper()}]...")

        try:
            # 1. Download
            meta = process_raw_media_entry(media_dict, folder)
            files = sorted([str(p) for p in Path(folder).rglob("*") if p.suffix.lower() in ['.jpg','.jpeg','.png','.mp4','.mov']])
            
            ocr_results = {
                "shortcode": code,
                "caption": meta['caption'],
                "owner": meta['owner'],
                "type": meta['type'],
                "audio_transcript": "",
                "media": []
            }

            for fpath in files:
                entry = {"path": fpath, "type": "unknown", "clean_text": "", "ocr": []}
                
                # --- VIDEO/REEL HANDLING ---
                if fpath.endswith(('.mp4', '.mov')):
                    entry["type"] = "video"
                    
                    # A. Run Audio Transcription (Whisper)
                    logger.info("Transcribing audio...")
                    try:
                        result = audio_model.transcribe(fpath)
                        transcript = result["text"].strip()
                        if transcript:
                            ocr_results["audio_transcript"] += transcript + " "
                    except Exception as e:
                        logger.warning(f"Audio transcription failed: {e}")

                    # B. SKIP Visual OCR for Reels/Videos
                    # We leave "clean_text" empty so it doesn't appear in the visual section
                    entry["clean_text"] = "" 
                    entry["ocr_frames"] = [] # Don't even extract frames

                # --- IMAGE/CAROUSEL HANDLING ---
                else:
                    entry["type"] = "image"
                    clean, raw = run_smart_easyocr(fpath, reader)
                    entry["clean_text"] = clean
                    entry["ocr"] = raw
                
                ocr_results["media"].append(entry)

            # 4. Save
            final_text = save_outputs(folder, ocr_results)
            
            combined_fh.write(f"Post Link: https://www.instagram.com/p/{code}/\n")
            combined_fh.write(f"Type: {meta['type'].title()}\n")
            if meta['caption']: combined_fh.write(f"Caption: {meta['caption']}\n")
            combined_fh.write("-" * 20 + "\n")
            combined_fh.write(final_text + "\n")
            combined_fh.write("=" * 80 + "\n\n")
            combined_fh.flush()

            processed.add(code)
            with open(checkpoint_path, 'w') as f: json.dump(list(processed), f)
            count += 1
            
        except Exception as e:
            logger.error(f"Failed on {code}: {e}")

    combined_fh.close()
    logger.info("Done.")

if __name__ == "__main__":
    main()