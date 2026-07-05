import gc
import json
import logging
import os
import subprocess
import textwrap
import time
from pathlib import Path

import torch

from .llm_service import LLMCleaner
from .ocr_cleanup import clean_ocr_text
from .processor import ModelHandler, inference_lock, run_florence_ocr, run_whisper_audio
from .utils import (
    GPUPowerMonitor,
    NpEncoder,
    download_file,
    ensure_dir,
    estimate_transcription_time,
    get_content_type,
    sanitize_filename,
)
from .youtube.youtube import YoutubeFetcher

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
_MEDIA_SUFFIXES = _IMAGE_SUFFIXES | _VIDEO_SUFFIXES


def _resolve_device(device: str | None) -> str:
    return device or ("cuda" if torch.cuda.is_available() else "cpu")


def _cleanup_runtime() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


class ScraperService:
    """Business logic for Instagram media download, OCR, transcription, and output files."""

    @staticmethod
    def load_checkpoint(checkpoint_path):
        """Handle empty/corrupted checkpoint files gracefully."""
        if not os.path.exists(checkpoint_path):
            return set()

        try:
            if os.path.getsize(checkpoint_path) == 0:
                logger.info("Starting fresh (no previous checkpoint)")
                return set()

            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            processed = set(data) if isinstance(data, list) else set()
            if processed:
                logger.info("Loaded checkpoint: %s posts already processed", len(processed))
            return processed
        except json.JSONDecodeError:
            logger.warning("Checkpoint file corrupted - resetting to empty")
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump([], f)
            return set()
        except Exception as exc:
            logger.warning("Could not load checkpoint (%s) - starting fresh", exc)
            return set()

    @staticmethod
    def save_checkpoint(checkpoint_path, processed):
        try:
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(list(processed), f)
        except Exception as exc:
            logger.warning("Could not save checkpoint: %s", exc)

    @staticmethod
    def process_media_entry(media_dict, folder):
        """Download media files from an Instagram-style media dict."""
        ensure_dir(folder)
        pk = media_dict.get("pk") or media_dict.get("id") or media_dict.get("code")
        code = media_dict.get("code")
        caption = media_dict.get("caption", {}).get("text", "") if media_dict.get("caption") else ""
        user = media_dict.get("user", {}).get("username", "unknown")
        ctype = get_content_type(media_dict)
        downloaded = 0

        def get_url(candidates):
            return candidates[0].get("url") if candidates else None

        def download_ytdlp_item(item, target_code):
            from .instagram.ytdlp_fetcher import YtdlpInstaFetcher

            source_url = item.get("_source_url") or item.get("video_versions", [{}])[0].get("url")
            if not source_url:
                return False
            return YtdlpInstaFetcher.download_with_ytdlp(
                source_url,
                folder,
                target_code,
                cookiefile=item.get("_ytdlp_cookiefile"),
            )

        if media_dict.get("_download_with_ytdlp"):
            if download_ytdlp_item(media_dict, code or str(pk)):
                downloaded += 1

        elif ctype == "image":
            url = get_url(media_dict.get("image_versions2", {}).get("candidates", []))
            if url and download_file(url, os.path.join(folder, f"00_{pk}.jpg")):
                downloaded += 1

        elif ctype in ["video", "reel"]:
            video_versions = media_dict.get("video_versions", [])
            url = video_versions[0].get("url") if video_versions else None
            if url and download_file(url, os.path.join(folder, f"00_{pk}.mp4")):
                downloaded += 1

        elif ctype == "carousel":
            for idx, child in enumerate(media_dict.get("carousel_media", [])):
                child_pk = child.get("pk") or child.get("id") or child.get("code") or f"{pk}_{idx}"
                child_type = child.get("media_type")

                if child.get("_download_with_ytdlp"):
                    if download_ytdlp_item(child, f"{idx:02d}_{child_pk}"):
                        downloaded += 1
                    continue

                if child_type == 1:
                    url = get_url(child.get("image_versions2", {}).get("candidates", []))
                    ext = "jpg"
                elif child_type == 2:
                    url = child.get("video_versions", [{}])[0].get("url")
                    ext = "mp4"
                else:
                    continue

                if url and download_file(url, os.path.join(folder, f"{idx:02d}_{child_pk}.{ext}")):
                    downloaded += 1

        media_files = [
            path for path in Path(folder).iterdir()
            if path.is_file() and path.suffix.lower() in _MEDIA_SUFFIXES
        ]
        if not media_files:
            raise RuntimeError(f"No downloadable media files were produced for Instagram post {code or pk}.")

        return {"shortcode": code, "caption": caption, "owner": user, "type": ctype, "downloaded": downloaded}

    @staticmethod
    def save_outputs(folder, results):
        """Save per-post JSON and combined text output."""
        with open(os.path.join(folder, "results.json"), "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, cls=NpEncoder)

        lines = []
        ordered_content = results.get("ordered_content") or []
        if ordered_content:
            for entry in ordered_content:
                label = f"--- MEDIA {entry.get('index', 0) + 1}: {entry.get('type', 'unknown').upper()} ---"
                lines.append(label)
                text = entry.get("text", "")
                lines.append(text if text else "[No text extracted]")
                lines.append("")
            full = "\n".join(lines).strip()
            with open(os.path.join(folder, "combined.txt"), "w", encoding="utf-8") as f:
                f.write(full)
            return full

        if results.get("audio_transcript"):
            lines.append("--- AUDIO ---")
            lines.append(results["audio_transcript"])
            lines.append("")

        visual = []
        for entry in sorted(results.get("media", []), key=lambda x: x.get("path", "")):
            text = entry.get("clean_text", "")
            if text:
                visual.append(text)

        if visual:
            lines.append("--- VISUAL ---")
            lines.append("\n\n".join(visual))

        full = "\n".join(lines)
        with open(os.path.join(folder, "combined.txt"), "w", encoding="utf-8") as f:
            f.write(full)
        return full

    @staticmethod
    def media_duration_seconds(path: str) -> int:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return max(int(float(result.stdout.strip() or 0)), 0)
        except Exception:
            return 0

    @classmethod
    def _prepare_post_records(cls, unprocessed, outdir, notify):
        post_records = []
        image_work = []
        video_work = []

        for ordinal, media_item in enumerate(unprocessed, start=1):
            code = media_item.get("code")
            folder = os.path.join(outdir, f"post_{code}")
            try:
                notify(f"Downloading Instagram media {ordinal}/{len(unprocessed)}: {code}")
                meta = cls.process_media_entry(media_item, folder)
                results = {
                    "shortcode": code,
                    "caption": meta["caption"],
                    "type": meta["type"],
                    "media": [],
                    "ordered_content": [],
                }
                media_files = sorted(
                    [path for path in Path(folder).iterdir() if path.suffix.lower() in _MEDIA_SUFFIXES],
                    key=lambda path: path.name,
                )
                if not media_files:
                    notify(f"No downloaded media files found for {code}; skipping this post.", warning=True)
                    continue
                for index, fpath in enumerate(media_files):
                    entry_type = "image" if fpath.suffix.lower() in _IMAGE_SUFFIXES else "video"
                    entry = {"index": index, "path": str(fpath), "type": entry_type, "text": ""}
                    results["ordered_content"].append(entry)
                    if entry_type == "image":
                        image_work.append(entry)
                    else:
                        video_work.append(entry)
                post_records.append({"code": code, "folder": folder, "meta": meta, "results": results})
            except Exception as exc:
                notify(f"Failed to download/prepare {code}: {exc}", warning=True)

        return post_records, image_work, video_work

    @staticmethod
    def _run_ocr_batch(handler, image_work, notify):
        if not image_work:
            return

        notify(f"Running OCR for {len(image_work)} Instagram image(s)")
        monitor = GPUPowerMonitor()
        monitor.start()
        start_time = time.time()
        with inference_lock():
            try:
                notify("Loading Florence OCR model")
                handler.load_vlm()
                for index, entry in enumerate(image_work, start=1):
                    notify(f"OCR image {index}/{len(image_work)}")
                    raw_text = run_florence_ocr(entry["path"], handler)
                    entry["raw_text"] = raw_text
                    entry["text"] = clean_ocr_text(raw_text)
            finally:
                notify("Unloading Florence OCR model")
                handler.unload_vlm()

        elapsed = time.time() - start_time
        logger.info("OCR batch processed in %s min %s sec", int(elapsed // 60), int(elapsed % 60))
        metrics = monitor.stop(elapsed)
        if metrics:
            logger.info(
                "OCR GPU metrics: util %.1f%% | VRAM %.2f GB | power %.1f W | energy %.4f Wh",
                metrics["avg_util_pct"],
                metrics["max_vram_gb"],
                metrics["avg_power_w"],
                metrics["energy_wh"],
            )

    @classmethod
    def _run_transcription_batch(cls, handler, video_work, notify):
        if not video_work:
            return

        total_duration = sum(cls.media_duration_seconds(entry["path"]) for entry in video_work)
        if total_duration:
            notify(f"Estimated transcription time: {estimate_transcription_time(total_duration)}")
        notify(f"Transcribing {len(video_work)} Instagram video(s)")

        monitor = GPUPowerMonitor()
        monitor.start()
        start_time = time.time()
        with inference_lock():
            try:
                notify("Loading Whisper transcription model")
                handler.load_audio()
                for index, entry in enumerate(video_work, start=1):
                    notify(f"Transcribing video {index}/{len(video_work)}")
                    entry["text"] = run_whisper_audio(entry["path"], handler)
            finally:
                notify("Unloading Whisper transcription model")
                handler.unload_audio()

        elapsed = time.time() - start_time
        logger.info("Whisper batch processed in %s min %s sec", int(elapsed // 60), int(elapsed % 60))
        metrics = monitor.stop(elapsed)
        if metrics:
            logger.info(
                "Whisper GPU metrics: util %.1f%% | VRAM %.2f GB | power %.1f W | energy %.4f Wh",
                metrics["avg_util_pct"],
                metrics["max_vram_gb"],
                metrics["avg_power_w"],
                metrics["energy_wh"],
            )

    @classmethod
    def _write_post_outputs(
        cls,
        post_records,
        processed,
        checkpoint_path,
        combined_file_path,
        clean_output,
    ):
        base, ext = os.path.splitext(combined_file_path)
        cleaned_file_path = f"{base}_cleaned{ext}" if clean_output else None
        llm_cleaner = LLMCleaner() if clean_output else None

        combined_fh = open(combined_file_path, "a", encoding="utf-8")
        cleaned_fh = open(cleaned_file_path, "a", encoding="utf-8") if clean_output else None
        try:
            for record in post_records:
                code = record["code"]
                meta = record["meta"]
                results = record["results"]
                results["media"] = [
                    {
                        "path": entry["path"],
                        "type": "image",
                        "raw_text": entry.get("raw_text", entry.get("text", "")),
                        "clean_text": entry["text"],
                    }
                    for entry in results["ordered_content"]
                    if entry["type"] == "image"
                ]
                audio_text = " ".join(
                    entry["text"]
                    for entry in results["ordered_content"]
                    if entry["type"] == "video" and entry["text"]
                ).strip()
                if audio_text:
                    results["audio_transcript"] = audio_text

                final_raw_text = cls.save_outputs(record["folder"], results)
                post_path = "reel" if meta["type"] == "reel" else "p"
                raw_block = f"Post: https://www.instagram.com/{post_path}/{code}/\n"
                raw_block += f"Type: {meta['type']}\n"
                if meta["caption"]:
                    raw_block += f"Caption: {meta['caption']}\n"
                raw_block += "-" * 40 + "\n"
                raw_block += final_raw_text + "\n"

                combined_fh.write(raw_block)
                combined_fh.write("=" * 80 + "\n\n")
                combined_fh.flush()

                if clean_output:
                    logger.info("Sending post %s to LLM for cleaning...", code)
                    cleaned_text = llm_cleaner.clean_text(raw_block)
                    cleaned_fh.write(cleaned_text + "\n")
                    cleaned_fh.write("=" * 80 + "\n\n")
                    cleaned_fh.flush()

                processed.add(code)
                if checkpoint_path:
                    cls.save_checkpoint(checkpoint_path, processed)

                status = "Processed and Cleaned" if clean_output else "Processed"
                logger.info("%s %s", status, code)
        finally:
            combined_fh.close()
            if cleaned_fh:
                cleaned_fh.close()

    @classmethod
    def _process_posts_batched(
        cls,
        media_items,
        outdir,
        device=None,
        checkpoint_path=None,
        combined_file_path=None,
        clean_output=True,
        progress=None,
    ):
        device = _resolve_device(device)

        def notify(message: str, *, warning: bool = False):
            if progress:
                progress(message)
            if warning:
                logger.warning(message)
            else:
                logger.info(message)

        if not media_items:
            notify("No media items were fetched; nothing to process.", warning=True)
            return

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
            logger.info("Skipped %s already-processed posts", skipped)
        if not unprocessed:
            logger.info("All fetched posts were already processed!")
            return

        notify(f"Processing {len(unprocessed)} new posts...")
        handler = ModelHandler(device)

        if not combined_file_path:
            combined_file_path = os.path.join(outdir, "all_combined.txt")
        ensure_dir(os.path.dirname(combined_file_path))

        try:
            post_records, image_work, video_work = cls._prepare_post_records(unprocessed, outdir, notify)
            if not post_records:
                raise RuntimeError("No downloaded Instagram media could be prepared for processing.")

            cls._run_ocr_batch(handler, image_work, notify)
            cls._run_transcription_batch(handler, video_work, notify)
            cls._write_post_outputs(
                post_records,
                processed,
                checkpoint_path,
                combined_file_path,
                clean_output,
            )
        finally:
            handler.unload_all()
            _cleanup_runtime()

        logger.info("Processing complete!")

    @classmethod
    def process_posts(
        cls,
        media_items,
        outdir,
        device=None,
        checkpoint_path=None,
        combined_file_path=None,
        clean_output=True,
        progress=None,
    ):
        """Main orchestration function for Instagram."""
        return cls._process_posts_batched(
            media_items,
            outdir,
            device=device,
            checkpoint_path=checkpoint_path,
            combined_file_path=combined_file_path,
            clean_output=clean_output,
            progress=progress,
        )


class YoutubeService:
    """Business logic for YouTube audio extraction and transcription."""

    @classmethod
    def process_video(cls, url, outdir, device=None, handler=None, progress=None):
        device = _resolve_device(device)

        def notify(message):
            if progress:
                progress(message)
            logger.info(message)

        ensure_dir(outdir)
        audio_path = None
        owns_handler = handler is None

        try:
            notify(f"Fetching audio from YouTube: {url}")
            fetcher = YoutubeFetcher(outdir)
            audio_path, title, quality_str, duration = fetcher.download_audio(url)
            notify(f"Audio ready: {title}")

            if owns_handler:
                handler = ModelHandler(device)

            notify("Preparing YouTube audio transcription")

            if duration:
                vid_mins = duration // 60
                vid_secs = duration % 60
                notify(f"Video length: {vid_mins}m {vid_secs}s")
                notify(f"Estimated transcription time: {estimate_transcription_time(duration)}")

            monitor = GPUPowerMonitor()
            monitor.start()
            start_time = time.time()
            with inference_lock():
                try:
                    notify("Loading Whisper transcription model")
                    handler.load_audio()
                    notify("Transcribing audio")
                    transcript = run_whisper_audio(audio_path, handler)
                finally:
                    if owns_handler:
                        notify("Unloading Whisper transcription model")
                        handler.unload_audio()

            actual_time_secs = time.time() - start_time
            metrics = monitor.stop(actual_time_secs)

            actual_mins = int(actual_time_secs // 60)
            actual_secs = int(actual_time_secs % 60)
            actual_time_str = f"{actual_mins} min {actual_secs} sec" if actual_mins > 0 else f"{actual_secs} sec"

            notify(f"Actual transcription time: {actual_time_str}")
            if duration:
                rtf = actual_time_secs / max(duration, 1)
                notify(f"Transcription speed: {rtf:.2f}x video length")

            if metrics:
                logger.info(
                    "Whisper GPU metrics: util %.1f%% | VRAM %.2f GB | power %.1f W | energy %.4f Wh",
                    metrics["avg_util_pct"],
                    metrics["max_vram_gb"],
                    metrics["avg_power_w"],
                    metrics["energy_wh"],
                )

            if metrics and metrics.get("max_vram_gb", 0) >= 3.7:
                notify("VRAM headroom is low for Whisper on a 4GB GPU")

            if transcript:
                safe_title = sanitize_filename(title) or "youtube_transcription_unknown"
                transcript_file = os.path.join(outdir, f"{safe_title}.txt")
                wrapped_transcript = textwrap.fill(transcript, width=80)

                with open(transcript_file, "w", encoding="utf-8") as f:
                    f.write(f"Title: {title}\n")
                    f.write(f"URL: {url}\n")
                    f.write(f"Original Audio Quality: {quality_str}\n")
                    f.write("-" * 80 + "\n")
                    f.write(wrapped_transcript + "\n")
                    f.write("-" * 80 + "\n")

                notify(f"Transcript saved: {os.path.basename(transcript_file)}")
            else:
                notify("No transcript was generated for this video")
                logger.warning("No transcript was generated for this video.")

        except Exception as exc:
            logger.error("Failed to process YouTube video: %s", exc)
            raise

        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except Exception:
                    pass
            if owns_handler and handler is not None:
                handler.unload_all()
            _cleanup_runtime()
