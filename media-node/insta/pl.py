"""
Instagram Scraper - Production-Ready Refactored Version
- No cache files (fresh fetch every time)
- First N + Last N post selection
- Robust checkpoint filtering
- Bypass instagrapi GraphQL bugs
"""

import os
import sys
import time
import json
import argparse
import logging
import warnings
import gc
import requests
import numpy as np
from pathlib import Path
from PIL import Image

try:
    from transformers import AutoProcessor, AutoModelForCausalLM
    import torch
except ImportError:
    sys.exit("Error: transformers/torch not installed")

try:
    import whisper
except ImportError:
    sys.exit("Error: openai-whisper not installed")

try:
    from instagrapi import Client
    from instagrapi.exceptions import LoginRequired, MediaNotFound, TwoFactorRequired
except ImportError:
    sys.exit("Error: instagrapi not installed. Run: pip install instagrapi")

os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
warnings.filterwarnings("ignore")

# Suppress verbose instagrapi logs
logging.getLogger("instagrapi").setLevel(logging.ERROR)  # Only show errors
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class InstagramFetcher:
    """
    Production-grade Instagram fetcher.
    COMPLETE RAW API IMPLEMENTATION - Zero GraphQL code paths.
    """

    def __init__(self, outdir, username=None, password=None):
        self.cl = Client()
        self.outdir = outdir
        self.session_file = os.path.join(outdir, "instagram_session.json")
        self.authenticated = False

        self.cl.delay_range = [1, 3]

        if username and password:
            self._login(username, password)

    def _login(self, username, password):
        """Smart session management with automatic reuse."""
        ensure_dir(self.outdir)

        if os.path.exists(self.session_file):
            try:
                logger.info("Found existing session file")
                self.cl.load_settings(self.session_file)
                logger.info("✓ Loaded saved session")

                try:
                    self.cl.account_info()
                    logger.info("✓ Session verified - reusing existing login")
                    self.authenticated = True
                    return
                except:
                    logger.info("Session expired, logging in fresh...")
            except Exception as e:
                logger.warning(f"Could not load session: {e}")

        logger.info(f"Logging in as @{username}...")

        try:
            self.cl.login(username, password)
            self.cl.dump_settings(self.session_file)
            logger.info("✓ Logged in successfully (session saved)")
            self.authenticated = True

        except TwoFactorRequired:
            code = input("Enter 2FA code: ").strip()
            self.cl.login(username, password, verification_code=code)
            self.cl.dump_settings(self.session_file)
            logger.info("✓ Logged in with 2FA (session saved)")
            self.authenticated = True

        except Exception as e:
            logger.error(f"Login failed: {e}")
            raise

    def extract_shortcode(self, url):
        """Extract shortcode from Instagram URL"""
        if not url:
            raise ValueError("URL cannot be empty")

        if not url.startswith('http'):
            return url.strip()

        import re
        patterns = [
            r'/p/([A-Za-z0-9_-]+)',
            r'/reel/([A-Za-z0-9_-]+)',
            r'/tv/([A-Za-z0-9_-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        raise ValueError(f"Invalid Instagram URL: {url}")

    def fetch_single_post(self, url_or_shortcode):
        """Fetch single post - mobile API only."""
        shortcode = self.extract_shortcode(url_or_shortcode)
        logger.info(f"Fetching post: {shortcode}")

        try:
            media_pk = self.cl.media_pk_from_code(shortcode)
            media = self.cl.media_info(media_pk)

            logger.info(f"✓ Fetched post by @{media.user.username}")

            return [self._convert_media(media)]

        except LoginRequired:
            logger.error("This post requires authentication")
            logger.error("Add: --username YOUR_USERNAME --password YOUR_PASSWORD")
            raise

        except MediaNotFound:
            logger.error(f"Post {shortcode} not found or deleted")
            raise

        except Exception as e:
            logger.error(f"Failed to fetch post: {e}")
            raise

    def _get_user_id_direct(self, username):
        """
        CRITICAL: Get user ID without triggering ANY instagrapi user methods.
        Direct raw mobile API call only.
        """
        try:
            # Raw API request - bypasses all instagrapi user code
            res = self.cl.private_request(
                f"users/{username}/usernameinfo/",
                params={}
            )

            user_data = res.get("user", {})
            user_id = user_data.get("pk")

            if not user_id:
                raise Exception(f"User @{username} not found")

            return str(user_id), user_data

        except Exception as e:
            raise Exception(f"Failed to get user ID for @{username}: {e}")

    def fetch_user_posts(self, username, first_n=0, last_n=0):
        """
        COMPLETE REWRITE: Zero instagrapi user methods.
        100% raw mobile API calls - NO GraphQL paths.

        Args:
            username: Instagram username
            first_n: Number of most recent posts (0 = all)
            last_n: Number of oldest posts (0 = none)

        Returns:
            List of media dicts (first N + last N combined)
        """
        logger.info(f"Fetching posts from @{username}...")

        try:
            # Get user ID via raw API (bypasses GraphQL entirely)
            user_id, user_data = self._get_user_id_direct(username)

            is_private = user_data.get("is_private", False)
            if is_private:
                logger.error(f"@{username} is private")
                raise Exception("Private account")

            total_posts = user_data.get("media_count", 0)
            logger.info(f"@{username} has {total_posts} posts")

            # Determine fetch count
            if first_n == 0 and last_n == 0:
                fetch_count = total_posts
                logger.info("Fetching ALL posts...")
            elif last_n == 0:
                fetch_count = min(first_n, total_posts)
                logger.info(f"Fetching first {fetch_count} posts...")
            else:
                fetch_count = min(first_n + last_n, total_posts)
                logger.info(f"Fetching first {first_n} + last {last_n} posts...")

            # Fetch via raw mobile API only
            media_items = []
            max_id = ""

            while len(media_items) < fetch_count:
                count = min(fetch_count - len(media_items), 50)

                res = self.cl.private_request(
                    f"feed/user/{user_id}/",
                    params={
                        "max_id": max_id,
                        "count": count,
                        "rank_token": self.cl.rank_token,
                        "ranked_content": "true",
                    },
                )

                items = res.get("items", [])
                if not items:
                    break

                media_items.extend(items)

                if len(media_items) >= fetch_count:
                    break

                next_max_id = res.get("next_max_id")
                if not next_max_id:
                    break

                max_id = next_max_id
                time.sleep(1)

            logger.info(f"✓ Fetched {len(media_items)} posts (raw dicts)")

            # Slice first N + last N
            if last_n > 0 and len(media_items) > (first_n + last_n):
                first_slice = media_items[:first_n] if first_n > 0 else []
                last_slice = media_items[-last_n:] if last_n > 0 else []

                seen_codes = set()
                final_items = []

                for item in first_slice + last_slice:
                    code = item.get("code")
                    if code and code not in seen_codes:
                        seen_codes.add(code)
                        final_items.append(item)

                logger.info(f"Selected {len(final_items)} posts (first {first_n} + last {last_n})")
                return final_items

            return media_items[:fetch_count]

        except Exception as e:
            logger.error(f"Failed to fetch user posts: {e}")
            raise

    def fetch_collection(self, collection_name, first_n=0, last_n=0):
        """Fetch posts from saved collection."""
        if not self.authenticated:
            raise Exception("Collections require authentication")

        logger.info(f"Fetching collection: '{collection_name}'")

        try:
            col_pk = self.cl.collection_pk_by_name(collection_name)
            logger.info(f"Collection ID: {col_pk}")
        except Exception as e:
            logger.error(f"Collection '{collection_name}' not found")
            raise

        media_items = []
        max_id = None

        while True:
            try:
                params = {"include_igtv_preview": "false"}
                if max_id:
                    params["max_id"] = max_id

                res = self.cl.private_request(f"feed/collection/{col_pk}/", params=params)

                for item in res.get("items", []):
                    media_dict = item.get("media")
                    if media_dict:
                        media_items.append(media_dict)

                next_max_id = res.get("next_max_id")
                if not next_max_id:
                    break

                max_id = next_max_id
                time.sleep(1)

            except Exception as e:
                logger.warning(f"Error fetching collection page: {e}")
                break

        logger.info(f"✓ Fetched {len(media_items)} posts from collection")

        # Apply first N + last N
        if last_n > 0 and len(media_items) > (first_n + last_n):
            first_slice = media_items[:first_n] if first_n > 0 else []
            last_slice = media_items[-last_n:] if last_n > 0 else []

            seen_codes = set()
            final_items = []

            for item in first_slice + last_slice:
                code = item.get("code")
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    final_items.append(item)

            logger.info(f"Selected {len(final_items)} posts (first {first_n} + last {last_n})")
            return final_items

        if first_n > 0:
            return media_items[:first_n]

        return media_items

    def _convert_media(self, media):
        """Convert instagrapi Media object to dict."""
        if media.media_type == 8:
            media_type = 8
        elif media.media_type == 2:
            media_type = 2
        else:
            media_type = 1

        product_type = media.product_type if hasattr(media, 'product_type') else 'feed'

        media_dict = {
            'pk': str(media.pk),
            'id': str(media.id),
            'code': media.code,
            'media_type': media_type,
            'product_type': product_type,
            'caption': {'text': media.caption_text} if media.caption_text else None,
            'user': {'username': media.user.username}
        }

        if media.thumbnail_url:
            media_dict['image_versions2'] = {
                'candidates': [{'url': str(media.thumbnail_url)}]
            }

        if media.video_url:
            media_dict['video_versions'] = [{'url': str(media.video_url)}]

        if media_type == 8 and hasattr(media, 'resources'):
            carousel_media = []
            for resource in media.resources:
                child_dict = {
                    'pk': str(resource.pk),
                    'media_type': 2 if resource.media_type == 2 else 1
                }

                if resource.thumbnail_url:
                    child_dict['image_versions2'] = {
                        'candidates': [{'url': str(resource.thumbnail_url)}]
                    }

                if resource.video_url:
                    child_dict['video_versions'] = [{'url': str(resource.video_url)}]

                carousel_media.append(child_dict)

            media_dict['carousel_media'] = carousel_media

        return media_dict

class ModelHandler:
    """
    Model manager with smart memory optimization.
    """

    def __init__(self, device):
        self.device = device
        self.vlm_model = None
        self.vlm_processor = None
        self.audio_model = None

    def load_vlm(self):
        """Load Florence-2, unload Whisper if present"""
        if self.vlm_model is not None: 
            return

        if self.audio_model is not None:
            logger.info("Unloading Whisper to free VRAM...")
            del self.audio_model
            self.audio_model = None
            gc.collect()
            torch.cuda.empty_cache()

        logger.info("Loading Florence-2...")
        model_id = "microsoft/Florence-2-large"
        self.vlm_processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
        self.vlm_model = AutoModelForCausalLM.from_pretrained(
            model_id, trust_remote_code=True, torch_dtype=torch.float16
        ).to(self.device)

    def load_audio(self):
        """Load Whisper, unload Florence-2 if present"""
        if self.audio_model is not None: 
            return

        if self.vlm_model is not None:
            logger.info("Unloading Florence-2 to free VRAM...")
            del self.vlm_model
            del self.vlm_processor
            self.vlm_model = None
            self.vlm_processor = None
            gc.collect()
            torch.cuda.empty_cache()

        logger.info("Loading Whisper...")
        self.audio_model = whisper.load_model("medium", device=self.device)


def run_florence_ocr(image_path, handler):
    """Run Florence-2 OCR"""
    try:
        image = Image.open(image_path).convert("RGB")
        task = "<OCR>"
        inputs = handler.vlm_processor(text=task, images=image, return_tensors="pt")
        inputs = {k: v.to(handler.device) for k, v in inputs.items()}

        if handler.vlm_model.dtype == torch.float16:
            inputs["pixel_values"] = inputs["pixel_values"].to(torch.float16)

        generated = handler.vlm_model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=1024,
            num_beams=3
        )

        text = handler.vlm_processor.batch_decode(generated, skip_special_tokens=False)[0]
        parsed = handler.vlm_processor.post_process_generation(
            text, task=task, image_size=(image.width, image.height)
        )
        return parsed.get("<OCR>", "")
    except Exception as e:
        logger.error(f"OCR error on {image_path}: {e}")
        return ""


