from dataclasses import replace

from .colors import ass_color, ass_inline_color, opacity_to_alpha
from .layout import Line, Word, build_lines
from .style import Style

ALIGNMENT = {"bottom": 2, "center": 5, "top": 8}

# libass draws one opaque box per run of identically styled text, so a line
# whose words change color gets overlapping boxes (visible as darker seams when
# the box is translucent). The box is therefore drawn on its own layer from a
# single uniform run, with the colored words layered on top.
BOX_LAYER, TEXT_LAYER = 0, 1


def ass_time(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape_text(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def _num(value: float) -> str:
    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _style_row(name: str, style: Style, primary: str, secondary: str, outline_colour: str,
               back_colour: str, border_style: int, outline: float, shadow: float) -> str:
    values = [
        name,
        style.font_family.replace(",", " "),
        style.font_size,
        primary,
        secondary,
        outline_colour,
        back_colour,
        -1 if style.bold else 0,
        -1 if style.italic else 0,
        0,
        0,
        100,
        100,
        _num(style.letter_spacing),
        0,
        border_style,
        _num(outline),
        _num(shadow),
        ALIGNMENT[style.position],
        style.margin_h,
        style.margin_h,
        style.margin_v,
        1,
    ]
    return "Style: " + ",".join(str(v) for v in values)


def _style_rows(style: Style) -> list[str]:
    if style.highlight_mode == "fill":
        primary = ass_color(style.highlight_color)
        secondary = ass_color(style.text_color, style.text_opacity)
    else:
        primary = ass_color(style.text_color, style.text_opacity)
        secondary = ass_color(style.highlight_color)
    shadow_colour = ass_color(style.shadow_color, style.shadow_opacity)
    outline_colour = ass_color(style.outline_color)

    if style.background == "box":
        box_colour = ass_color(style.box_color, style.box_opacity)
        return [
            _style_row("Text", style, primary, secondary, outline_colour, shadow_colour, 1, 0, 0),
            _style_row("Box", style, ass_color("#000000", 0), ass_color("#000000", 0), box_colour, shadow_colour,
                       3, max(style.padding_x, style.padding_y), style.shadow_depth),
        ]
    outline = style.outline_width if style.background == "outline" else 0
    return [_style_row("Text", style, primary, secondary, outline_colour, shadow_colour, 1, outline, style.shadow_depth)]


def _display(word: Word, style: Style) -> str:
    text = escape_text(word.text)
    return text.upper() if style.uppercase else text


def _scaled(text: str, scale: int) -> str:
    return f"{{\\fscx{scale}\\fscy{scale}}}{text}{{\\fscx100\\fscy100}}"


def _box_text(line: Line, style: Style, active: int | None = None) -> str:
    parts = []
    for k, word in enumerate(line.words):
        text = _display(word, style)
        parts.append(_scaled(text, style.active_scale) if k == active else text)
    return f"{{\\xbord{style.padding_x}\\ybord{style.padding_y}\\1a&HFF&}}" + " ".join(parts)


def _word_events(line: Line, style: Style) -> list[tuple[int, str, float, float, str]]:
    events = []
    text_c = ass_inline_color(style.text_color)
    hl_c = ass_inline_color(style.highlight_color)
    scale = style.active_scale
    dim = style.text_opacity < 100
    dim_alpha = f"\\1a&H{opacity_to_alpha(style.text_opacity):02X}&"
    box = style.background == "box"

    if box and scale == 100:
        events.append((BOX_LAYER, "Box", line.start, line.end, _box_text(line, style)))

    for j, current in enumerate(line.words):
        start = current.start
        end = line.words[j + 1].start if j + 1 < len(line.words) else line.end
        if end - start < 0.01:
            continue
        parts = []
        for k, word in enumerate(line.words):
            active = k == j
            lit = active or (style.highlight_mode == "progressive" and k < j)
            tag = f"\\c{hl_c if lit else text_c}"
            if dim:
                tag += "\\1a&H00&" if lit else dim_alpha
            text = _display(word, style)
            if active and scale != 100:
                parts.append(f"{{{tag}}}" + _scaled(text, scale))
            else:
                parts.append(f"{{{tag}}}{text}")
        events.append((TEXT_LAYER, "Text", start, end, " ".join(parts)))
        if box and scale != 100:
            events.append((BOX_LAYER, "Box", start, end, _box_text(line, style, active=j)))
    return events


def _fill_events(line: Line, style: Style) -> list[tuple[int, str, float, float, str]]:
    parts = []
    for j, word in enumerate(line.words):
        nxt = line.words[j + 1].start if j + 1 < len(line.words) else word.end
        duration = max(0, round(nxt * 100) - round(word.start * 100))
        parts.append(f"{{\\kf{duration}}}{_display(word, style)}")
    events = [(TEXT_LAYER, "Text", line.start, line.end, " ".join(parts))]
    if style.background == "box":
        events.append((BOX_LAYER, "Box", line.start, line.end, _box_text(line, style)))
    return events


def write_ass_files(path, words: list[Word], style: Style, title: str = "Karaoke Captions") -> None:
    """Write the captions and the matching coverage matte used for transparent renders."""
    from .media import matte_path

    path.write_text(build_ass(words, style, title=title), "utf-8")
    matte_path(path).write_text(build_matte_ass(words, style), "utf-8")


def build_matte_ass(words: list[Word], style: Style) -> str:
    """Same captions with every color white: rendered on black, luma equals coverage (alpha)."""
    white = {k: "#FFFFFF" for k in ("text_color", "highlight_color", "box_color", "outline_color", "shadow_color")}
    return build_ass(words, replace(style, **white), title="matte")


def build_ass(words: list[Word], style: Style, title: str = "Karaoke Captions") -> str:
    lines = build_lines(words, style)
    out = [
        "[Script Info]",
        f"Title: {title}",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {style.width}",
        f"PlayResY: {style.height}",
        "YCbCr Matrix: None",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *_style_rows(style),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for line in lines:
        events = _fill_events(line, style) if style.highlight_mode == "fill" else _word_events(line, style)
        for layer, name, start, end, text in events:
            out.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{name},,0,0,0,,{text}")
    return "\n".join(out) + "\n"
