import argparse
import json
import shutil
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

from . import __version__


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .server import create_app

    app = create_app(Path(args.workspace) if args.workspace else None)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"\n  Karaoke Captions {__version__} is running at {url}\n  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def _console_steps():
    from .core.progress import Steps

    labels = {"decode": "Decoding audio", "download": "Downloading model", "load": "Loading model", "transcribe": "Transcribing"}

    class ConsoleSteps(Steps):
        def _line(self, step_id: str, status: str, detail: str | None, end: str = "") -> None:
            print(f"\r  {labels.get(step_id, step_id):<18} {status:>7}  {detail or ''}".ljust(90), end=end, flush=True)

        def begin(self, step_id, detail="", **_):
            self._line(step_id, "0%", detail)

        def update(self, step_id, progress, detail=None, *, estimated=False, heartbeat=False):
            self._line(step_id, f"{'~' if estimated else ''}{progress * 100:.0f}%", detail)

        def done(self, step_id, detail=None):
            self._line(step_id, "done", detail, end="\n")

        def skip(self, step_id, detail=""):
            self._line(step_id, "skipped", detail, end="\n")

    return ConsoleSteps()


def cmd_transcribe(args: argparse.Namespace) -> None:
    from .core.transcribe import TranscribeOptions, transcribe

    audio = Path(args.audio)
    out = Path(args.out) if args.out else audio.with_suffix(".words.json")
    options = TranscribeOptions.from_dict(
        {"model": args.model, "language": args.language, "device": args.device, "prompt": args.prompt}
    )

    result = transcribe(audio, options, steps=_console_steps())
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), "utf-8")
    print(f"  Saved {len(result['words'])} words to {out}")


def cmd_render(args: argparse.Namespace) -> None:
    from .core import media
    from .core.ass_writer import build_ass, write_ass_files
    from .core.fonts import FontRegistry
    from .core.layout import parse_words
    from .core.style import Style

    audio = Path(args.audio)
    data = json.loads(Path(args.words).read_text("utf-8-sig"))
    words = parse_words(data.get("words", []) if isinstance(data, dict) else data)

    style_data: dict = {}
    if args.preset:
        preset = json.loads(Path(args.preset).read_text("utf-8"))
        style_data.update(preset.get("style", preset))
    for item in args.set or []:
        key, _, value = item.partition("=")
        style_data[key.strip()] = value.strip()
    style = Style.from_dict(style_data)

    info = media.probe(audio)
    out = Path(args.out) if args.out else audio.with_name(f"{audio.stem}-captions.{args.format}")
    ass_text = build_ass(words, style, title=audio.stem)
    if out.suffix.lower() == ".ass":
        out.write_text(ass_text, "utf-8")
        print(f"  Saved {out}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        ass_path = work / "captions.ass"
        write_ass_files(ass_path, words, style, title=audio.stem)
        registry = FontRegistry(work / "fonts", work / "fonts.json", work / "fontdirs")
        registry.scan()
        options = media.RenderOptions.from_dict(
            {"format": args.format, "background": "media" if info.has_video else "color",
             "bg_color": args.bg_color, "include_audio": args.audio_track}
        )

        def progress(value: float) -> None:
            print(f"\r  Rendering… {value * 100:5.1f}%", end="", flush=True)

        result = media.render(ass_path, registry.fonts_dir_for(style.font_family), style, info.duration, options,
                              work, audio.resolve(), info, None, progress, lambda: False)
        shutil.move(str(result), out)
    print(f"\n  Saved {out}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="karaoke-captions", description="Word-by-word karaoke captions from audio.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Start the web app (default)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--workspace", help="Folder for projects, fonts and renders (default: ./workspace)")
    serve.add_argument("--no-browser", action="store_true", help="Don't open the browser automatically")
    serve.set_defaults(func=cmd_serve)

    tr = sub.add_parser("transcribe", help="Transcribe audio to words.json without the web app")
    tr.add_argument("audio")
    tr.add_argument("--out")
    tr.add_argument("--model", default="small")
    tr.add_argument("--language", default="")
    tr.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    tr.add_argument("--prompt", default="", help="Names and terms to help recognition, e.g. 'Lyzr, Tatum'")
    tr.set_defaults(func=cmd_transcribe)

    rd = sub.add_parser("render", help="Render captions from words.json without the web app")
    rd.add_argument("audio")
    rd.add_argument("words")
    rd.add_argument("--preset", help="Preset JSON exported from the web app")
    rd.add_argument("--set", action="append", metavar="KEY=VALUE", help="Override a style field, e.g. --set highlight_color=#FACC15")
    rd.add_argument("--format", default="mov", choices=["mov", "webm", "mp4", "ass"])
    rd.add_argument("--bg-color", default="#000000", help="Background for mp4 when the input has no video")
    rd.add_argument("--audio-track", action="store_true", help="Include the audio in transparent overlays")
    rd.add_argument("--out")
    rd.set_defaults(func=cmd_render)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["serve", *(argv or sys.argv[1:])])
    args.func(args)


if __name__ == "__main__":
    main()