def run_whisper_audio(audio_path, handler):
    """Run Whisper transcription"""
    try:
        result = handler.audio_model.transcribe(audio_path)
        return result["text"].strip()
    except Exception as e:
        logger.warning(f"Audio error on {audio_path}: {e}")
        return ""


def ensure_dir(path):
    """Create directory if needed"""
    Path(path).mkdir(parents=True, exist_ok=True)


def get_content_type(media_dict):
    """Determine content type"""
    mt = media_dict.get("media_type")
    pt = media_dict.get("product_type")

    if mt == 8:
        return "carousel"
    elif pt == "clips":
        return "reel"
    elif mt == 2:
        return "video"
    elif mt == 1:
        return "image"
    return "unknown"


def download_file(url, target):
    """Download file from URL"""
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(target, 'wb') as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
        return True
    except Exception as e:
        logger.warning(f"Download failed: {e}")
        return False


def process_media_entry(media_dict, folder):
    """
    Download media files from dict.
    """
    ensure_dir(folder)
    pk = media_dict.get("pk") or media_dict.get("id")
    code = media_dict.get("code")
    caption = media_dict.get("caption", {}).get("text", "") if media_dict.get("caption") else ""
    user = media_dict.get("user", {}).get("username", "unknown")
    ctype = get_content_type(media_dict)

    def get_url(candidates):
        return candidates[0].get("url") if candidates else None

    if ctype == "image":
        url = get_url(media_dict.get("image_versions2", {}).get("candidates", []))
        if url:
            download_file(url, os.path.join(folder, f"00_{pk}.jpg"))

    elif ctype in ["video", "reel"]:
        vds = media_dict.get("video_versions", [])
        if vds:
            download_file(vds[0].get("url"), os.path.join(folder, f"00_{pk}.mp4"))

    elif ctype == "carousel":
        for idx, child in enumerate(media_dict.get("carousel_media", [])):
            c_pk = child.get("pk", f"{pk}_{idx}")
            c_type = child.get("media_type")

            if c_type == 1:
                url = get_url(child.get("image_versions2", {}).get("candidates", []))
                ext = "jpg"
            elif c_type == 2:
                url = child.get("video_versions", [{}])[0].get("url")
                ext = "mp4"
            else:
                continue

            if url:
                download_file(url, os.path.join(folder, f"{idx:02d}_{c_pk}.{ext}"))

    return {"shortcode": code, "caption": caption, "owner": user, "type": ctype}


