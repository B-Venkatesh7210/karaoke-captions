import re

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def normalize_hex(value: str, fallback: str = "#FFFFFF") -> str:
    match = _HEX.match((value or "").strip())
    if not match:
        return fallback.upper()
    return "#" + match.group(1).upper()


def opacity_to_alpha(opacity: float) -> int:
    """ASS alpha is inverted: 0x00 is opaque, 0xFF is fully transparent."""
    opacity = max(0.0, min(100.0, float(opacity)))
    return round((100.0 - opacity) * 255 / 100)


def ass_color(hex_color: str, opacity: float = 100) -> str:
    """'#A855F7', 100 -> '&H00F755A8' (ASS stores colors as &HAABBGGRR)."""
    rgb = normalize_hex(hex_color)[1:]
    r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H{opacity_to_alpha(opacity):02X}{b}{g}{r}"


def ass_inline_color(hex_color: str) -> str:
    """Override-tag form used inside dialogue text, e.g. {\\c&HF755A8&}."""
    rgb = normalize_hex(hex_color)[1:]
    return f"&H{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}&"
