import gc
import logging
import os
import shutil
import subprocess
import threading
import warnings
from contextlib import contextmanager
from typing import Any

# Set runtime defaults before importing torch/transformers. Users can override
# these in .env/.env.docker before the process starts.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", os.getenv("ML_CPU_THREADS", "2"))
os.environ.setdefault("MKL_NUM_THREADS", os.getenv("ML_CPU_THREADS", "2"))

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor
except ImportError as exc:
    raise RuntimeError("transformers and torch are required for Florence OCR") from exc

try:
    import whisper as openai_whisper
except ImportError:
    openai_whisper = None

try:
    from faster_whisper import WhisperModel as FasterWhisperModel
except ImportError:
    FasterWhisperModel = None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return max(int(value.strip()), minimum)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = (os.getenv(name) or default).strip().lower()
    if value in choices:
        return value
    logger.warning("Invalid value for %s=%r; using %s", name, value, default)
    return default


def _resolve_ffmpeg_executable() -> str:
    """Return the system or bundled FFmpeg executable."""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        import imageio_ffmpeg
    except ImportError:
        logger.warning("FFmpeg not found. Audio transcription is unavailable.")
        return "ffmpeg"

    return imageio_ffmpeg.get_ffmpeg_exe()


_FFMPEG_EXECUTABLE = _resolve_ffmpeg_executable()

warnings.filterwarnings("ignore")

try:
    torch_threads = _env_int("TORCH_NUM_THREADS", int(os.getenv("ML_CPU_THREADS", "2")), minimum=1)
    torch.set_num_threads(torch_threads)
except Exception:
    pass

_INFERENCE_LOCK = threading.Lock()
_INFERENCE_LOCK_ENABLED = _env_bool("MEDIA_INFERENCE_LOCK", True)


@contextmanager
def inference_lock():
    """Serialize heavy ML inference so two large models cannot load together."""
    if not _INFERENCE_LOCK_ENABLED:
        yield
        return
    with _INFERENCE_LOCK:
        yield


def _cleanup_memory() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _move_inputs_to_device(inputs: dict[str, Any], device: str) -> dict[str, Any]:
    moved = {}
    for key, value in inputs.items():
        moved[key] = value.to(device) if hasattr(value, "to") else value
    return moved


class ModelHandler:
    """
    Owns the currently resident ML model.

    Only one heavy model is kept in memory at a time. Loading Florence unloads
    Whisper; loading Whisper unloads Florence. This keeps the process inside a
    realistic CPU/RAM envelope on local Docker and Hugging Face CPU Spaces.
    """

    def __init__(self, device: str):
        self.device = device
        self.vlm_model = None
        self.vlm_processor = None
        self.audio_model = None
        self.audio_backend = None
        self._logged_audio_device = False

    def _log_audio_device_profile(self) -> None:
        if self._logged_audio_device or self.device != "cuda" or not torch.cuda.is_available():
            return

        props = torch.cuda.get_device_properties(0)
        major, minor = torch.cuda.get_device_capability(0)
        tensor_core_note = "likely" if major >= 7 and "GTX" not in props.name else "unlikely"
        logger.info(
            "Whisper device: %s | capability: %s.%s | VRAM: %.1f GB | fast FP16/Tensor Cores: %s",
            props.name,
            major,
            minor,
            props.total_memory / 1024**3,
            tensor_core_note,
        )
        if tensor_core_note == "unlikely":
            logger.info(
                "GTX-class GPUs can report high CUDA utilization while running Whisper medium "
                "slower than expected because FP16 lacks Tensor Core acceleration."
            )
        self._logged_audio_device = True

    def unload_vlm(self) -> None:
        if self.vlm_model is None and self.vlm_processor is None:
            return
        logger.info("Unloading Florence model")
        self.vlm_model = None
        self.vlm_processor = None
        _cleanup_memory()

    def unload_audio(self) -> None:
        if self.audio_model is None:
            return
        logger.info("Unloading Whisper model")
        self.audio_model = None
        self.audio_backend = None
        _cleanup_memory()

    def unload_all(self) -> None:
        self.unload_vlm()
        self.unload_audio()
        _cleanup_memory()

    def load_vlm(self) -> None:
        """Load Florence OCR and unload Whisper if needed."""
        if self.vlm_model is not None and self.vlm_processor is not None:
            return

        self.unload_audio()

        model_id = os.getenv("FLORENCE_MODEL_ID", "microsoft/Florence-2-base").strip()
        local_files_only = _env_bool("FLORENCE_LOCAL_FILES_ONLY", False) or _env_bool("TRANSFORMERS_OFFLINE", False)
        dtype = torch.float16 if self.device == "cuda" else torch.float32

        logger.info("Loading Florence processor: %s", model_id)
        self.vlm_processor = AutoProcessor.from_pretrained(
            model_id,
            trust_remote_code=True,
            local_files_only=local_files_only,
        )
        logger.info("Florence processor loaded")

        logger.info("Loading Florence model weights: %s", model_id)
        self.vlm_model = AutoModelForCausalLM.from_pretrained(
            model_id,
            trust_remote_code=True,
            torch_dtype=dtype,
            local_files_only=local_files_only,
        )
        logger.info("Florence model weights loaded")

        logger.info("Moving Florence model to device: %s", self.device)
        self.vlm_model = self.vlm_model.to(self.device)
        self.vlm_model.eval()
        logger.info("Florence ready")

    def load_audio(self) -> None:
        """Load Whisper and unload Florence if needed."""
        if self.audio_model is not None:
            return

        self._log_audio_device_profile()
        self.unload_vlm()

        backend = _env_choice("WHISPER_BACKEND", "openai", {"openai", "faster-whisper"})
        model_size = os.getenv("WHISPER_MODEL_SIZE", "small").strip()

        if backend == "faster-whisper":
            if FasterWhisperModel is None:
                raise RuntimeError(
                    "WHISPER_BACKEND=faster-whisper requires the faster-whisper package. "
                    "Install it or set WHISPER_BACKEND=openai."
                )
            compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip()
            device = "cuda" if self.device == "cuda" else "cpu"
            logger.info(
                "Loading faster-whisper model: size=%s device=%s compute_type=%s",
                model_size,
                device,
                compute_type,
            )
            self.audio_model = FasterWhisperModel(model_size, device=device, compute_type=compute_type)
            self.audio_backend = "faster-whisper"
            logger.info("faster-whisper ready")
            return

        if openai_whisper is None:
            raise RuntimeError(
                "WHISPER_BACKEND=openai requires the openai-whisper package. "
                "Install it or set WHISPER_BACKEND=faster-whisper."
            )

        logger.info("Loading OpenAI Whisper model: size=%s device=%s", model_size, self.device)
        self.audio_model = openai_whisper.load_model(model_size, device=self.device)
        self.audio_backend = "openai"
        logger.info("OpenAI Whisper ready")


