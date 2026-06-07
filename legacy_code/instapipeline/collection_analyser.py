#!/usr/bin/env python3
"""
integrated_collection_runner.py (v13: Batch Processing / Multi-Pass)

Architecture:
 1. Pre-sorts collection into 'Visual' (Carousel/Image) and 'Audio' (Reel/Video) batches.
 2. Pass 1: Loads Florence-2 (VLM) -> Processes ALL Visual posts -> Unloads VLM.
 3. Pass 2: Loads Whisper (Audio) -> Processes ALL Audio posts -> Unloads Whisper.
 4. Result: Only 1 model switch event total. Maximum speed, Minimum VRAM stress.

Usage:
  python integrated_collection_runner.py --username USER --password PASS --outdir ./mind_archive
"""

import os
import sys
import time
import json
import argparse
import logging
import requests
import warnings
import gc
import numpy as np
from pathlib import Path
from PIL import Image

# --- Dependency Checks ---
try:
    import transformers
    from packaging import version
    if version.parse(transformers.__version__) > version.parse("4.46.0"):
        print(f"!! WARNING !! Detected transformers version {transformers.__version__}.")
        print("Florence-2 may crash. If it does, run: pip install transformers==4.46.0")
except ImportError:
    pass

try:
    from instagrapi import Client
    from instagrapi.exceptions import TwoFactorRequired
except ImportError:
    sys.exit("Error: 'instagrapi' not installed.")

try:
    from transformers import AutoProcessor, AutoModelForCausalLM
    import torch
except ImportError:
    sys.exit("Error: 'transformers' or 'torch' not installed.")

try:
    import whisper
except ImportError:
    sys.exit("Error: 'openai-whisper' not installed.")

# Memory Optimization for Low VRAM
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ==========================================
# 1. Model Handler (Simplified for Batching)
# ==========================================
class ModelHandler:
    def __init__(self, device):
        self.device = device
        self.vlm_model = None
        self.vlm_processor = None
        self.audio_model = None

    def load_vlm(self):
        """Loads Florence-2. Unloads Whisper if present."""
        if self.vlm_model is not None: return

        if self.audio_model is not None:
            logger.info("Unloading Whisper to free VRAM...")
            del self.audio_model
            self.audio_model = None
            gc.collect()
            torch.cuda.empty_cache()

        logger.info("Loading Florence-2-large VLM...")
        try:
            model_id = "microsoft/Florence-2-large"
            self.vlm_processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
            self.vlm_model = AutoModelForCausalLM.from_pretrained(
                model_id, 
                trust_remote_code=True, 
                torch_dtype=torch.float16
            ).to(self.device)
        except Exception as e:
            logger.error(f"Failed to load VLM: {e}")
            sys.exit(1)

    def load_audio(self):
        """Loads Whisper. Unloads Florence-2 if present."""
        if self.audio_model is not None: return

        if self.vlm_model is not None:
            logger.info("Unloading Florence-2 to free VRAM...")
            del self.vlm_model
            del self.vlm_processor
            self.vlm_model = None
            self.vlm_processor = None
            gc.collect()
            torch.cuda.empty_cache()

        logger.info("Loading Whisper (Medium)...")
        try:
            self.audio_model = whisper.load_model("medium", device=self.device)
        except Exception as e:
            logger.error(f"Failed to load Whisper: {e}")
            sys.exit(1)


# ==========================================
# 2. Core Processing Functions
# ==========================================
def run_florence_ocr(image_path, handler):
    try:
        model = handler.vlm_model
        processor = handler.vlm_processor
        image = Image.open(image_path).convert("RGB")
        
        task_prompt = "<OCR>"
        inputs = processor(text=task_prompt, images=image, return_tensors="pt")
        inputs = {k: v.to(handler.device) for k, v in inputs.items()}
        
        if model.dtype == torch.float16:
            inputs["pixel_values"] = inputs["pixel_values"].to(torch.float16)

        generated_ids = model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=1024,
            num_beams=3,
            do_sample=False
        )
        text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(text, task=task_prompt, image_size=(image.width, image.height))
        return parsed.get("<OCR>", "")
    except Exception as e:
        logger.error(f"OCR Error on {image_path}: {e}")
        torch.cuda.empty_cache()
        return ""

