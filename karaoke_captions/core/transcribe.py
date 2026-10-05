import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from .progress import Steps, Timings, estimate, fmt_bytes, fmt_duration

MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]

REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
MODEL_FILES = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*")

# First-run guesses, replaced by measured values: seconds to load, and seconds of work per second of audio.
LOAD_GUESS = {"tiny": 2, "base": 3, "small": 5, "medium": 12, "large-v3": 25, "large-v3-turbo": 15}
SPEED_GUESS = {
    "cpu": {"tiny": 0.06, "base": 0.1, "small": 0.25, "medium": 0.6, "large-v3": 1.2, "large-v3-turbo": 0.5},
    "cuda": {"tiny": 0.02, "base": 0.02, "small": 0.03, "medium": 0.05, "large-v3": 0.08, "large-v3-turbo": 0.04},
}
TRANSCRIBE_OVERHEAD = 1.5
DEVICE_NAMES = {"cpu": "CPU", "cuda": "GPU (CUDA)"}
HINTS = {
    "cpu": "This is taking much longer than expected. Large models are slow on CPU; try Small or Large v3 Turbo.",
    "cuda": "This is taking much longer than expected. If it's stuck, cancel and choose Device: CPU.",
}

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


def _in_background(fn: Callable, on_tick: Callable[[], None], should_cancel, interval: float = 0.25):
    """Run a blocking call on a worker thread so progress keeps ticking and Cancel stays responsive."""
    result: dict = {}

    def target():
        try:
            result["value"] = fn()
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    while True:
        thread.join(interval)
        if not thread.is_alive():
            break
        if should_cancel and should_cancel():
            raise TranscriptionCancelled()
        on_tick()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _decode_audio(path: Path, duration: float, steps: Steps, should_cancel):
    """Decode to 16 kHz mono float32 with ffmpeg (avoids PyAV version breakage)."""
    import numpy as np

    from .media import FFmpegError, _NO_WINDOW, ffmpeg_bin

    binary = ffmpeg_bin()
    if not binary:
        raise FFmpegError("ffmpeg was not found. Install ffmpeg and make sure it is on your PATH.")
    steps.begin("decode")
    bytes_per_second = 16000 * 4
    expected = duration * bytes_per_second
    proc = subprocess.Popen(
        [binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )
    chunks, received = [], 0
    try:
        while chunk := proc.stdout.read(1 << 20):
            chunks.append(chunk)
            received += len(chunk)
            if should_cancel and should_cancel():
                raise TranscriptionCancelled()
            if expected:
                done = received / bytes_per_second
                steps.update("decode", min(0.999, received / expected), f"{fmt_duration(done)} of {fmt_duration(duration)}")
        stderr = proc.stderr.read()
        proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        raise
    if proc.returncode != 0:
        raise FFmpegError(stderr.decode("utf-8", "replace").strip()[-400:] or "Could not decode audio")
    steps.done("decode", f"{fmt_duration(received / bytes_per_second)} of audio")
    return np.frombuffer(b"".join(chunks), dtype=np.float32)


def _cached_model_dir(repo_id: str) -> str | None:
    from huggingface_hub import try_to_load_from_cache

    model_bin = try_to_load_from_cache(repo_id, "model.bin")
    if not isinstance(model_bin, str):
        return None
    folder = os.path.dirname(model_bin)
    if all(os.path.exists(os.path.join(folder, f)) for f in ("config.json", "tokenizer.json")):
        return folder
    return None


def _model_dir(name: str, steps: Steps, should_cancel) -> str:
    repo_id = REPOS[name]
    folder = _cached_model_dir(repo_id)
    if folder:
        steps.skip("download", "Already downloaded")
        return folder
    return _download(repo_id, steps, should_cancel)


def _download(repo_id: str, steps: Steps, should_cancel) -> str:
    from huggingface_hub import HfApi, hf_hub_download

    steps.begin("download", "Contacting Hugging Face…", hint="No data received for a while. Check your internet connection.")
    try:
        info = HfApi().model_info(repo_id, files_metadata=True)
    except Exception as exc:
        raise RuntimeError(f"Couldn't reach Hugging Face to download the model. Check your internet connection. ({exc})")
    files = sorted(
        ((s.rfilename, s.size or 0) for s in info.siblings if any(fnmatch(s.rfilename, p) for p in MODEL_FILES)),
        key=lambda f: f[1],
    )
    total = sum(size for _, size in files) or 1
    state = {"done": 0, "current": 0}

    class Bar:
        """Stands in for tqdm so hf_hub_download reports downloaded bytes.

        Having update_transfer makes Xet downloads report through this one bar,
        like huggingface_hub's own snapshot aggregation. Xet reports bytes written
        to disk in large bursts, so bytes received from the network count too.
        """

        def __init__(self, *args, **kwargs):
            self.total = kwargs.get("total")
            self.n = kwargs.get("initial") or 0
            self.received = self.n
            state["current"] = self.n

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def _report(self):
            if should_cancel and should_cancel():
                raise TranscriptionCancelled()
            current = max(self.n, self.received)
            state["current"] = min(current, self.total) if self.total else current

        def update(self, n=1):
            self.n += n or 0
            self._report()

        def update_transfer(self, n=1):
            self.received += n or 0
            self._report()

        def set_postfix_str(self, *args, **kwargs):
            pass

        def set_transfer_postfix_str(self, *args, **kwargs):
            pass

        def set_description(self, *args, **kwargs):
            pass

        def refresh(self, *args, **kwargs):
            pass

        def close(self):
            pass

    def fetch() -> str:
        path = ""
        for filename, size in files:
            try:
                path = hf_hub_download(repo_id, filename, tqdm_class=Bar)
            except TypeError:
                path = hf_hub_download(repo_id, filename)
            state["done"] += size
            state["current"] = 0
        return os.path.dirname(path)

    started = time.monotonic()
    last = -1

    def tick():
        nonlocal last
        got = state["done"] + state["current"]
        if got == last:
            return
        last = got
        rate = got / max(0.5, time.monotonic() - started)
        steps.update("download", min(0.999, got / total), f"{fmt_bytes(got)} / {fmt_bytes(total)} · {fmt_bytes(rate)}/s")

    folder = _in_background(fetch, tick, should_cancel)
    steps.done("download", f"{fmt_bytes(total)} downloaded")
    return folder


def _load_model(name: str, folder: str, device: str):
    from faster_whisper import WhisperModel

    device, compute_type = _resolve_device(device)
    key = (name, device, compute_type)
    with _models_lock:
        if key not in _models:
            _models[key] = WhisperModel(folder, device=device, compute_type=compute_type)
        return _models[key]


def _is_loaded(name: str, device: str) -> bool:
    return (name, *_resolve_device(device)) in _models


def transcribe(
    audio: Path,
    options: TranscribeOptions,
    steps: Steps | None = None,
    should_cancel: Callable[[], bool] | None = None,
    duration: float | None = None,
    timings: Timings | None = None,
) -> dict:
    steps = steps or Steps()
    timings = timings or Timings()
    if duration is None:
        from .media import probe

        try:
            duration = probe(audio).duration
        except Exception:
            duration = 0.0
    samples = _decode_audio(audio, duration or 0.0, steps, should_cancel)
    duration = len(samples) / 16000 or duration or 1.0
    folder = _model_dir(options.model, steps, should_cancel)

    device = options.device
    if device == "auto":
        device = _resolve_device("auto")[0]
    try:
        return _run(samples, duration, folder, options, device, steps, should_cancel, timings)
    except TranscriptionCancelled:
        raise
    except Exception as exc:
        gpu_missing = any(s in str(exc).lower() for s in ("cublas", "cudnn", "cuda", "libcu"))
        if device != "cuda" or options.device != "auto" or not gpu_missing:
            raise
        steps.begin("load", "GPU libraries not found, falling back to CPU…")
        return _run(samples, duration, folder, options, "cpu", steps, should_cancel, timings)


def _run(samples, duration: float, folder: str, options: TranscribeOptions, device: str,
         steps: Steps, should_cancel, timings: Timings) -> dict:
    where = DEVICE_NAMES.get(device, device.upper())
    hint = HINTS.get(device)

    if _is_loaded(options.model, device):
        model = _load_model(options.model, folder, device)
        steps.done("load", f"'{options.model}' already in memory")
    else:
        load_key = f"load:{options.model}:{device}"
        expected = timings.get(load_key, LOAD_GUESS.get(options.model, 10))
        steps.begin("load", f"'{options.model}' on {where}", expected=expected, hint=hint)
        t0 = time.monotonic()
        model = _in_background(
            lambda: _load_model(options.model, folder, device),
            lambda: steps.update("load", estimate(time.monotonic() - t0, expected), estimated=True),
            should_cancel,
        )
        took = time.monotonic() - t0
        timings.record(load_key, took)
        steps.done("load", f"'{options.model}' on {where} in {took:.1f}s")

    speed_key = f"speed:{options.model}:{device}"
    speed = timings.get(speed_key, SPEED_GUESS.get(device, SPEED_GUESS["cpu"]).get(options.model, 0.5))
    expected = TRANSCRIBE_OVERHEAD + duration * speed
    steps.begin("transcribe", f"on {where}", expected=expected, hint=hint)

    found: queue.Queue = queue.Queue()
    stop = threading.Event()

    def work():
        segments, info = model.transcribe(
            samples,
            language=options.language,
            word_timestamps=True,
            vad_filter=True,
            initial_prompt=options.prompt or None,
        )
        for segment in segments:
            if stop.is_set():
                break
            found.put(segment)
        return info

    words: list[dict] = []
    heard = 0.0
    t0 = time.monotonic()

    def collect():
        nonlocal heard
        new = False
        while True:
            try:
                segment = found.get_nowait()
            except queue.Empty:
                break
            new = True
            heard = max(heard, segment.end)
            for word in segment.words or []:
                text = word.word.strip()
                if text:
                    words.append({
                        "text": text,
                        "start": round(word.start, 3),
                        "end": round(word.end, 3),
                        "prob": round(float(word.probability), 3),
                    })
        real = min(0.999, heard / duration)
        guess = estimate(time.monotonic() - t0, expected)
        steps.update("transcribe", max(real, guess), f"{len(words)} words · {fmt_duration(heard)} of {fmt_duration(duration)}",
                     estimated=guess > real, heartbeat=new)

    try:
        info = _in_background(work, collect, should_cancel)
    finally:
        stop.set()
    collect()
    took = time.monotonic() - t0
    if duration >= 3:
        timings.record(speed_key, max(0.005, (took - TRANSCRIBE_OVERHEAD) / duration))
    steps.done("transcribe", f"{len(words)} words in {took:.1f}s")
    return {"language": info.language, "words": words}
