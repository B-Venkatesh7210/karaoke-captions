import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]

_models: dict[tuple[str, str, str], object] = {}
_models_lock = threading.Lock()


class TranscriptionCancelled(Exception):
    pass


@dataclass
class TranscribeOptions:
    model: str = "small"
    language: str | None = None
    device: str = "auto"
    prompt: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> "TranscribeOptions":
        data = data or {}
        language = (data.get("language") or "").strip() or None
        if language == "auto":
            language = None
        model = data.get("model") or cls.model
        device = data.get("device") or "auto"
        return cls(
            model=model if model in MODELS else cls.model,
            language=language,
            device=device if device in ("auto", "cpu", "cuda") else "auto",
            prompt=(data.get("prompt") or "").strip(),
        )


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _resolve_device(device: str) -> tuple[str, str]:
    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    return device, ("float16" if device == "cuda" else "int8")


def _load_model(name: str, device: str):
    from faster_whisper import WhisperModel

    device, compute_type = _resolve_device(device)
    key = (name, device, compute_type)
    with _models_lock:
        if key not in _models:
            _models[key] = WhisperModel(name, device=device, compute_type=compute_type)
        return _models[key]


def _decode_audio(path: Path):
    """Decode to 16 kHz mono float32 with ffmpeg (avoids PyAV version breakage)."""
    import numpy as np

    from .media import FFmpegError, _NO_WINDOW, ffmpeg_bin

    binary = ffmpeg_bin()
    if not binary:
        raise FFmpegError("ffmpeg was not found. Install ffmpeg and make sure it is on your PATH.")
    result = subprocess.run(
        [binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
        capture_output=True,
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise FFmpegError(result.stderr.decode("utf-8", "replace").strip()[-400:] or "Could not decode audio")
    return np.frombuffer(result.stdout, dtype=np.float32)


def transcribe(
    audio: Path,
    options: TranscribeOptions,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    report = on_progress or (lambda _p, _m: None)
    report(0.0, "Decoding audio…")
    samples = _decode_audio(audio)

    device = options.device
    if device == "auto":
        device = _resolve_device("auto")[0]
    try:
        return _run(samples, options, device, report, should_cancel)
    except TranscriptionCancelled:
        raise
    except Exception as exc:
        gpu_missing = any(s in str(exc).lower() for s in ("cublas", "cudnn", "cuda", "libcu"))
        if device != "cuda" or options.device != "auto" or not gpu_missing:
            raise
        report(0.0, "GPU libraries not found, falling back to CPU…")
        return _run(samples, options, "cpu", report, should_cancel)


def _run(samples, options: TranscribeOptions, device: str, report, should_cancel) -> dict:
    report(0.0, f"Loading Whisper model '{options.model}' on {device.upper()} (the first run downloads it)…")
    model = _load_model(options.model, device)

    report(0.0, f"Transcribing on {device.upper()}…")
    segments, info = model.transcribe(
        samples,
        language=options.language,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=options.prompt or None,
    )

    duration = float(info.duration or 0) or 1.0
    words = []
    for segment in segments:
        if should_cancel and should_cancel():
            raise TranscriptionCancelled()
        for word in segment.words or []:
            text = word.word.strip()
            if not text:
                continue
            words.append(
                {
                    "text": text,
                    "start": round(word.start, 3),
                    "end": round(word.end, 3),
                    "prob": round(float(word.probability), 3),
                }
            )
        report(min(0.999, segment.end / duration), f"Transcribed {len(words)} words")

    return {"language": info.language, "words": words}
