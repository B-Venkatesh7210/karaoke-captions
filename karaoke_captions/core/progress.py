import json
import math
import threading
from pathlib import Path


class Steps:
    """Receives step-by-step progress. The base class ignores everything."""

    def begin(self, step_id: str, detail: str = "", *, expected: float | None = None, hint: str | None = None) -> None:
        pass

    def update(self, step_id: str, progress: float, detail: str | None = None, *,
               estimated: bool = False, heartbeat: bool = False) -> None:
        pass

    def done(self, step_id: str, detail: str | None = None) -> None:
        pass

    def skip(self, step_id: str, detail: str = "") -> None:
        pass


def estimate(elapsed: float, expected: float) -> float:
    """Progress for work that can't report its own: 90% at the expected time, then creeping toward 99%."""
    expected = max(expected, 0.5)
    if elapsed <= expected:
        return 0.9 * elapsed / expected
    return 0.9 + 0.09 * (1 - math.exp(-(elapsed - expected) / expected))


def fmt_bytes(n: float) -> str:
    if n < 1024:
        return f"{n:.0f} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.0f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def fmt_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class Timings:
    """Measured durations, remembered across runs so estimated progress gets closer to reality."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, float] = {}
        if path and path.exists():
            try:
                self._data = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                self._data = {}

    def get(self, key: str, default: float) -> float:
        return float(self._data.get(key, default))

    def record(self, key: str, value: float) -> None:
        with self._lock:
            old = self._data.get(key)
            self._data[key] = round(value if old is None else (old + value) / 2, 4)
            if not self.path:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self._data, indent=2), "utf-8")
            except OSError:
                pass
