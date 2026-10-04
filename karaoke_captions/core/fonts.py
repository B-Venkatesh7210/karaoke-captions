"""Discover installed and uploaded fonts.

Families are keyed by the legacy family name (name ID 1) because that is what
libass matches against the ASS `Fontname` field.
"""

import hashlib
import json
import os
import shutil
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

FONT_EXTS = {".ttf", ".otf", ".ttc", ".otc"}


@dataclass
class FontFace:
    id: str
    path: str
    index: int
    family: str
    subfamily: str
    weight: int
    italic: bool
    scale: float
    source: str


@dataclass
class FontFamily:
    name: str
    source: str
    faces: list[FontFace] = field(default_factory=list)

    @property
    def scale(self) -> float:
        regular = [f for f in self.faces if not f.italic] or self.faces
        regular.sort(key=lambda f: abs(f.weight - 400))
        return regular[0].scale


def system_font_dirs() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return [windir / "Fonts", local / "Microsoft" / "Windows" / "Fonts"]
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".fonts", home / ".local" / "share" / "fonts"]


def _name(font, name_id: int) -> str | None:
    table = font["name"]
    for platform, encoding, lang in ((3, 1, 0x409), (1, 0, 0)):
        record = table.getName(name_id, platform, encoding, lang)
        if record:
            try:
                return record.toUnicode().strip()
            except UnicodeDecodeError:
                pass
    record = table.getDebugName(name_id)
    return record.strip() if record else None


def _read_faces(path: Path, source: str) -> list[FontFace]:
    from fontTools.ttLib import TTCollection, TTFont

    faces = []
    try:
        if path.suffix.lower() in (".ttc", ".otc"):
            fonts = list(TTCollection(str(path), lazy=True).fonts)
        else:
            fonts = [TTFont(str(path), lazy=True, fontNumber=-1)]
    except Exception:
        return faces

    for index, font in enumerate(fonts):
        try:
            family = _name(font, 1)
            if not family:
                continue
            subfamily = _name(font, 2) or "Regular"
            weight, italic, scale = 400, "italic" in subfamily.lower() or "oblique" in subfamily.lower(), 0.8
            upm = font["head"].unitsPerEm if "head" in font else 1000
            if "OS/2" in font:
                os2 = font["OS/2"]
                weight = int(os2.usWeightClass or 400)
                italic = italic or bool(os2.fsSelection & 1)
                height = (os2.usWinAscent or 0) + (os2.usWinDescent or 0)
                if height > 0:
                    scale = upm / height
            face_id = hashlib.sha1(f"{path}|{index}".encode()).hexdigest()[:16]
            faces.append(
                FontFace(
                    id=face_id,
                    path=str(path),
                    index=index,
                    family=family,
                    subfamily=subfamily,
                    weight=weight,
                    italic=italic,
                    scale=round(scale, 4),
                    source=source,
                )
            )
        except Exception:
            continue
    return faces


class FontRegistry:
    def __init__(self, upload_dir: Path, cache_file: Path, fontdirs_root: Path):
        self.upload_dir = upload_dir
        self.cache_file = cache_file
        self.fontdirs_root = fontdirs_root
        self.families: dict[str, FontFamily] = {}
        self.faces: dict[str, FontFace] = {}
        self.ready = threading.Event()
        self._lock = threading.Lock()

    def scan_async(self) -> None:
        threading.Thread(target=self.scan, daemon=True).start()

    def scan(self) -> None:
        cache = {}
        if self.cache_file.exists():
            try:
                cache = json.loads(self.cache_file.read_text("utf-8"))
            except Exception:
                cache = {}

        new_cache = {}
        all_faces: list[FontFace] = []
        sources = [(d, "system") for d in system_font_dirs()] + [(self.upload_dir, "uploaded")]
        for directory, source in sources:
            if not directory.exists():
                continue
            for path in directory.rglob("*"):
                if path.suffix.lower() not in FONT_EXTS or not path.is_file():
                    continue
                key = str(path)
                stamp = f"{path.stat().st_mtime_ns}:{path.stat().st_size}"
                cached = cache.get(key)
                if cached and cached.get("stamp") == stamp:
                    faces = [FontFace(**f) for f in cached["faces"]]
                else:
                    faces = _read_faces(path, source)
                new_cache[key] = {"stamp": stamp, "faces": [asdict(f) for f in faces]}
                all_faces.extend(faces)

        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(new_cache), "utf-8")
        except OSError:
            pass

        with self._lock:
            self.families = {}
            self.faces = {}
            for face in all_faces:
                self._add(face)
        self.ready.set()

    def _add(self, face: FontFace) -> None:
        self.faces[face.id] = face
        key = face.family.lower()
        family = self.families.get(key)
        if family is None:
            family = self.families[key] = FontFamily(name=face.family, source=face.source)
        if face.source == "uploaded":
            family.source = "uploaded"
        if not any(f.path == face.path and f.index == face.index for f in family.faces):
            family.faces.append(face)

    def add_upload(self, filename: str, data: bytes) -> list[FontFace]:
        safe = "".join(c for c in Path(filename).name if c.isalnum() or c in "._- ").strip() or "font.ttf"
        if Path(safe).suffix.lower() not in FONT_EXTS:
            raise ValueError("Unsupported font type. Use .ttf, .otf or .ttc")
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        dest = self.upload_dir / safe
        dest.write_bytes(data)
        faces = _read_faces(dest, "uploaded")
        if not faces:
            dest.unlink(missing_ok=True)
            raise ValueError("Could not read that font file")
        with self._lock:
            for face in faces:
                self._add(face)
            for family in {f.family.lower() for f in faces}:
                shutil.rmtree(self.fontdirs_root / _slug(family), ignore_errors=True)
        return faces

    def list(self) -> list[dict]:
        with self._lock:
            families = sorted(self.families.values(), key=lambda f: (f.source != "uploaded", f.name.lower()))
            return [
                {
                    "name": fam.name,
                    "source": fam.source,
                    "scale": fam.scale,
                    "faces": [
                        {
                            "id": f.id,
                            "weight": f.weight,
                            "italic": f.italic,
                            "subfamily": f.subfamily,
                            "collection": Path(f.path).suffix.lower() in (".ttc", ".otc"),
                        }
                        for f in fam.faces
                    ],
                }
                for fam in families
            ]

    def face_path(self, face_id: str) -> Path | None:
        face = self.faces.get(face_id)
        return Path(face.path) if face else None

    def fonts_dir_for(self, family_name: str) -> Path | None:
        """A folder holding only this family's files, passed to ffmpeg as `fontsdir`.

        Copying the files guarantees libass finds the exact font the preview used,
        even when the system font provider can't see per-user installed fonts.
        """
        family = self.families.get(family_name.strip().lower())
        if not family:
            return None
        target = self.fontdirs_root / _slug(family.name)
        if not target.exists():
            tmp = target.with_name(target.name + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            for path in {f.path for f in family.faces}:
                try:
                    shutil.copy2(path, tmp / Path(path).name)
                except OSError:
                    continue
            tmp.rename(target)
        return target


def _slug(name: str) -> str:
    cleaned = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    return f"{cleaned[:40]}-{hashlib.sha1(name.lower().encode()).hexdigest()[:6]}"