class NpEncoder(json.JSONEncoder):
    """JSON encoder for numpy/torch"""
    def default(self, obj):
        if isinstance(obj, (torch.Tensor, np.ndarray)):
            return obj.tolist()
        return super().default(obj)


def save_outputs(folder, results):
    """Save results to JSON and text"""
    with open(os.path.join(folder, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, cls=NpEncoder)

    lines = []

    if results.get("audio_transcript"):
        lines.append("--- AUDIO ---")
        lines.append(results["audio_transcript"])
        lines.append("")

    visual = []
    for entry in sorted(results.get("media", []), key=lambda x: x.get("path", "")):
        txt = entry.get("clean_text", "")
        if txt:
            visual.append(txt)

    if visual:
        lines.append("--- VISUAL ---")
        lines.append("\n\n".join(visual))

    full = "\n".join(lines)
    with open(os.path.join(folder, "combined.txt"), "w", encoding="utf-8") as f:
        f.write(full)

    return full


def load_checkpoint(checkpoint_path):
    """Handle empty/corrupted checkpoint files gracefully."""
    if not os.path.exists(checkpoint_path):
        return set()
    
    try:
        # ✅ Check if file is empty BEFORE parsing
        if os.path.getsize(checkpoint_path) == 0:
            logger.info("Starting fresh (no previous checkpoint)")
            return set()
        
        with open(checkpoint_path, "r") as f:
            data = json.load(f)
        
        processed = set(data) if isinstance(data, list) else set()
        
        # ✅ Only log if checkpoint has data
        if processed:
            logger.info(f"Loaded checkpoint: {len(processed)} posts already processed")
        
        return processed
        
    # ✅ Specific handling for JSON errors
    except json.JSONDecodeError:
        logger.warning("Checkpoint file corrupted - resetting to empty")
        # Auto-fix corrupted file
        with open(checkpoint_path, "w") as f:
            json.dump([], f)
        return set()
    
    except Exception as e:
        logger.warning(f"Could not load checkpoint ({e}) - starting fresh")
        return set()



def save_checkpoint(checkpoint_path, processed):
    """Save checkpoint"""
    try:
        with open(checkpoint_path, "w") as f:
            json.dump(list(processed), f)
    except Exception as e:
        logger.warning(f"Could not save checkpoint: {e}")


def filter_unprocessed(media_items, processed_codes):
    """
    REFACTORED: Filter out already-processed posts.
    Returns only unprocessed items.
    """
    unprocessed = []
    skipped = 0

    for item in media_items:
        code = item.get("code")
        if code and code not in processed_codes:
            unprocessed.append(item)
        else:
            skipped += 1

    if skipped > 0:
        logger.info(f"Skipped {skipped} already-processed posts")

    return unprocessed


def process_posts(media_items, outdir, device, checkpoint_path=None):
    """
    REFACTORED: Process posts with upfront checkpoint filtering.

    Flow:
    1. Load checkpoint
    2. Filter out processed posts
    3. Batch unprocessed posts
    4. Process batches
    5. Save checkpoint after each post
    """
    # Load checkpoint
    processed = load_checkpoint(checkpoint_path) if checkpoint_path else set()

    # Filter unprocessed posts BEFORE batching
    unprocessed = filter_unprocessed(media_items, processed)

    if not unprocessed:
        logger.info("All posts already processed!")
        return

    logger.info(f"Processing {len(unprocessed)} new posts...")

    # Sort into batches
    vlm_batch = []
    audio_batch = []

    for item in unprocessed:
        ctype = get_content_type(item)
        if ctype in ["image", "carousel"]:
            vlm_batch.append(item)
        elif ctype in ["video", "reel"]:
            audio_batch.append(item)

    logger.info(f"Batches: {len(vlm_batch)} visual, {len(audio_batch)} audio")

    handler = ModelHandler(device)
    combined_fh = open(os.path.join(outdir, "all_combined.txt"), "a", encoding="utf-8")

    # PASS 1: Visual
    if vlm_batch:
        logger.info("=== VISUAL PASS ===")
        handler.load_vlm()

        for media in vlm_batch:
            code = media.get("code")
            folder = os.path.join(outdir, f"post_{code}")

            try:
                meta = process_media_entry(media, folder)
                files = sorted([str(p) for p in Path(folder).rglob("*.jpg")])

                results = {
                    "shortcode": code,
                    "caption": meta['caption'],
                    "type": meta['type'],
                    "media": []
                }

                for fpath in files:
                    text = run_florence_ocr(fpath, handler)
                    results["media"].append({"path": fpath, "type": "image", "clean_text": text})

                final = save_outputs(folder, results)

                combined_fh.write(f"Post: https://www.instagram.com/p/{code}/\n")
                combined_fh.write(f"Type: {meta['type']}\n")
                if meta['caption']:
                    combined_fh.write(f"Caption: {meta['caption']}\n")
                combined_fh.write("-" * 40 + "\n")
                combined_fh.write(final + "\n")
                combined_fh.write("=" * 80 + "\n\n")
                combined_fh.flush()

                processed.add(code)
                if checkpoint_path:
                    save_checkpoint(checkpoint_path, processed)

                logger.info(f"✓ Processed {code}")
            except Exception as e:
                logger.error(f"Failed {code}: {e}")

    # PASS 2: Audio
    if audio_batch:
        logger.info("=== AUDIO PASS ===")
        handler.load_audio()

        for media in audio_batch:
            code = media.get("code")
            folder = os.path.join(outdir, f"post_{code}")

            try:
                meta = process_media_entry(media, folder)
                files = sorted([str(p) for p in Path(folder).rglob("*.mp4")])

                results = {
                    "shortcode": code,
                    "caption": meta['caption'],
                    "type": meta['type'],
                    "audio_transcript": "",
                    "media": []
                }

                for fpath in files:
                    trans = run_whisper_audio(fpath, handler)
                    if trans:
                        results["audio_transcript"] += trans + " "

                final = save_outputs(folder, results)

                combined_fh.write(f"Post: https://www.instagram.com/p/{code}/\n")
                combined_fh.write(f"Type: {meta['type']}\n")
                if meta['caption']:
                    combined_fh.write(f"Caption: {meta['caption']}\n")
                combined_fh.write("-" * 40 + "\n")
                combined_fh.write(final + "\n")
                combined_fh.write("=" * 80 + "\n\n")
                combined_fh.flush()

                processed.add(code)
                if checkpoint_path:
                    save_checkpoint(checkpoint_path, processed)

                logger.info(f"✓ Processed {code}")
            except Exception as e:
                logger.error(f"Failed {code}: {e}")

    combined_fh.close()
    logger.info("✓ Processing complete!")


def main():
    parser = argparse.ArgumentParser(
        description='Instagram Scraper - Production Refactored',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Single post
  python %(prog)s --post https://www.instagram.com/p/ABC123/ \
      --username USER --password PASS

  # First 10 + Last 10 posts from user
  python %(prog)s --public-user nasa \
      --first-n 10 --last-n 10

  # Collection: First 20 + Last 5
  python %(prog)s --private-user "Mind" \
      --username USER --password PASS \
      --first-n 20 --last-n 5
        '''
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--public-user", metavar="USERNAME")
    mode_group.add_argument("--post", metavar="URL")
    mode_group.add_argument("--private-user", metavar="COLLECTION")

    parser.add_argument("--username", help="Your Instagram username")
    parser.add_argument("--password", help="Your Instagram password")

    # REFACTORED: first-n and last-n instead of max-posts
    parser.add_argument("--first-n", type=int, default=0,
                       help="Number of most recent posts (0 = all)")
    parser.add_argument("--last-n", type=int, default=0,
                       help="Number of oldest posts (0 = none)")

    parser.add_argument("--outdir", default="./archive")
    parser.add_argument("--checkpoint", default="checkpoint.json")

    args = parser.parse_args()

    # Validate
    if args.private_user and (not args.username or not args.password):
        parser.error("--private-user requires --username and --password")

    # Backward compatibility: treat --first-n as max-posts if --last-n not specified
    if args.first_n == 0 and args.last_n == 0:
        args.first_n = 50  # Default to first 50 if nothing specified

    ensure_dir(args.outdir)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Initialize fetcher
    fetcher = InstagramFetcher(
        outdir=args.outdir,
        username=args.username,
        password=args.password
    )

    checkpoint_path = os.path.join(args.outdir, args.checkpoint)

    # MODE 1: Public User (NO CACHE)
    if args.public_user:
        logger.info(f"=== MODE: Public User (@{args.public_user}) ===")

        # REFACTORED: No cache - fetch fresh
        media_items = fetcher.fetch_user_posts(
            args.public_user,
            first_n=args.first_n,
            last_n=args.last_n
        )

        # REFACTORED: Process with checkpoint filtering
        process_posts(media_items, args.outdir, device, checkpoint_path)

    # MODE 2: Single Post
    elif args.post:
        logger.info("=== MODE: Single Post ===")

        try:
            media_items = fetcher.fetch_single_post(args.post)
            process_posts(media_items, args.outdir, device)

        except Exception as e:
            logger.error(f"Failed: {e}")
            sys.exit(1)

    # MODE 3: Collection (NO CACHE)
    elif args.private_user:
        logger.info(f"=== MODE: Collection ('{args.private_user}') ===")

        # REFACTORED: No cache - fetch fresh
        media_items = fetcher.fetch_collection(
            args.private_user,
            first_n=args.first_n,
            last_n=args.last_n
        )

        # REFACTORED: Process with checkpoint filtering
        process_posts(media_items, args.outdir, device, checkpoint_path)


if __name__ == "__main__":
    main()