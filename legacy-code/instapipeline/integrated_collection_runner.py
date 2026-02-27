#!/usr/bin/env python3
"""
integrated_collection_runner.py (v6: Ordered Slides & Clean Output)

Features:
 1. GPU Acceleration (CUDA) enabled.
 2. Bypasses strict validation (Raw JSON fetch).
 3. Smart Detection: Distinguishes Carousels, Reels, and Feed Videos.
 4. Filters: Can skip Reels or process only Carousels.
 5. Fixes: 
    - Carousel slides are processed in exact sequence (01, 02, 03...).
    - Caption appears only once at the top.

Usage:
  # Process everything (GPU accelerated):
  python integrated_collection_runner.py --username USER --password PASS --outdir ./mind_archive

  # Skip Reels (Fast videos) and only do text posts/carousels:
  python integrated_collection_runner.py --username USER --password PASS --skip-reels
"""

import os
import sys
import time
import json
import argparse
import logging
import requests
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

def extract_frames_from_video(video_path, output_folder, max_frames=60, step=15):
    if cv2 is None: return []
    frames_dir = os.path.join(output_folder, "frames_" + Path(video_path).stem)
    os.makedirs(frames_dir, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened(): return []
    saved_frames = []
    count = 0
    saved_count = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret: break
        if count % step == 0:
            frame_name = f"frame_{count:06d}.jpg"
            frame_path = os.path.join(frames_dir, frame_name)
            cv2.imwrite(frame_path, frame)
            saved_frames.append(frame_path)
            saved_count += 1
            if saved_count >= max_frames: break
        count += 1
    cap.release()
    return saved_frames

# ==========================================
# 2. Smart Text Reconstruction
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
    
    # Combined Text
    combined_lines = []
    # Sort files by path (relies on the 00_, 01_ prefix added in process_raw_media_entry)
    for entry in sorted(results.get("media", []), key=lambda x: x.get("path", "")):
        txt = entry.get("clean_text") or "\n".join([f.get("clean_text","") for f in entry.get("ocr_frames", [])])
        if txt: combined_lines.append(txt)
    
    # NOTE: Caption append removed here to prevent duplication (it is added in main)
    
    full_text = "\n".join(combined_lines)
    with open(os.path.join(folder, "combined_text.txt"), "w", encoding="utf-8") as f:
        f.write(full_text)
    return full_text

# ==========================================
# 4. Raw API & Download Logic
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
    media_type = media_dict.get("media_type") # 1=Img, 2=Vid, 8=Carousel
    product_type = media_dict.get("product_type") # clips, feed, carousel_container
    
    if media_type == 8:
        return "carousel"
    elif product_type == "clips":
        return "reel"
    elif media_type == 2:
        return "video" 
    elif media_type == 1:
        return "image"
    return "unknown"

def process_raw_media_entry(media_dict, folder):
    """
    Parses raw dict and downloads files. 
    ENHANCEMENT: Adds index prefixes (00_, 01_) to filenames to ensure 
    sorted() respects the correct slide order.
    """
    ensure_dir(folder)
    
    pk = media_dict.get("pk") or media_dict.get("id")
    code = media_dict.get("code")
    caption = media_dict.get("caption", {}).get("text", "") if media_dict.get("caption") else ""
    user = media_dict.get("user", {}).get("username", "unknown")
    
    ctype = get_content_type(media_dict)
    downloaded_files = []

    def get_url(candidates):
        return candidates[0].get("url") if candidates else None

    # Logic to handle different types
    if ctype == "image":
        url = get_url(media_dict.get("image_versions2", {}).get("candidates"))
        if url:
            # Prefix 00_ for consistency
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
            url = None
            ext = "jpg"
            
            if c_type == 1:
                url = get_url(child.get("image_versions2", {}).get("candidates"))
            elif c_type == 2:
                url = child.get("video_versions", [{}])[0].get("url")
                ext = "mp4"

            if url:
                # IMPORTANT: Save with Index Prefix (00_, 01_, 02_)
                fname = f"{idx:02d}_{c_pk}.{ext}"
                if download_file_manual(url, os.path.join(folder, fname)): downloaded_files.append(fname)

    return {
        "shortcode": code,
        "pk": pk,
        "caption": caption,
        "owner": user,
        "type": ctype,
        "files": downloaded_files
    }

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
    
    # Filters
    ap.add_argument("--skip-reels", action="store_true", help="Skip processing 'clips' (Reels).")
    ap.add_argument("--carousel-only", action="store_true", help="Only process carousels (multi-page posts).")
    
    args = ap.parse_args()

    ensure_dir(args.outdir)
    checkpoint_path = os.path.join(args.outdir, args.checkpoint)
    
    processed = set()
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, 'r') as f: processed = set(json.load(f))
        except: pass

    # Init OCR (GPU Enabled)
    logger.info("Initializing EasyOCR (GPU)...")
    try:
        reader = easyocr.Reader(["en"], gpu=True) 
    except Exception as e:
        logger.warning(f"GPU Init failed ({e}), falling back to CPU.")
        reader = easyocr.Reader(["en"], gpu=False)

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
        
        # PRE-FILTERING
        ctype = get_content_type(media_dict)
        
        if args.skip_reels and ctype == "reel":
            logger.info(f"Skipping {code} (Type: Reel/Clip)")
            processed.add(code) 
            continue
            
        if args.carousel_only and ctype != "carousel":
            logger.info(f"Skipping {code} (Type: {ctype} != Carousel)")
            processed.add(code)
            continue

        folder = os.path.join(args.outdir, f"post_{code}")
        logger.info(f"Processing {code} [Type: {ctype.upper()}]...")

        try:
            # 1. Download
            meta = process_raw_media_entry(media_dict, folder)
            
            # 2. Identify Files (Sorted by name, which now includes 00_ index)
            files = sorted([str(p) for p in Path(folder).rglob("*") if p.suffix.lower() in ['.jpg','.jpeg','.png','.mp4','.mov']])
            
            # 3. Run OCR
            ocr_results = {
                "shortcode": code,
                "caption": meta['caption'],
                "owner": meta['owner'],
                "type": meta['type'],
                "media": []
            }

            for fpath in files:
                entry = {"path": fpath, "type": "unknown", "clean_text": "", "ocr": []}
                
                if fpath.endswith(('.mp4', '.mov')):
                    entry["type"] = "video"
                    frames = extract_frames_from_video(fpath, folder)
                    frame_results = []
                    for frm in frames:
                        f_txt, f_raw = run_smart_easyocr(frm, reader)
                        frame_results.append({"frame": frm, "clean_text": f_txt})
                    entry["ocr_frames"] = frame_results
                    entry["clean_text"] = "\n".join([r["clean_text"] for r in frame_results if r["clean_text"]])
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
            # Only extracted text goes here now (no duplicate caption)
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