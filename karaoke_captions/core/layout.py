"""Group timed words into caption lines.

The same rules are mirrored in static/js/layout.js for the live preview;
keep the two in sync.
"""

from dataclasses import dataclass

from .style import Style

SENTENCE_END = (".", "?", "!", "…")
TRAILING_QUOTES = "\"'”’)]"


@dataclass
class Word:
    text: str
    start: float
    end: float
    break_after: bool = False
    prob: float | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Word":
        text = str(data.get("text", data.get("word", ""))).strip()
        start = float(data.get("start", 0))
        end = float(data.get("end", start))
        prob = data.get("prob")
        return cls(
            text=text,
            start=start,
            end=max(start, end),
            break_after=bool(data.get("break_after", False)),
            prob=float(prob) if prob is not None else None,
        )

    def to_dict(self) -> dict:
        data = {"text": self.text, "start": round(self.start, 3), "end": round(self.end, 3)}
        if self.break_after:
            data["break_after"] = True
        if self.prob is not None:
            data["prob"] = round(self.prob, 3)
        return data


@dataclass
class Line:
    words: list[Word]
    start: float
    end: float


def parse_words(raw: list[dict]) -> list[Word]:
    words = [Word.from_dict(w) for w in raw]
    words = [w for w in words if w.text]
    words.sort(key=lambda w: w.start)
    return words


def ends_sentence(text: str) -> bool:
    return text.rstrip(TRAILING_QUOTES).endswith(SENTENCE_END)


def build_lines(words: list[Word], style: Style) -> list[Line]:
    groups: list[list[Word]] = []
    current: list[Word] = []

    def line_chars(ws: list[Word]) -> int:
        return len(" ".join(w.text for w in ws))

    for i, word in enumerate(words):
        if current and style.max_chars > 0 and line_chars(current) + 1 + len(word.text) > style.max_chars:
            groups.append(current)
            current = []
        current.append(word)

        nxt = words[i + 1] if i + 1 < len(words) else None
        should_break = (
            len(current) >= style.max_words
            or word.break_after
            or (style.sentence_break and ends_sentence(word.text))
            or (nxt is not None and style.pause_break > 0 and nxt.start - word.end >= style.pause_break)
        )
        if should_break:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    lines: list[Line] = []
    for idx, group in enumerate(groups):
        end = group[-1].end
        if style.line_hold > 0:
            next_start = groups[idx + 1][0].start if idx + 1 < len(groups) else None
            end += style.line_hold
            if next_start is not None:
                end = min(end, next_start)
        lines.append(Line(words=group, start=group[0].start, end=max(end, group[-1].end)))
    return lines