def run_whisper_audio(audio_path, handler):
    try:
        res = handler.audio_model.transcribe(audio_path)
        return res["text"].strip()
    except Exception as e:
        logger.warning(f"Audio Error on {audio_path}: {e}")
        return ""


# ==========================================
# 3. Utilities & I/O
# ==========================================
class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (torch.Tensor, np.ndarray)): return obj.tolist()
        return super().default(obj)

def save_outputs(folder, results):
    with open(os.path.join(folder, "ocr_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, cls=NpEncoder)
    
    combined_lines = []
    # Audio Section
    if results.get("audio_transcript"):
        combined_lines.append("--- AUDIO TRANSCRIPT ---")
        combined_lines.append(results["audio_transcript"])
        combined_lines.append("")
    
    # Visual Section
    visual_lines = []
    sorted_media = sorted(results.get("media", []), key=lambda x: x.get("path", ""))
    for i, entry in enumerate(sorted_media):
        txt = entry.get("clean_text", "")
        if txt:
            if i > 0: visual_lines.append("\n\n") # Spacing between slides
            visual_lines.append(txt)
            
    if visual_lines:
        combined_lines.append("--- VISUAL TEXT (VLM) ---")
        combined_lines.append("".join(visual_lines))
    
    full_text = "\n".join(combined_lines)
    with open(os.path.join(folder, "combined_text.txt"), "w", encoding="utf-8") as f:
        f.write(full_text)
    return full_text

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

def fetch_raw_collection_items(cl, collection_pk):
    max_id = None
    while True:
        try:
            params = {"include_igtv_preview": "false"}
            if max_id: params["max_id"] = max_id
            res = cl.private_request(f"feed/collection/{collection_pk}/", params=params)
            for item in res.get("items", []): yield item.get("media", item)
            next_max_id = res.get("next_max_id")
            if not next_max_id: break
            max_id = next_max_id
            time.sleep(1) 
        except: break

def download_file_manual(url, target_path):
    try:
        with requests.get(url, stream=True) as r:
            if r.status_code == 403: return False
            r.raise_for_status()
            with open(target_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        return True
    except: return False

def ensure_dir(path): Path(path).mkdir(parents=True, exist_ok=True)

def get_content_type(media_dict):
    mt = media_dict.get("media_type") 
    pt = media_dict.get("product_type") 
    if mt == 8: return "carousel"
    elif pt == "clips": return "reel"
    elif mt == 2: return "video" 
    elif mt == 1: return "image"
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

    # Logic: Only download files relevant to the type
    if ctype == "image":
        url = get_url(media_dict.get("image_versions2", {}).get("candidates"))
        if url and download_file_manual(url, os.path.join(folder, f"00_{pk}.jpg")):
            downloaded_files.append(f"00_{pk}.jpg")
    
    elif ctype in ["video", "reel"]:
        vds = media_dict.get("video_versions", [])
        if vds and download_file_manual(vds[0].get("url"), os.path.join(folder, f"00_{pk}.mp4")):
            downloaded_files.append(f"00_{pk}.mp4")
            
    elif ctype == "carousel":
        for idx, child in enumerate(media_dict.get("carousel_media", [])):
            c_pk = child.get("pk", f"{pk}_{idx}")
            c_type = child.get("media_type")
            url = None; ext = "jpg"
            if c_type == 1: url = get_url(child.get("image_versions2", {}).get("candidates"))
            elif c_type == 2:
                # We skip video slides in carousels for simplicity/speed if needed, 
                # but let's download just in case.
                url = child.get("video_versions", [{}])[0].get("url"); ext = "mp4"
            
            if url and download_file_manual(url, os.path.join(folder, f"{idx:02d}_{c_pk}.{ext}")):
                downloaded_files.append(f"{idx:02d}_{c_pk}.{ext}")

    return {"shortcode": code, "caption": caption, "owner": user, "type": ctype}


# ==========================================
# 4. Main Driver (Batch Architecture)
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

    # --- 1. FETCH & SORT METADATA ---
    cache_dir = os.path.join(args.outdir, "collection_info")
    ensure_dir(cache_dir)
    cache_file = os.path.join(cache_dir, f"{args.username}_{args.collection}_cache.json")
    media_items = []

    if os.path.exists(cache_file):
        logger.info(f"Loaded cache from {cache_file}")
        with open(cache_file, "r", encoding="utf-8") as f: media_items = json.load(f)
    else:
        logger.info("Logging in to fetch collection...")
        cl = init_instagrapi_client(args.username, args.password, args.outdir)
        try: col_pk = cl.collection_pk_by_name(args.collection)
        except: logger.error("Collection not found"); return
        media_items = list(fetch_raw_collection_items(cl, col_pk))
        with open(cache_file, "w", encoding="utf-8") as f: json.dump(media_items, f, default=str)
        logger.info(f"Saved {len(media_items)} items to cache.")

    # Sort into batches
    vlm_batch = []   # Images, Carousels
    audio_batch = [] # Reels, Videos

    count = 0
    for item in media_items:
        code = item.get("code")
        if not code or code in processed: continue
        if args.max_posts > 0 and count >= args.max_posts: break
        
        ctype = get_content_type(item)
        if ctype in ["image", "carousel"]:
            vlm_batch.append(item)
        elif ctype in ["video", "reel"]:
            audio_batch.append(item)
        count += 1

    logger.info(f"Workload: {len(vlm_batch)} Visual posts, {len(audio_batch)} Audio posts.")
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    handler = ModelHandler(device)
    combined_fh = open(os.path.join(args.outdir, args.combined_file), "a", encoding="utf-8")

    # --- 2. PASS 1: VISUAL BATCH ---
    if vlm_batch:
        logger.info("=== STARTING VISUAL PASS (Florence-2) ===")
        handler.load_vlm() # Load Model ONCE
        
        for media_dict in vlm_batch:
            code = media_dict.get("code")
            logger.info(f"Processing {code} [Type: Visual]")
            folder = os.path.join(args.outdir, f"post_{code}")
            
            try:
                meta = process_raw_media_entry(media_dict, folder)
                files = sorted([str(p) for p in Path(folder).rglob("*.jpg")]) # Only JPGs for VLM
                
                ocr_results = {
                    "shortcode": code, "caption": meta['caption'], "type": meta['type'], "media": []
                }
                
                for fpath in files:
                    text = run_florence_ocr(fpath, handler)
                    ocr_results["media"].append({"path": fpath, "type": "image", "clean_text": text})

                final_text = save_outputs(folder, ocr_results)
                
                # Write to combined
                combined_fh.write(f"Post Link: https://www.instagram.com/p/{code}/\n")
                combined_fh.write(f"Type: {meta['type'].title()}\n")
                if meta['caption']: combined_fh.write(f"Caption: {meta['caption']}\n")
                combined_fh.write("-" * 20 + "\n")
                combined_fh.write(final_text + "\n")
                combined_fh.write("=" * 80 + "\n\n")
                combined_fh.flush()

                processed.add(code)
                with open(checkpoint_path, 'w') as f: json.dump(list(processed), f)
            except Exception as e:
                logger.error(f"Failed on {code}: {e}")

    # --- 3. PASS 2: AUDIO BATCH ---
    if audio_batch:
        logger.info("=== STARTING AUDIO PASS (Whisper) ===")
        handler.load_audio() # Switch Model ONCE
        
        for media_dict in audio_batch:
            code = media_dict.get("code")
            logger.info(f"Processing {code} [Type: Audio]")
            folder = os.path.join(args.outdir, f"post_{code}")
            
            try:
                meta = process_raw_media_entry(media_dict, folder)
                files = sorted([str(p) for p in Path(folder).rglob("*.mp4")]) # Only MP4s for Whisper
                
                ocr_results = {
                    "shortcode": code, "caption": meta['caption'], "type": meta['type'], 
                    "audio_transcript": "", "media": []
                }
                
                for fpath in files:
                    trans = run_whisper_audio(fpath, handler)
                    if trans: ocr_results["audio_transcript"] += trans + " "
                    # No visual entry for audio files

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
            except Exception as e:
                logger.error(f"Failed on {code}: {e}")

    combined_fh.close()
    logger.info("Done. All batches completed.")

if __name__ == "__main__":
    main()