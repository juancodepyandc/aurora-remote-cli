"""Colour maths and terminal-depth adaptation.

Themes are authored once, in 24-bit hex. This module decides how much of that
survives on the terminal the user actually has:

* truecolor / 256 colour  -> pass hex straight through
* 16 colour               -> quantise to the nearest ANSI colour
* no colour               -> degrade to bold/dim only

Because of that, no theme file ever needs to know what a terminal supports,
and no render call site needs a special case.
"""
from __future__ import annotations

from typing import Iterable, Sequence

HexColor = str

#: xterm 16-colour palette, ordered 0-15.
ANSI_16: tuple[HexColor, ...] = (
    "#000000", "#800000", "#008000", "#808000",
    "#000080", "#800080", "#008080", "#c0c0c0",
    "#808080", "#ff0000", "#00ff00", "#ffff00",
    "#0000ff", "#ff00ff", "#00ffff", "#ffffff",
)

#: Friendly aliases so theme authors can say what they mean.
NAMED: dict[str, HexColor] = {
    "black": "#000000", "red": "#ff4040", "green": "#3ddc84", "yellow": "#ffd166",
    "blue": "#4cc9f0", "magenta": "#c77dff", "cyan": "#4dd0e1", "white": "#e8e8e8",
    "orange": "#ff9f1c", "pink": "#ff6b9d", "teal": "#2ec4b6", "lime": "#a7f432",
    "amber": "#ffb703", "brown": "#b08968", "navy": "#1b2a4a", "slate": "#4a5568",
}


def normalize(value: str | int) -> HexColor:
    """Accept a name, a hex string, or a packed 24-bit int; return ``#rrggbb``."""
    if isinstance(value, int):
        # Palette slots are allowed to hold 0xRRGGBB directly.
        if not 0 <= value <= 0xFFFFFF:
            raise ValueError(f"Unsupported colour: {value!r}")
        return f"#{value:06x}"
    key = str(value).strip().lower()
    if key in NAMED:
        key = NAMED[key]
    if not key.startswith("#"):
        if len(key) in (3, 4):
            key = "#" + "".join(ch * 2 for ch in key)
        else:
            key = "#" + key
    if len(key) == 4:  # #rgb -> #rrggbb
        key = "#" + "".join(ch * 2 for ch in key[1:])
    if len(key) != 7:
        raise ValueError(f"Unsupported colour: {value!r}")
    return key


def to_rgb(value: str) -> tuple[int, int, int]:
    hexed = normalize(value)
    return int(hexed[1:3], 16), int(hexed[3:5], 16), int(hexed[5:7], 16)


def from_rgb(rgb: Sequence[int]) -> HexColor:
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def blend(first: str, second: str, ratio: float) -> HexColor:
    """Linear interpolation between two colours. ``ratio`` 0 -> first."""
    a, b = to_rgb(first), to_rgb(second)
    t = max(0.0, min(1.0, ratio))
    return from_rgb([a[i] + (b[i] - a[i]) * t for i in range(3)])


def shade(value: str, factor: float) -> HexColor:
    """Darken (factor < 1) or lighten (factor > 1) a colour."""
    rgb = to_rgb(value)
    if factor <= 1.0:
        return from_rgb([c * factor for c in rgb])
    return blend(value, "#ffffff", min(1.0, factor - 1.0))


def luminance(value: str) -> float:
    r, g, b = to_rgb(value)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def readable_on(foreground: str, background: str) -> str:
    """Pick whichever of black/white reads better on ``background``."""
    return "#000000" if luminance(background) > 0.55 else "#ffffff"


# --- Terminal depth adaptation -------------------------------------------

#: Standard 6x6x6 cube used by 256-colour terminals.
_CUBE = (0, 95, 135, 175, 215, 255)


def to_ansi256(value: str) -> str:
    """Quantise a colour to the xterm 256-colour cube."""
    r, g, b = to_rgb(value)
    if abs(r - g) < 12 and abs(g - b) < 12 and abs(r - b) < 12:
        grey = round((r + g + b) / 3)
        if grey < 8:
            return "color(16)"
        if grey > 248:
            return "color(231)"
        return f"color({232 + round((grey - 8) / 247 * 24)})"
    quant = tuple(min(5, max(0, round(c / 51))) for c in (r, g, b))
    index = 16 + 36 * quant[0] + 6 * quant[1] + quant[2]
    return f"color({index})"


