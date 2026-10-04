"""Runtime capability detection.

The CLI must adapt instead of assuming. This module answers, once, the
questions the renderer needs on any OS and in any terminal:

* can we emit colour, and how much of it?
* can we draw with Unicode block characters, or must we fall back to ASCII?
* is there a real terminal attached, or are we writing to a pipe/file?
* how much time is the user willing to pay for an animation?

Everything is overridable from the environment so a user can force a mode
without editing code, and so tests can pin the behaviour.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Literal

ColorDepth = Literal["none", "ansi", "color8", "color256", "truecolor"]
AnimLevel = Literal["none", "static", "reduced", "full"]

_TERM_COLOR_HINTS = {
    "truecolor": ("truecolor", "24bit", "direct"),
    "color256": ("256color",),
    "color8": ("88color",),
}


def _env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _supports_unicode() -> bool:
    """Detect a terminal able to render box drawing and block glyphs."""
    # An explicit override wins, so a user on a UTF-8 terminal can force the
    # ASCII artwork and a user on a legacy one can force the pretty one.
    forced = _env_flag("JOBIA_UNICODE")
    if forced is not None:
        return forced
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    if "utf" in encoding:
        return True
    for var in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = os.environ.get(var, "")
        if "utf" in value.lower():
            return True
    return False


def _detect_color_depth(stream) -> ColorDepth:
    forced = os.environ.get("JOBIA_COLOR")
    if forced == "never":
        return "none"
    if forced == "always":
        return "truecolor"
    if forced in ("none", "ansi", "color8", "color256", "truecolor"):
        return forced  # type: ignore[return-value]
    if "NO_COLOR" in os.environ:
        return "none"
    if os.environ.get("TERM", "") == "dumb":
        return "none"
    if not hasattr(stream, "isatty") or not stream.isatty():
        return "none"
    if _env_flag("FORCE_COLOR") or os.environ.get("FORCE_COLOR"):
        return "truecolor"
    term = os.environ.get("TERM", "").lower()
    colorterm = os.environ.get("COLORTERM", "").lower()
    if "truecolor" in colorterm or "24bit" in colorterm:
        return "truecolor"
    for depth, hints in _TERM_COLOR_HINTS.items():
        if any(hint in term for hint in hints):
            return depth  # type: ignore[return-value]
    if "color" in term:
        return "color8"
    if os.environ.get("TERM"):
        return "ansi"
    return "none"


def _detect_animation(color_depth: ColorDepth, is_tty: bool, slow: bool) -> AnimLevel:
    """Map terminal capabilities onto an animation budget level."""
    forced = os.environ.get("JOBIA_ANIM")
    if forced in ("none", "static", "reduced", "full"):
        return forced  # type: ignore[return-value]
    if not is_tty:
        return "none"
    if os.environ.get("TERM", "") == "dumb":
        return "static"
    if color_depth == "none" and _env_flag("NO_COLOR"):
        return "static"
    if slow:
        return "reduced"
    return "full"


@dataclass(frozen=True)
class Capabilities:
    """What the current terminal can actually do."""

    os_name: str
    is_tty: bool
    color_depth: ColorDepth
    unicode: bool
    animation: AnimLevel
    width: int
    height: int
    ansi_styles: bool
    slow: bool
    env_overrides: dict[str, str] = field(default_factory=dict)

    # --- Derived helpers -------------------------------------------------

    @property
    def color(self) -> bool:
        return self.color_depth != "none"

    @property
    def rich_palette(self) -> bool:
        """True when we can rely on 256/24-bit colour for gradients."""
        return self.color_depth in ("color256", "truecolor")

    @property
    def animate(self) -> bool:
        return self.animation in ("reduced", "full")

    @property
    def fancy_frames(self) -> bool:
        return self.animation == "full"

    @property
    def box_glyphs(self) -> bool:
        return self.unicode

    def glyph(self, fancy: str, plain: str) -> str:
        """Return a glyph pair, picking the one this terminal can draw."""
        return fancy if self.unicode else plain

    def clamp_width(self, *, minimum: int = 20, maximum: int = 120) -> int:
        return max(minimum, min(self.width, maximum))

    def frame_interval(self) -> float:
        """Seconds between animation frames, tuned to the animation level."""
        override = _env_int("JOBIA_FPS")
        if override and override > 0:
            return 1.0 / min(override, 60)
        if self.animation == "full":
            return 1.0 / 24.0
        if self.animation == "reduced":
            return 1.0 / 8.0
        return 1.0 / 4.0


def detect(stream=None, *, force_animation: AnimLevel | None = None) -> Capabilities:
    """Inspect the environment and report what is possible."""
    stream = stream if stream is not None else sys.stdout
    is_tty = bool(getattr(stream, "isatty", lambda: False)())
    color_depth = _detect_color_depth(stream)
    unicode_ok = _supports_unicode()
    slow = bool(_env_flag("JOBIA_SLOW")) or _is_ci()
    animation = force_animation or _detect_animation(color_depth, is_tty, slow)
    if force_animation is not None:
        animation = force_animation
    width, height = _terminal_size()
    if width <= 0:
        width = 80
    if height <= 0:
        height = 24
    env_overrides = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("JOBIA_")
    }
    return Capabilities(
        os_name=_os_name(),
        is_tty=is_tty,
        color_depth=color_depth,
        unicode=unicode_ok,
        animation=animation,
        width=width,
        height=height,
        ansi_styles=color_depth != "none",
        slow=slow,
        env_overrides=env_overrides,
    )


def _os_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith("freebsd"):
        return "freebsd"
    return sys.platform


def _is_ci() -> bool:
    return bool(os.environ.get("CI")) or bool(os.environ.get("GITHUB_ACTIONS"))


def _terminal_size() -> tuple[int, int]:
    try:
        size = shutil.get_terminal_size(fallback=(80, 24))
        return size.columns, size.lines
    except (OSError, ValueError):
        return 80, 24


def os_label() -> str:
    """Human readable OS name for status output."""
    names = {
        "macos": "macOS",
        "windows": "Windows",
        "linux": "Linux",
        "freebsd": "FreeBSD",
    }
    raw = _os_name()
    return names.get(raw, raw)


# --- Cached singleton -----------------------------------------------------

_cached: Capabilities | None = None


def current(refresh: bool = False, *, force_animation: AnimLevel | None = None) -> Capabilities:
    """Return cached capabilities for this process."""
    global _cached
    if _cached is None or refresh or force_animation is not None:
        _cached = detect(force_animation=force_animation)
    return _cached


def set_animation(level: AnimLevel) -> None:
    """Override the animation level for the rest of the session."""
    global _cached
    _cached = detect(force_animation=level)
