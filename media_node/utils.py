import os
import re
import json
import logging
import requests
import numpy as np
from pathlib import Path
import threading
import subprocess
import time

# Configure Logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

class NpEncoder(json.JSONEncoder):
    """JSON encoder for numpy/torch"""
    def default(self, obj):
        if hasattr(obj, 'tolist'):
            return obj.tolist()
        return super().default(obj)

def ensure_dir(path):
    """Create directory if needed"""
    Path(path).mkdir(parents=True, exist_ok=True)

def sanitize_filename(title):
    """NEW: Removes invalid characters for Windows/Linux filenames"""
    # Strips out \ / * ? " < > | :
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title)
    return safe_title.strip()

# ... [Keep your existing extract_shortcode, get_content_type, download_file functions here untouched] ...
def extract_shortcode(url):
    """Extract shortcode from Instagram URL"""
    if not url:
        raise ValueError("URL cannot be empty")

    if not url.startswith('http'):
        return url.strip()

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
    
    
    
def estimate_transcription_time(video_duration_seconds, cuda_cores=1024):
    """Intelligent heuristic to calculate expected Whisper transcription time."""
    if not video_duration_seconds:
        return "Unknown"

    # Whisper medium on 4GB VRAM has a large fixed inference overhead for short clips.
    # Longer clips amortize that overhead and tend to run closer to real time.
    warmup_overhead = 210
    realtime_factor = 0.9
    total_seconds = warmup_overhead + (video_duration_seconds * realtime_factor)

    if video_duration_seconds >= 180:
        total_seconds = 75 + (video_duration_seconds * 1.15)
    
    mins = int(total_seconds // 60)
    secs = int(total_seconds % 60)
    
    if mins > 0:
        return f"{mins} min {secs} sec"
    return f"{secs} sec"



class GPUPowerMonitor:
    """
    Background thread that samples NVIDIA GPU metrics directly from the driver.
    Bypasses Windows Task Manager inaccuracies.
    """
    def __init__(self):
        self.running = False
        self.power_readings = []
        self.util_readings = []
        self.vram_readings = []
        self.thread = None
        self.has_nvidia = False
        
        try:
            subprocess.check_output(['nvidia-smi'])
            self.has_nvidia = True
        except Exception:
            logger.warning("nvidia-smi not found. GPU monitoring disabled.")

    def _poll(self):
        while self.running:
            try:
                # Queries Power (W), Compute Utilization (%), and VRAM Used (MB)
                output = subprocess.check_output(
                    ['nvidia-smi', '--query-gpu=power.draw,utilization.gpu,memory.used', '--format=csv,noheader,nounits'],
                    text=True
                )
                # Output looks like: "104.53, 98, 3850"
                parts = output.strip().split(',')
                if len(parts) == 3:
                    self.power_readings.append(float(parts[0].strip()))
                    self.util_readings.append(float(parts[1].strip()))
                    self.vram_readings.append(float(parts[2].strip()))
            except Exception:
                pass
            time.sleep(1) # Sample every 1 second

    def start(self):
        if not self.has_nvidia:
            return
        self.running = True
        self.power_readings, self.util_readings, self.vram_readings = [], [], []
        self.thread = threading.Thread(target=self._poll, daemon=True)
        self.thread.start()

    def stop(self, actual_duration_secs):
        if not self.has_nvidia or not self.running:
            return None
            
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
            
        if not self.power_readings:
            return None
            
        # Calculate Averages and Peaks
        avg_power = sum(self.power_readings) / len(self.power_readings)
        avg_util = sum(self.util_readings) / len(self.util_readings)
        max_vram_gb = max(self.vram_readings) / 1024.0 # Convert MB to GB
        
        # Calculate Energy
        energy_j = avg_power * actual_duration_secs
        energy_wh = energy_j / 3600
        
        return {
            "avg_power_w": avg_power,
            "avg_util_pct": avg_util,
            "max_vram_gb": max_vram_gb,
            "energy_j": energy_j,
            "energy_wh": energy_wh
        }
