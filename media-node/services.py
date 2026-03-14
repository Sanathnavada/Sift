import code
import os
import json
import logging
import torch
import time
import textwrap
from pathlib import Path
from llm_service import LLMCleaner
# Unified imports reflecting your tree structure
from utils import (
    ensure_dir, get_content_type, download_file, NpEncoder, 
    sanitize_filename, estimate_transcription_time, GPUPowerMonitor
)
from processor import ModelHandler, run_florence_ocr, run_whisper_audio
from yt.youtube import YoutubeFetcher

logger = logging.getLogger(__name__)

class ScraperService:
    """
    Business logic layer for processing posts, handling files, 
    and managing checkpoints.
    """
    
    @staticmethod
    def load_checkpoint(checkpoint_path):
        """Handle empty/corrupted checkpoint files gracefully."""
        if not os.path.exists(checkpoint_path):
            return set()
        
        try:
            if os.path.getsize(checkpoint_path) == 0:
                logger.info("Starting fresh (no previous checkpoint)")
                return set()
            
            with open(checkpoint_path, "r") as f:
                data = json.load(f)
            
            processed = set(data) if isinstance(data, list) else set()
            if processed:
                logger.info(f"Loaded checkpoint: {len(processed)} posts already processed")
            return processed
        except json.JSONDecodeError:
            logger.warning("Checkpoint file corrupted - resetting to empty")
            with open(checkpoint_path, "w") as f:
                json.dump([], f)
            return set()
        except Exception as e:
            logger.warning(f"Could not load checkpoint ({e}) - starting fresh")
            return set()

    @staticmethod
    def save_checkpoint(checkpoint_path, processed):
        try:
            with open(checkpoint_path, "w") as f:
                json.dump(list(processed), f)
        except Exception as e:
            logger.warning(f"Could not save checkpoint: {e}")

    @staticmethod
    def process_media_entry(media_dict, folder):
        """Download media files from dict."""
        ensure_dir(folder)
        pk = media_dict.get("pk") or media_dict.get("id") or media_dict.get("code")
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

    @staticmethod
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

    @classmethod
    def process_posts(cls, media_items, outdir, device=None, checkpoint_path=None, combined_file_path=None):
        """
        Main orchestration function for Instagram.
        """
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        processed = cls.load_checkpoint(checkpoint_path) if checkpoint_path else set()

        unprocessed = []
        seen_in_batch: set = set()
        skipped = 0
        for item in media_items:
            code = item.get("code")
            if code and code not in processed and code not in seen_in_batch:
                unprocessed.append(item)
                seen_in_batch.add(code)
            else:
                skipped += 1

        if skipped > 0:
            logger.info(f"Skipped {skipped} already-processed posts")

        if not unprocessed:
            logger.info("All posts already processed!")
            return

        logger.info(f"Processing {len(unprocessed)} new posts...")

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
        
        # Initialize LLM Cleaner
        llm_cleaner = LLMCleaner()

        if not combined_file_path:
            combined_file_path = os.path.join(outdir, "all_combined.txt")
            
        base, ext = os.path.splitext(combined_file_path)
        cleaned_file_path = f"{base}_cleaned{ext}"

        # Ensure the directory for the text file exists (since it might be outside outdir)
        ensure_dir(os.path.dirname(combined_file_path))

        combined_fh = open(combined_file_path, "a", encoding="utf-8")
        cleaned_fh = open(cleaned_file_path, "a", encoding="utf-8")

        def process_and_write(media_item, is_audio_pass):
            code = media_item.get("code")
            folder = os.path.join(outdir, f"post_{code}")
            
            try:
                meta = cls.process_media_entry(media_item, folder)
                results = {
                    "shortcode": code,
                    "caption": meta['caption'],
                    "type": meta['type'],
                    "media": []
                }
                
                monitor = GPUPowerMonitor()
                monitor.start()
                start_time = time.time()
                
                if is_audio_pass:
                    results["audio_transcript"] = ""
                    files = sorted([str(p) for p in Path(folder).rglob("*.mp4")])
                    for fpath in files:
                        trans = run_whisper_audio(fpath, handler)
                        if trans:
                            results["audio_transcript"] += trans + " "
                else:
                    files = sorted([str(p) for p in Path(folder).rglob("*.jpg")])
                    for fpath in files:
                        text = run_florence_ocr(fpath, handler)
                        results["media"].append({"path": fpath, "type": "image", "clean_text": text})

                actual_time_secs = time.time() - start_time
                metrics = monitor.stop(actual_time_secs)
                
                actual_mins = int(actual_time_secs // 60)
                actual_secs = int(actual_time_secs % 60)
                time_str = f"{actual_mins} min {actual_secs} sec" if actual_mins > 0 else f"{actual_secs} sec"
                
                logger.info(f"⏱️ Processed in {time_str}")
                
                power_str = ""
                if metrics:
                    power_str = (f"GPU Util: {metrics['avg_util_pct']:.1f}% | "
                                 f"VRAM Peak: {metrics['max_vram_gb']:.2f} GB | "
                                 f"Power: {metrics['avg_power_w']:.1f} W | "
                                 f"Energy: {metrics['energy_wh']:.4f} Wh")
                    logger.info(f"⚡ {power_str}")

                final_raw_text = cls.save_outputs(folder, results)

                # --- 1. Construct the Raw Block ---
                raw_block = f"Post: https://www.instagram.com/p/{code}/\n"
                raw_block += f"Type: {meta['type']}\n"
                if meta['caption']: 
                    raw_block += f"Caption: {meta['caption']}\n"
                
                raw_block += "-" * 40 + "\n"
                raw_block += final_raw_text + "\n"
                
                # --- 2. Write Raw to standard file ---
                combined_fh.write(raw_block)
                combined_fh.write("=" * 80 + "\n\n")
                combined_fh.flush()

                # --- 3. Clean and Write to Cleaned file ---
                logger.info(f"✨ Sending post {code} to LLM for cleaning...")
                cleaned_text = llm_cleaner.clean_text(raw_block)
                
                cleaned_fh.write(cleaned_text + "\n")
                cleaned_fh.write("=" * 80 + "\n\n")
                cleaned_fh.flush()

                processed.add(code)
                if checkpoint_path:
                    cls.save_checkpoint(checkpoint_path, processed)

                logger.info(f"✓ Processed and Cleaned {code}")
            except Exception as e:
                logger.error(f"Failed {code}: {e}")

        # PASS 1: Visual
        if vlm_batch:
            logger.info("=== VISUAL PASS ===")
            handler.load_vlm()
            for media in vlm_batch:
                process_and_write(media, is_audio_pass=False)

        # PASS 2: Audio
        if audio_batch:
            logger.info("=== AUDIO PASS ===")
            handler.load_audio()
            for media in audio_batch:
                process_and_write(media, is_audio_pass=True)

        combined_fh.close()
        cleaned_fh.close()
        logger.info("✓ Processing complete!")
        
class YoutubeService:
    """
    Business logic layer for processing YouTube videos with memory and disk cleanup.
    """
    @classmethod
    def process_video(cls, url, outdir, device=None, handler=None):
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        ensure_dir(outdir)
        audio_path = None
        
        try:
            fetcher = YoutubeFetcher(outdir)
            audio_path, title, quality_str, duration = fetcher.download_audio(url)

            # NEW: Only create a new handler if one wasn't passed in
            if handler is None:
                handler = ModelHandler(device)
                
            logger.info("=== YOUTUBE AUDIO PASS ===")
            
            if duration:
                vid_mins = duration // 60
                vid_secs = duration % 60
                logger.info(f"📊 Video Length: {vid_mins}m {vid_secs}s")
                logger.info(f"⏳ Est. GPU Time: {estimate_transcription_time(duration)} (Whisper Medium)")
            
            # This will now safely SKIP loading if the model is already in VRAM
            handler.load_audio()
            logger.info("Transcribing audio...")
            
            monitor = GPUPowerMonitor()
            monitor.start()
            start_time = time.time()
            
            transcript = run_whisper_audio(audio_path, handler)
            
            actual_time_secs = time.time() - start_time
            metrics = monitor.stop(actual_time_secs)
            
            actual_mins = int(actual_time_secs // 60)
            actual_secs = int(actual_time_secs % 60)
            actual_time_str = f"{actual_mins} min {actual_secs} sec" if actual_mins > 0 else f"{actual_secs} sec"
            
            logger.info(f"⏱️ Actual transcription time: {actual_time_str}")
            
            power_str = ""
            if metrics:
                power_str = (f"GPU Util: {metrics['avg_util_pct']:.1f}% | "
                             f"VRAM Peak: {metrics['max_vram_gb']:.2f} GB | "
                             f"Power: {metrics['avg_power_w']:.1f} W | "
                             f"Energy: {metrics['energy_wh']:.4f} Wh")
                logger.info(f"⚡ {power_str}")

            if transcript:
                safe_title = sanitize_filename(title)
                if not safe_title: safe_title = "youtube_transcription_unknown"
                transcript_file = os.path.join(outdir, f"{safe_title}.txt")
                
                wrapped_transcript = textwrap.fill(transcript, width=80)
                
                with open(transcript_file, "w", encoding="utf-8") as f:
                    f.write(f"Title: {title}\n")
                    f.write(f"URL: {url}\n")
                    f.write(f"Original Audio Quality: {quality_str}\n")
                    f.write("-" * 80 + "\n")
                    f.write(wrapped_transcript + "\n")
                    f.write("-" * 80 + "\n")
                    
                logger.info(f"✓ Transcript successfully saved to: {transcript_file}")
            else:
                logger.warning("No transcript was generated for this video.")

        except Exception as e:
            logger.error(f"Failed to process YouTube video: {e}")
            
        finally:
            if audio_path and os.path.exists(audio_path):
                try: os.remove(audio_path)
                except Exception: pass