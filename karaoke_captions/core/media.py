import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .style import Style

BROWSER_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".weba"}
BROWSER_VIDEO = {".mp4", ".m4v", ".webm"}

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class FFmpegError(RuntimeError):
    pass


class RenderCancelled(Exception):
    pass


def ffmpeg_bin() -> str | None:
    return os.environ.get("FFMPEG") or shutil.which("ffmpeg")


def ffprobe_bin() -> str | None:
    return os.environ.get("FFPROBE") or shutil.which("ffprobe")


def _require(binary: str | None, name: str) -> str:
    if not binary:
        raise FFmpegError(f"{name} was not found. Install ffmpeg and make sure it is on your PATH.")
    return binary


@dataclass
class MediaInfo:
    duration: float
    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def probe(path: Path) -> MediaInfo:
    cmd = [_require(ffprobe_bin(), "ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if result.returncode != 0:
        raise FFmpegError(f"Could not read media file: {result.stderr.strip()[-400:]}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next(
        (s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0)
    if not duration:
        duration = max((float(s.get("duration") or 0) for s in streams), default=0)
    return MediaInfo(
        duration=round(duration, 3),
        has_video=video is not None,
        has_audio=audio is not None,
        width=int(video["width"]) if video else None,
        height=int(video["height"]) if video else None,
    )


def run_ffmpeg(
    args: list[str],
    cwd: Path,
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    cmd = [_require(ffmpeg_bin(), "ffmpeg"), "-y", "-hide_banner", "-nostats", "-loglevel", "error"]
    if on_progress:
        cmd += ["-progress", "pipe:1"]
    cmd += args
    log_path = cwd / "ffmpeg.log"
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE if on_progress else subprocess.DEVNULL,
            stderr=log,
            stdin=subprocess.DEVNULL,
            text=True,
            creationflags=_NO_WINDOW,
        )
        try:
            if on_progress and proc.stdout:
                for raw in proc.stdout:
                    if should_cancel and should_cancel():
                        proc.terminate()
                        proc.wait(timeout=10)
                        raise RenderCancelled()
                    key, _, value = raw.strip().partition("=")
                    if key in ("out_time_us", "out_time_ms") and duration and value.isdigit():
                        on_progress(min(0.999, int(value) / 1_000_000 / duration))
            proc.wait()
        except BaseException:
            if proc.poll() is None:
                proc.kill()
            raise
    if proc.returncode != 0:
        tail = log_path.read_text("utf-8", errors="replace").strip()[-800:]
        raise FFmpegError(tail or f"ffmpeg exited with code {proc.returncode}")


def make_browser_proxy(src: Path, info: MediaInfo, dest_dir: Path, stem: str = "preview") -> Path:
    """Return a file the browser can play, transcoding only when needed."""
    ext = src.suffix.lower()
    if info.has_video:
        if ext in BROWSER_VIDEO:
            return src
        dest = dest_dir / f"{stem}.mp4"
        run_ffmpeg(
            ["-i", str(src), "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", dest.name],
            cwd=dest_dir,
        )
        return dest
    if ext in BROWSER_AUDIO:
        return src
    dest = dest_dir / f"{stem}.m4a"
    run_ffmpeg(["-i", str(src), "-vn", "-c:a", "aac", "-b:a", "160k", dest.name], cwd=dest_dir)
    return dest


def generate_aurora(dest: Path) -> None:
    """Create the built-in animated gradient template video."""
    source = (
        "gradients=s=1280x720:c0=0x0f172a:c1=0x6d28d9:c2=0x0891b2:c3=0x1e1b4b"
        ":n=4:speed=0.012:d=20:r=30"
    )
    run_ffmpeg(
        ["-f", "lavfi", "-i", source, "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", dest.name],
        cwd=dest.parent,
    )


def _filter_path(path: Path, cwd: Path) -> str:
    try:
        rel = os.path.relpath(path, cwd)
    except ValueError:
        rel = str(path)
    return rel.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _ass_filter(ass_path: Path, fonts_dir: Path | None, cwd: Path) -> str:
    parts = [f"ass={_filter_path(ass_path, cwd)}"]
    if fonts_dir:
        parts.append(f"fontsdir={_filter_path(fonts_dir, cwd)}")
    return ":".join(parts)


def _fit_filter(style: Style, fit: str) -> str:
    w, h = style.width, style.height
    if fit == "contain":
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"


def matte_path(ass_path: Path) -> Path:
    return ass_path.with_name(ass_path.stem + ".matte.ass")


def _transparent_graph(ass_path: Path, fonts_dir: Path | None, cwd: Path, source: str, pix_fmt: str, pre: str = "") -> str:
    """Filter graph producing captions with a correct straight alpha channel.

    The ass filter's alpha mode squares partial alpha (a 50% box comes out at
    25%) and premultiplies the color. Instead, render the captions on black for
    color, render an all-white copy on black as a coverage matte, merge the
    matte in as alpha and un-premultiply.
    """
    color = _ass_filter(ass_path, fonts_dir, cwd)
    matte = _ass_filter(matte_path(ass_path), fonts_dir, cwd)
    unpremultiply = "if(y\\,min(255\\,(x*255+y/2)/y)\\,0)"
    head = f"{source},format=rgb24{',' + pre if pre else ''},split[c][m]"
    tail = f"format={pix_fmt}" if pix_fmt.startswith("rgb") else f"scale=out_color_matrix=bt709:out_range=tv,format={pix_fmt}"
    return (
        f"{head};[c]{color},format=gbrp[cc];[m]{matte},format=gbrp,split[m1][m2];"
        f"[cc][m1]lut2=c0='{unpremultiply}':c1='{unpremultiply}':c2='{unpremultiply}'[st];"
        f"[m2]extractplanes=g[a];[st][a]alphamerge,{tail}[v]"
    )


def snapshot(ass_path: Path, fonts_dir: Path | None, style: Style, at: float, work_dir: Path) -> bytes:
    """Render the captions at one timestamp to a transparent PNG using libass."""
    out = work_dir / "snapshot.png"
    source = f"color=c=black:s={style.width}x{style.height}:r=100:d=1"
    graph = _transparent_graph(ass_path, fonts_dir, work_dir, source, "rgba", pre=f"setpts=PTS+{max(0.0, at):.2f}/TB")
    run_ffmpeg(["-filter_complex", graph, "-map", "[v]", "-frames:v", "1", "-c:v", "png", out.name], cwd=work_dir)
    return out.read_bytes()


@dataclass
class RenderOptions:
    format: str = "mov"
    background: str = "color"
    bg_color: str = "#000000"
    include_audio: bool = False
    fit: str = "cover"

    @classmethod
    def from_dict(cls, data: dict | None) -> "RenderOptions":
        data = data or {}
        fmt = data.get("format", "mov")
        bg = data.get("background", "color")
        return cls(
            format=fmt if fmt in ("mov", "webm", "mp4") else "mov",
            background=bg if bg in ("color", "media", "template") else "color",
            bg_color=str(data.get("bg_color") or "#000000"),
            include_audio=bool(data.get("include_audio", False)),
            fit="contain" if data.get("fit") == "contain" else "cover",
        )

    @property
    def extension(self) -> str:
        return self.format


def render(
    ass_path: Path,
    fonts_dir: Path | None,
    style: Style,
    duration: float,
    options: RenderOptions,
    work_dir: Path,
    media_path: Path,
    media_info: MediaInfo,
    template_path: Path | None,
    on_progress: Callable[[float], None],
    should_cancel: Callable[[], bool],
) -> Path:
    from .colors import normalize_hex

    w, h, fps = style.width, style.height, style.fps
    out = work_dir / f"captions.{options.extension}"
    ass = _ass_filter(ass_path, fonts_dir, work_dir)
    dur = f"{duration:.3f}"
    args: list[str] = []

    if options.format in ("mov", "webm"):
        source = f"color=c=black:s={w}x{h}:r={fps}:d={dur}"
        audio = options.include_audio and media_info.has_audio
        if options.format == "mov":
            graph = _transparent_graph(ass_path, fonts_dir, work_dir, source, "yuva444p10le")
            codec = ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le", "-vendor", "apl0"]
            audio_codec = ["-c:a", "pcm_s16le"]
        else:
            graph = _transparent_graph(ass_path, fonts_dir, work_dir, source, "yuva420p")
            codec = ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30", "-row-mt", "1",
                     "-deadline", "good", "-cpu-used", "4", "-auto-alt-ref", "0"]
            audio_codec = ["-c:a", "libopus", "-b:a", "160k"]
        if audio:
            args += ["-i", str(media_path)]
        args += ["-filter_complex", graph, "-map", "[v]", *codec,
                 "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
        args += ["-map", "0:a:0", *audio_codec] if audio else ["-an"]
        args += ["-t", dur, out.name]
    else:
        if options.background == "media" and media_info.has_video:
            args += ["-i", str(media_path)]
            vf = f"{_fit_filter(style, options.fit)},fps={fps},{ass}"
            maps = ["-map", "0:v:0", "-map", "0:a:0?"]
        elif options.background == "template" and template_path:
            args += ["-stream_loop", "-1", "-i", str(template_path), "-i", str(media_path)]
            vf = f"{_fit_filter(style, options.fit)},fps={fps},{ass}"
            maps = ["-map", "0:v:0", "-map", "1:a:0?"]
        else:
            color = normalize_hex(options.bg_color, "#000000").replace("#", "0x")
            args += ["-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={fps}:d={dur}", "-i", str(media_path)]
            vf = ass
            maps = ["-map", "0:v:0", "-map", "1:a:0?"]
        vf = vf.replace(ass, f"format=rgb24,{ass}")
        args += ["-vf", f"{vf},scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", *maps,
                 "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                 "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", dur, out.name]

    run_ffmpeg(args, cwd=work_dir, duration=duration, on_progress=on_progress, should_cancel=should_cancel)
    return out
