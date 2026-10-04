import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from .core.style import Style

_ID = re.compile(r"^[a-f0-9]{12}$")


def safe_filename(name: str, fallback: str = "file") -> str:
    name = Path(name or "").name
    cleaned = "".join(c for c in name if c.isalnum() or c in "._- ").strip(" .")
    return cleaned or fallback


class ProjectStore:
    """Each project is a folder in the workspace holding project.json, the media and renders."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def dir(self, project_id: str) -> Path:
        if not _ID.match(project_id or ""):
            raise KeyError(project_id)
        return self.root / project_id

    def create(self, name: str) -> dict:
        project_id = uuid.uuid4().hex[:12]
        folder = self.root / project_id
        (folder / "renders").mkdir(parents=True)
        project = {
            "id": project_id,
            "name": name,
            "created": time.time(),
            "updated": time.time(),
            "status": "uploading",
            "error": None,
            "media": None,
            "language": None,
            "transcribe": None,
            "words": [],
            "style": Style().to_dict(),
            "preview": {"background": "template", "template_id": "aurora"},
        }
        self.save(project)
        return project

    def load(self, project_id: str) -> dict:
        path = self.dir(project_id) / "project.json"
        if not path.exists():
            raise KeyError(project_id)
        return json.loads(path.read_text("utf-8"))

    def save(self, project: dict) -> None:
        project["updated"] = time.time()
        folder = self.dir(project["id"])
        tmp = folder / "project.json.tmp"
        with self._lock:
            tmp.write_text(json.dumps(project, ensure_ascii=False), "utf-8")
            tmp.replace(folder / "project.json")

    def update(self, project_id: str, **changes) -> dict:
        project = self.load(project_id)
        project.update(changes)
        self.save(project)
        return project

    def delete(self, project_id: str) -> None:
        shutil.rmtree(self.dir(project_id), ignore_errors=True)

    def list(self) -> list[dict]:
        items = []
        for path in self.root.glob("*/project.json"):
            try:
                project = json.loads(path.read_text("utf-8"))
            except Exception:
                continue
            items.append(
                {
                    "id": project["id"],
                    "name": project.get("name"),
                    "status": project.get("status"),
                    "updated": project.get("updated"),
                    "duration": (project.get("media") or {}).get("duration"),
                    "words": len(project.get("words") or []),
                }
            )
        items.sort(key=lambda p: p.get("updated") or 0, reverse=True)
        return items


class TemplateStore:
    """Reusable background videos for previewing and burning in captions."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = root / "templates.json"
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        try:
            return json.loads(self.index_path.read_text("utf-8"))
        except Exception:
            return []

    def _write(self, items: list[dict]) -> None:
        self.index_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")

    def list(self) -> list[dict]:
        return [t for t in self._read() if (self.root / t["file"]).exists()]

    def get(self, template_id: str) -> dict | None:
        return next((t for t in self._read() if t["id"] == template_id), None)

    def path(self, template_id: str, preview: bool = False) -> Path | None:
        item = self.get(template_id or "")
        if not item:
            return None
        return self.root / (item.get("preview") or item["file"] if preview else item["file"])

    def add(self, item: dict) -> dict:
        with self._lock:
            items = self._read()
            items.append(item)
            self._write(items)
        return item

    def remove(self, template_id: str) -> None:
        with self._lock:
            items = self._read()
            keep = []
            for item in items:
                if item["id"] == template_id and not item.get("builtin"):
                    for key in ("file", "preview"):
                        if item.get(key):
                            (self.root / item[key]).unlink(missing_ok=True)
                else:
                    keep.append(item)
            self._write(keep)