def to_ansi16(value: str) -> str:
    """Quantise a colour to the basic 16 ANSI colours."""
    r, g, b = to_rgb(value)
    best_index, best_distance = 0, None
    for index, candidate in enumerate(ANSI_16):
        cr, cg, cb = to_rgb(candidate)
        # Weighted RGB distance keeps skin tones and greys closer to truth.
        distance = 2 * (r - cr) ** 2 + 4 * (g - cg) ** 2 + 3 * (b - cb) ** 2
        if best_distance is None or distance < best_distance:
            best_index, best_distance = index, distance
    return f"ansi({best_index})"


def adapt(value: str, depth: str) -> str:
    """Convert a hex colour into the richest form ``depth`` can render."""
    if depth == "truecolor":
        return normalize(value)
    if depth == "color256":
        return to_ansi256(value)
    if depth in ("ansi", "color8"):
        return to_ansi16(value)
    return "default"


# --- Rich / prompt_toolkit style strings ---------------------------------


def rich_style(value: str, depth: str, *, bold: bool = False, dim: bool = False,
               italic: bool = False, underline: bool = False) -> str:
    """Build a Rich markup style string adapted to the terminal depth."""
    parts = []
    if bold:
        parts.append("bold")
    if dim:
        parts.append("dim")
    if italic:
        parts.append("italic")
    if underline:
        parts.append("underline")
    if depth != "none":
        parts.append(adapt(value, depth))
    return " ".join(parts) if parts else "none"


def to_prompt_toolkit(value: str, depth: str) -> str:
    """Build a prompt_toolkit style string, which only understands hex."""
    if depth == "truecolor":
        return normalize(value)
    # prompt_toolkit cannot express colour(216) or ansi(5); fall back to hex,
    # which prompt_toolkit itself downsamples when the terminal is smaller.
    return normalize(value)


# --- Gradients ------------------------------------------------------------


def gradient(stops: Sequence[str], steps: int) -> list[HexColor]:
    """Build a smooth ramp across ``steps`` from an ordered list of stops."""
    if steps <= 0:
        return []
    if not stops:
        return []
    if len(stops) == 1 or steps == 1:
        return [normalize(stops[0])] * steps
    segments = len(stops) - 1
    ramp: list[HexColor] = []
    for index in range(steps):
        position = index / max(1, steps - 1) * segments
        low = min(segments, int(position))
        high = min(segments, low + 1)
        ramp.append(blend(stops[low], stops[high], position - low))
    return ramp


def rainbow(steps: int) -> list[HexColor]:
    """A stable 6-stop hue ramp, for spinners and progress bars."""
    return gradient(["#ff5f6d", "#ffc371", "#7ee787", "#63b3ff", "#b18cff", "#ff5f6d"], steps)


def strip_ansi(text: str) -> str:
    """Remove escape sequences, for width maths and plain-ASCII fallbacks."""
    out, index = [], 0
    while index < len(text):
        char = text[index]
        if char == "\x1b":
            index += 1
            if index < len(text) and text[index] == "[":
                index += 1
                while index < len(text) and not text[index].isalpha():
                    index += 1
                index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def apply_ramp(text: str, ramp: Iterable[str], depth: str) -> str:
    """Colour each character of ``text`` along ``ramp``, for streamed output."""
    if depth == "none":
        return text
    colors = list(ramp)
    if not colors:
        return text
    out = []
    for index, char in enumerate(text):
        if char in ("\n", "\r"):
            out.append(char)
            continue
        picked = colors[index % len(colors)]
        red, green, blue = to_rgb(picked)
        if depth == "truecolor":
            out.append(f"\x1b[38;2;{red};{green};{blue}m{char}\x1b[0m")
        elif depth == "color256":
            out.append(f"\x1b[38;5;{to_ansi256(picked)[6:-1]}m{char}\x1b[0m")
        else:
            out.append(f"\x1b[{to_ansi16(picked)[5:-1]}m{char}\x1b[0m")
    return "".join(out)