def run_florence_ocr(image_path: str, handler: ModelHandler) -> str:
    """Run Florence OCR for one image using an already-loaded handler."""
    try:
        max_new_tokens = _env_int("OCR_MAX_NEW_TOKENS", 256, minimum=16)
        num_beams = _env_int("OCR_NUM_BEAMS", 1, minimum=1)

        logger.info("Opening image for OCR: %s", image_path)
        with Image.open(image_path) as source_image:
            image = source_image.convert("RGB")

        task = os.getenv("FLORENCE_OCR_TASK", "<OCR>").strip() or "<OCR>"
        logger.info("Preparing Florence OCR inputs")
        inputs = handler.vlm_processor(text=task, images=image, return_tensors="pt")
        inputs = _move_inputs_to_device(inputs, handler.device)

        if handler.device == "cuda" and handler.vlm_model.dtype == torch.float16:
            inputs["pixel_values"] = inputs["pixel_values"].to(torch.float16)

        logger.info(
            "Running Florence generate(): max_new_tokens=%s num_beams=%s",
            max_new_tokens,
            num_beams,
        )
        with torch.inference_mode():
            generated = handler.vlm_model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=max_new_tokens,
                num_beams=num_beams,
                do_sample=False,
            )
        logger.info("Florence generate() complete")

        logger.info("Decoding Florence output")
        text = handler.vlm_processor.batch_decode(generated, skip_special_tokens=False)[0]

        logger.info("Post-processing Florence output")
        parsed = handler.vlm_processor.post_process_generation(
            text,
            task=task,
            image_size=(image.width, image.height),
        )
        return parsed.get(task, "") or parsed.get("<OCR>", "")
    except Exception as exc:
        logger.error("OCR error on %s: %s", image_path, exc)
        return ""
    finally:
        try:
            del inputs
        except UnboundLocalError:
            pass
        try:
            del generated
        except UnboundLocalError:
            pass
        try:
            del image
        except UnboundLocalError:
            pass
        _cleanup_memory()


def _decode_audio_for_openai_whisper(audio_path: str) -> np.ndarray:
    if openai_whisper is None:
        raise RuntimeError("openai-whisper is not installed")

    command = [
        _FFMPEG_EXECUTABLE,
        "-nostdin",
        "-threads",
        "0",
        "-i",
        audio_path,
        "-f",
        "s16le",
        "-ac",
        "1",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(openai_whisper.audio.SAMPLE_RATE),
        "-",
    ]
    decoded_audio = subprocess.run(command, capture_output=True, check=True).stdout
    return np.frombuffer(decoded_audio, np.int16).flatten().astype(np.float32) / 32768.0


def run_whisper_audio(audio_path: str, handler: ModelHandler) -> str:
    """Run Whisper transcription for one audio/video file."""
    try:
        if handler.audio_backend == "faster-whisper":
            beam_size = _env_int("WHISPER_BEAM_SIZE", 1, minimum=1)
            logger.info("Running faster-whisper transcription: beam_size=%s", beam_size)
            segments, _info = handler.audio_model.transcribe(audio_path, beam_size=beam_size)
            return " ".join(segment.text.strip() for segment in segments if segment.text).strip()

        logger.info("Decoding audio for OpenAI Whisper")
        waveform = _decode_audio_for_openai_whisper(audio_path)
        logger.info("Running OpenAI Whisper transcription")
        result = handler.audio_model.transcribe(
            waveform,
            fp16=(handler.device == "cuda" and _env_bool("WHISPER_OPENAI_FP16", True)),
        )
        return result.get("text", "").strip()
    except Exception as exc:
        logger.warning("Audio error on %s: %s", audio_path, exc)
        return ""
    finally:
        if "waveform" in locals():
            del waveform
        _cleanup_memory()
