import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .core import media
from .core.ass_writer import build_ass, write_ass_files
from .core.fonts import FontRegistry
from .core.layout import parse_words
from .core.style import Style
from .core.transcribe import MODELS, TranscribeOptions, transcribe, whisper_available
from .jobs import Job, JobManager
from .projects import ProjectStore, TemplateStore, safe_filename

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
PRESETS_DIR = PACKAGE_DIR / "presets"
MAX_SNAPSHOT_WORDS = 200_000


def default_workspace() -> Path:
    return Path(os.environ.get("KARAOKE_WORKSPACE") or Path.cwd() / "workspace").resolve()


def create_app(workspace: Path | None = None) -> FastAPI:
    workspace = (workspace or default_workspace()).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    store = ProjectStore(workspace / "projects")
    templates = TemplateStore(workspace / "templates")
    fonts = FontRegistry(workspace / "fonts", workspace / "cache" / "fonts.json", workspace / "cache" / "fontdirs")
    user_presets_dir = workspace / "presets"
    user_presets_dir.mkdir(exist_ok=True)
    jobs = JobManager()

    app = FastAPI(title="Karaoke Captions", version=__version__)

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # ---------- helpers ----------

    def load_project(project_id: str) -> dict:
        try:
            return store.load(project_id)
        except KeyError:
            raise HTTPException(404, "Project not found")

    def media_path(project: dict) -> Path:
        return store.dir(project["id"]) / project["media"]["file"]

    def fonts_dir(style: Style) -> Path | None:
        if not fonts.ready.is_set():
            fonts.ready.wait(timeout=20)
        return fonts.fonts_dir_for(style.font_family)

    def resolve_inputs(project: dict, body: dict) -> tuple[Style, list]:
        style = Style.from_dict(body.get("style") or project.get("style"))
        raw_words = body.get("words") if body.get("words") is not None else project.get("words") or []
        if len(raw_words) > MAX_SNAPSHOT_WORDS:
            raise HTTPException(413, "Too many words")
        return style, parse_words(raw_words)

    def project_payload(project: dict) -> dict:
        job = jobs.active_for(project["id"], "transcribe")
        return {**project, "job": job.to_dict() if job else None}

    # ---------- startup ----------

    def ensure_builtin_template() -> None:
        if any(t.get("builtin") for t in templates.list()):
            return
        if not media.ffmpeg_bin():
            return
        dest = templates.root / "aurora.mp4"
        try:
            media.generate_aurora(dest)
            info = media.probe(dest)
        except Exception as exc:
            print(f"[karaoke] Could not create the built-in template: {exc}")
            return
        templates.add({
            "id": "aurora",
            "name": "Aurora gradient",
            "file": dest.name,
            "preview": None,
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "builtin": True,
        })

    @app.on_event("startup")
    def on_startup() -> None:
        fonts.scan_async()
        threading.Thread(target=ensure_builtin_template, daemon=True).start()
        for summary in store.list():
            if summary["status"] in ("uploading", "preparing", "transcribing"):
                project = store.load(summary["id"])
                project["status"] = "ready" if project.get("words") else "error"
                project["error"] = None if project.get("words") else "Interrupted. Start the transcription again."
                store.save(project)

    # ---------- system ----------

    @app.get("/api/style/defaults")
    def style_defaults():
        return Style().to_dict()

    @app.get("/api/health")
    def health():
        return {
            "version": __version__,
            "ffmpeg": media.ffmpeg_bin(),
            "ffprobe": media.ffprobe_bin(),
            "whisper": whisper_available(),
            "models": MODELS,
            "workspace": str(workspace),
        }

    # ---------- projects ----------

    @app.get("/api/projects")
    def list_projects():
        return store.list()

    @app.post("/api/projects")
    def create_project(
        file: UploadFile = File(...),
        words_file: UploadFile | None = File(None),
        model: str = Form("small"),
        language: str = Form(""),
        device: str = Form("auto"),
        prompt: str = Form(""),
    ):
        imported = None
        if words_file is not None and words_file.filename:
            try:
                data = json.loads(words_file.file.read().decode("utf-8-sig"))
                raw = data.get("words", []) if isinstance(data, dict) else data
                imported = [w.to_dict() for w in parse_words(raw)]
            except Exception:
                raise HTTPException(400, "words.json could not be read. Expected {\"words\": [{\"word\", \"start\", \"end\"}]}")
            if not imported:
                raise HTTPException(400, "words.json has no words")

        name = Path(file.filename or "Untitled").stem
        project = store.create(name)
        folder = store.dir(project["id"])
        dest = folder / ("source" + Path(safe_filename(file.filename or "", "media")).suffix.lower())
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)

        options = TranscribeOptions.from_dict({"model": model, "language": language, "device": device, "prompt": prompt})
        project["media"] = {"file": dest.name, "original_name": file.filename}
        project["status"] = "preparing"
        project["transcribe"] = options.__dict__
        store.save(project)

        jobs.submit("transcribe", project["id"], lambda job: _prepare_and_transcribe(job, project["id"], options, imported))
        return project_payload(store.load(project["id"]))

    def _prepare_and_transcribe(job: Job, project_id: str, options: TranscribeOptions, imported: list | None):
        project = store.load(project_id)
        folder = store.dir(project_id)
        try:
            job.message = "Reading media…"
            src = media_path(project)
            info = media.probe(src)
            if not info.has_audio:
                raise RuntimeError("This file has no audio track.")
            if not project["media"].get("duration"):
                job.message = "Preparing preview…"
                playback = media.make_browser_proxy(src, info, folder)
                project["media"].update(info.to_dict(), playback=playback.name)
                if info.has_video and info.width and info.height and info.height > info.width:
                    project["style"].update(width=1080, height=1920, font_size=80, max_words=3, margin_v=380)
                if info.has_video:
                    project["preview"] = {"background": "media", "template_id": None}

            if imported is not None:
                project.update(words=imported, status="ready", error=None)
                store.save(project)
                return None

            if not whisper_available():
                raise RuntimeError("faster-whisper is not installed. Run: pip install faster-whisper")

            project["status"] = "transcribing"
            store.save(project)

            def progress(value: float, message: str) -> None:
                job.progress = value
                job.message = message

            result = transcribe(src, options, on_progress=progress, should_cancel=lambda: job.cancel_requested)
            project = store.load(project_id)
            project.update(words=result["words"], language=result["language"], status="ready", error=None)
            store.save(project)
        except BaseException as exc:
            project = store.load(project_id)
            cancelled = job.cancel_requested
            if project.get("words"):
                project["status"] = "ready"
            else:
                project["status"] = "error"
                project["error"] = "Transcription cancelled." if cancelled else str(exc)
            store.save(project)
            raise
        return None

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        return project_payload(load_project(project_id))

    @app.patch("/api/projects/{project_id}")
    def update_project(project_id: str, body: dict = Body(...)):
        project = load_project(project_id)
        if "words" in body:
            project["words"] = [w.to_dict() for w in parse_words(body["words"] or [])]
        if "style" in body:
            project["style"] = Style.from_dict(body["style"]).to_dict()
        if "name" in body and str(body["name"]).strip():
            project["name"] = str(body["name"]).strip()[:120]
        if "preview" in body and isinstance(body["preview"], dict):
            project["preview"] = {
                "background": str(body["preview"].get("background") or "dark"),
                "template_id": body["preview"].get("template_id"),
            }
        store.save(project)
        return {"ok": True, "updated": project["updated"]}

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str):
        load_project(project_id)
        job = jobs.active_for(project_id, "transcribe")
        if job:
            jobs.cancel(job.id)
        store.delete(project_id)
        return {"ok": True}

    @app.post("/api/projects/{project_id}/transcribe")
    def retranscribe(project_id: str, body: dict = Body(default={})):
        project = load_project(project_id)
        if jobs.active_for(project_id, "transcribe"):
            raise HTTPException(409, "A transcription is already running for this project")
        options = TranscribeOptions.from_dict(body)
        project.update(status="transcribing", error=None, transcribe=options.__dict__, words=[] if body.get("replace") else project["words"])
        store.save(project)
        jobs.submit("transcribe", project_id, lambda job: _prepare_and_transcribe(job, project_id, options, None))
        return project_payload(store.load(project_id))

    @app.get("/api/projects/{project_id}/media")
    def get_media(project_id: str):
        project = load_project(project_id)
        playback = (project.get("media") or {}).get("playback")
        if not playback:
            raise HTTPException(404, "Media is still being prepared")
        return FileResponse(store.dir(project_id) / playback)

    @app.get("/api/projects/{project_id}/captions.ass")
    def download_ass(project_id: str):
        project = load_project(project_id)
        style, words = resolve_inputs(project, {})
        text = build_ass(words, style, title=project["name"])
        filename = safe_filename(project["name"], "captions") + ".ass"
        return Response(text, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/api/projects/{project_id}/words.json")
    def download_words(project_id: str):
        project = load_project(project_id)
        filename = safe_filename(project["name"], "words") + ".words.json"
        payload = {"language": project.get("language"), "words": project.get("words") or []}
        return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/projects/{project_id}/snapshot")
    def project_snapshot(project_id: str, body: dict = Body(...)):
        project = load_project(project_id)
        style, words = resolve_inputs(project, body)
        work = store.dir(project_id) / "tmp" / uuid.uuid4().hex[:8]
        work.mkdir(parents=True)
        try:
            ass_path = work / "captions.ass"
            write_ass_files(ass_path, words, style)
            png = media.snapshot(ass_path, fonts_dir(style), style, float(body.get("t") or 0), work)
        except media.FFmpegError as exc:
            raise HTTPException(500, str(exc))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/projects/{project_id}/render")
    def project_render(project_id: str, body: dict = Body(...)):
        project = load_project(project_id)
        if project.get("status") != "ready":
            raise HTTPException(409, "The transcript isn't ready yet")
        style, words = resolve_inputs(project, body)
        if not words:
            raise HTTPException(400, "There are no words to render")
        options = media.RenderOptions.from_dict(body.get("output"))
        template_path = templates.path(body.get("output", {}).get("template_id")) if options.background == "template" else None
        if options.format == "mp4" and options.background == "template" and not template_path:
            raise HTTPException(400, "Choose a template video first")
        if options.format == "mp4" and options.background == "media" and not project["media"].get("has_video"):
            raise HTTPException(400, "Your upload is audio only. Pick a template or a solid color background.")

        project["style"] = style.to_dict()
        project["words"] = [w.to_dict() for w in words]
        store.save(project)

        def run(job: Job) -> Path:
            work = store.dir(project_id) / "renders" / job.id
            work.mkdir(parents=True, exist_ok=True)
            job.message = "Writing subtitles…"
            ass_path = work / "captions.ass"
            write_ass_files(ass_path, words, style, title=project["name"])
            job.message = "Loading fonts…"
            fdir = fonts_dir(style)
            duration = float(project["media"].get("duration") or (words[-1].end + 1))
            job.message = "Rendering video…"

            def progress(value: float) -> None:
                job.progress = value
                job.message = f"Rendering video… {value * 100:.0f}%"

            out = media.render(ass_path, fdir, style, duration, options, work, media_path(project),
                               media.MediaInfo(**{k: project["media"].get(k) for k in ("duration", "has_video", "has_audio", "width", "height")}),
                               template_path, progress, lambda: job.cancel_requested)
            final = work / f"{safe_filename(project['name'], 'captions')}-captions.{options.extension}"
            out.replace(final)
            (work / "render.json").write_text(json.dumps({
                "created": time.time(), "file": final.name, "output": options.__dict__,
            }), "utf-8")
            job.message = "Done"
            return final

        job = jobs.submit("render", project_id, run)
        return job.to_dict()

    @app.get("/api/projects/{project_id}/renders")
    def list_renders(project_id: str):
        load_project(project_id)
        items = []
        for meta in (store.dir(project_id) / "renders").glob("*/render.json"):
            try:
                data = json.loads(meta.read_text("utf-8"))
            except Exception:
                continue
            file = meta.parent / data["file"]
            if file.exists():
                items.append({
                    "id": meta.parent.name,
                    "file": data["file"],
                    "created": data["created"],
                    "size": file.stat().st_size,
                    "format": data["output"].get("format"),
                    "url": f"/api/projects/{project_id}/renders/{meta.parent.name}",
                })
        items.sort(key=lambda r: r["created"], reverse=True)
        return items

    @app.get("/api/projects/{project_id}/renders/{render_id}")
    def download_render(project_id: str, render_id: str):
        load_project(project_id)
        folder = store.dir(project_id) / "renders" / safe_filename(render_id)
        meta = folder / "render.json"
        if not meta.exists():
            raise HTTPException(404, "Render not found")
        data = json.loads(meta.read_text("utf-8"))
        return FileResponse(folder / data["file"], filename=data["file"])

    @app.delete("/api/projects/{project_id}/renders/{render_id}")
    def delete_render(project_id: str, render_id: str):
        load_project(project_id)
        shutil.rmtree(store.dir(project_id) / "renders" / safe_filename(render_id), ignore_errors=True)
        return {"ok": True}

    # ---------- jobs ----------

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job.to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job = jobs.cancel(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job.to_dict()

    # ---------- fonts ----------

    @app.get("/api/fonts")
    def list_fonts():
        return {"ready": fonts.ready.is_set(), "families": fonts.list() if fonts.ready.is_set() else []}

    @app.post("/api/fonts")
    def upload_font(file: UploadFile = File(...)):
        try:
            faces = fonts.add_upload(file.filename or "font.ttf", file.file.read())
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"family": faces[0].family, "families": sorted({f.family for f in faces})}

    @app.get("/api/fonts/face/{face_id}")
    def font_face(face_id: str):
        path = fonts.face_path(face_id)
        if not path or not path.exists():
            raise HTTPException(404, "Font not found")
        return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})

    # ---------- templates ----------

    @app.get("/api/templates")
    def list_templates():
        return [{k: v for k, v in t.items() if k not in ("file", "preview")} for t in templates.list()]

    @app.post("/api/templates")
    def upload_template(file: UploadFile = File(...), name: str = Form("")):
        template_id = uuid.uuid4().hex[:12]
        ext = Path(safe_filename(file.filename or "", "template.mp4")).suffix.lower() or ".mp4"
        dest = templates.root / f"{template_id}{ext}"
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)
        try:
            info = media.probe(dest)
            if not info.has_video:
                raise media.FFmpegError("That file has no video track.")
            preview = media.make_browser_proxy(dest, info, templates.root, stem=f"{template_id}_preview")
        except media.FFmpegError as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, str(exc))
        item = templates.add({
            "id": template_id,
            "name": name.strip() or Path(file.filename or "Template").stem,
            "file": dest.name,
            "preview": preview.name if preview != dest else None,
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "builtin": False,
        })
        return {k: v for k, v in item.items() if k not in ("file", "preview")}

    @app.get("/api/templates/{template_id}/video")
    def template_video(template_id: str):
        path = templates.path(template_id, preview=True)
        if not path or not path.exists():
            raise HTTPException(404, "Template not found")
        return FileResponse(path)

    @app.delete("/api/templates/{template_id}")
    def delete_template(template_id: str):
        templates.remove(template_id)
        return {"ok": True}

    # ---------- presets ----------

    def read_presets(directory: Path, builtin: bool) -> list[dict]:
        items = []
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text("utf-8"))
            except Exception:
                continue
            items.append({
                "id": ("builtin:" if builtin else "user:") + path.stem,
                "name": data.get("name", path.stem),
                "description": data.get("description", ""),
                "style": data.get("style", {}),
                "builtin": builtin,
            })
        return items

    @app.get("/api/presets")
    def list_presets():
        return read_presets(PRESETS_DIR, True) + read_presets(user_presets_dir, False)

    @app.post("/api/presets")
    def save_preset(body: dict = Body(...)):
        name = str(body.get("name") or "").strip()[:60]
        if not name:
            raise HTTPException(400, "Give the preset a name")
        style = Style.from_dict(body.get("style")).to_dict()
        for key in ("width", "height", "fps"):
            style.pop(key, None)
        slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or uuid.uuid4().hex[:6]
        path = user_presets_dir / f"{slug}.json"
        path.write_text(json.dumps({"name": name, "description": body.get("description", ""), "style": style}, indent=2), "utf-8")
        return {"id": f"user:{slug}", "name": name, "style": style, "builtin": False}

    @app.delete("/api/presets/{preset_id}")
    def delete_preset(preset_id: str):
        if not preset_id.startswith("user:"):
            raise HTTPException(400, "Built-in presets can't be deleted")
        (user_presets_dir / f"{safe_filename(preset_id[5:])}.json").unlink(missing_ok=True)
        return {"ok": True}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
