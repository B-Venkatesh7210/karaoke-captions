from dataclasses import asdict, dataclass, fields

from .colors import normalize_hex

BACKGROUNDS = ("box", "outline", "none")
HIGHLIGHT_MODES = ("word", "progressive", "fill")
POSITIONS = ("bottom", "center", "top")


@dataclass
class Style:
    # Canvas
    width: int = 1920
    height: int = 1080
    fps: int = 30

    # Text
    font_family: str = "Inter"
    font_size: int = 64
    bold: bool = True
    italic: bool = False
    uppercase: bool = False
    letter_spacing: float = 0

    # Colors
    text_color: str = "#FFFFFF"
    text_opacity: float = 100
    highlight_color: str = "#A855F7"
    highlight_mode: str = "word"
    active_scale: int = 100

    # Background
    background: str = "box"
    box_color: str = "#111111"
    box_opacity: float = 50
    padding_x: int = 9
    padding_y: int = 9
    outline_color: str = "#000000"
    outline_width: float = 3
    shadow_color: str = "#000000"
    shadow_opacity: float = 50
    shadow_depth: float = 0

    # Layout
    position: str = "bottom"
    margin_v: int = 72
    margin_h: int = 80
    max_words: int = 6
    max_chars: int = 0
    pause_break: float = 0.5
    sentence_break: bool = True
    line_hold: float = 0.0

    @classmethod
    def from_dict(cls, data: dict | None) -> "Style":
        data = data or {}
        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for name, field in known.items():
            if name not in data or data[name] is None:
                continue
            value = data[name]
            try:
                if field.type in ("int", int):
                    value = int(round(float(value)))
                elif field.type in ("float", float):
                    value = float(value)
                elif field.type in ("bool", bool):
                    value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
                else:
                    value = str(value)
            except (TypeError, ValueError):
                continue
            kwargs[name] = value
        style = cls(**kwargs)
        style._sanitize()
        return style

    def _sanitize(self) -> None:
        self.width = max(16, self.width - self.width % 2)
        self.height = max(16, self.height - self.height % 2)
        self.fps = max(1, min(120, self.fps))
        self.font_size = max(4, self.font_size)
        self.font_family = self.font_family.strip() or "Arial"
        for name in ("text_color", "highlight_color", "box_color", "outline_color", "shadow_color"):
            setattr(self, name, normalize_hex(getattr(self, name)))
        if self.background not in BACKGROUNDS:
            self.background = "box"
        if self.highlight_mode not in HIGHLIGHT_MODES:
            self.highlight_mode = "word"
        if self.position not in POSITIONS:
            self.position = "bottom"
        self.max_words = max(1, self.max_words)
        self.max_chars = max(0, self.max_chars)
        self.active_scale = max(50, min(200, self.active_scale))
        self.padding_x = max(0, self.padding_x)
        self.padding_y = max(0, self.padding_y)
        self.pause_break = max(0.0, self.pause_break)
        self.line_hold = max(0.0, self.line_hold)

    def to_dict(self) -> dict:
        return asdict(self)
