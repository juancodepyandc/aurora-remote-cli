"""Theme data model.

A theme is pure data: colours, glyphs, banner instructions, prompt text,
motion preferences. It contains no rendering logic and no terminal
assumptions, which is what lets the same theme work on a 24-bit Linux
terminal, a 16-colour Windows console and a pipe in CI.

Rendering lives in :mod:`aurora_cli.core.animation` and
:mod:`aurora_cli.display`; colour adaptation lives in
:mod:`aurora_cli.core.color`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

from aurora_cli.core import color as colour
from aurora_cli.core.capabilities import Capabilities

HexColor = str

# --- Palette --------------------------------------------------------------


@dataclass(frozen=True)
class Palette:
    """Semantic colour slots. Renderers ask for meaning, never for hue."""

    primary: HexColor = "#4dd0e1"
    secondary: HexColor = "#c77dff"
    accent: HexColor = "#ffd166"
    success: HexColor = "#3ddc84"
    warning: HexColor = "#ffb703"
    error: HexColor = "#ff5f6d"
    info: HexColor = "#63b3ff"
    text: HexColor = "#e8e8e8"
    muted: HexColor = "#7a8699"
    border: HexColor = "#3a4a63"
    banner: HexColor = "#4dd0e1"
    banner_alt: HexColor = "#c77dff"
    surface: HexColor = "#0b111b"
    raised: HexColor = "#131f2c"

    def slot(self, name: str) -> HexColor:
        """Look up a slot by name, falling back to the primary colour."""
        value = getattr(self, name, None)
        if isinstance(value, str):
            return value
        return self.primary

    def as_dict(self) -> dict[str, HexColor]:
        return {f.name: getattr(self, f.name) for f in self.__dataclass_fields__.values()}  # type: ignore[attr-defined]

    def with_overrides(self, **overrides: HexColor) -> "Palette":
        known = {k: colour.normalize(v) for k, v in overrides.items() if k in self.__dataclass_fields__}
        return replace(self, **known) if known else self

    def ramp(self, count: int, slots: Sequence[str] = ("primary", "secondary", "banner_alt")) -> list[HexColor]:
        return colour.gradient([self.slot(s) for s in slots], count)


# --- Glyphs ---------------------------------------------------------------


@dataclass(frozen=True)
class Glyphs:
    """Every character a theme draws, with an ASCII fallback for each.

    Terminals that cannot render block drawing or emoji get the plain column
    instead of mojibake, which is why nothing else in the codebase writes a
    box-drawing character directly.
    """

    # panel borders, keyed by the Rich box style they pair with
    border_heavy: tuple[str, str, str, str, str, str, str, str] = (
        "┏", "━", "┓", "┣", "━", "┫", "┗", "┛",
    )
    border_round: tuple[str, ...] = ("╭", "─", "╮", "│", "│", "╰", "╯", "├", "┤", "┬", "┴", "┼")
    border_ascii: tuple[str, ...] = ("+", "-", "+", "|", "|", "+", "+", "+", "+", "+", "+", "+")

    ok: tuple[str, str] = ("✔", "+")
    fail: tuple[str, str] = ("✖", "x")
    warn: tuple[str, str] = ("▲", "!")
    info: tuple[str, str] = ("›", ">")
    bullet: tuple[str, str] = ("•", "-")
    arrow: tuple[str, str] = ("❯", ">")
    prompt_cursor: tuple[str, str] = ("█", "#")
    spin_dots: str = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    spin_arc: str = "◜◠◝◞◡◟"
    spin_wave: str = "▁▂▃▄▅▆▇█▇▆▅▄▃▂"
    bubble: str = "o"
    bubble_big: str = "O"
    water: str = "~"
    ripple: str = "≈"
    shard: str = "░▒▓"

    def pick(self, pair: tuple[str, str], unicode: bool) -> str:
        return pair[0] if unicode else pair[1]

    def for_box(self, unicode: bool) -> str:
        """Return the border style name matching the detected capability."""
        return "rounded" if unicode else "ascii"


# --- Banner specification -------------------------------------------------


@dataclass(frozen=True)
class Decoration:
    """A procedural overlay drawn behind or under the banner art.

    The animation engine owns the implementation; themes only declare what
    they want. Keeping it declarative means a new ambient effect is one
    engine branch, not a fork of every theme.
    """

    kind: str  # water | bubbles | stars | rain | snow | matrix | sparks
    density: float = 0.4
    color: HexColor | None = None
    seed: int = 7


@dataclass(frozen=True)
class BannerSpec:
    """Instructions for rendering the startup banner."""

    art: str = ""
    art_ascii: str = ""
    frames: tuple[str, ...] = ()
    effect: str = "reveal"  # reveal | glitch | wave | typewriter | breathe | static
    gradient: bool = True
    decorations: tuple[Decoration, ...] = ()
    speed: float = 1.0
    total_ms: int = 1400
    caption: str = ""

    @property
    def animated(self) -> bool:
        return bool(self.frames) or self.effect != "static"

    def art_for(self, unicode_ok: bool) -> str:
        """Pick the artwork this terminal can actually draw."""
        if not unicode_ok and self.art_ascii.strip():
            return self.art_ascii
        if self.frames:
            return self.frames[-1]
        return self.art


# --- Motion ---------------------------------------------------------------


@dataclass(frozen=True)
class Motion:
    """Timing and motion preferences, all bounded by a time budget."""

    banner_ms: int = 1400
    stream_ms_per_token: int = 0
    shimmer: bool = True
    panel_pulse: bool = True
    scanline: bool = False
    typewriter_help: bool = False
    respect_reduced_motion: bool = True

    def effective(self, caps: Capabilities) -> int:
        """Scale the banner budget to the detected animation level."""
        if caps.animation == "reduced":
            return max(200, self.banner_ms // 4)
        if caps.animation in {"static", "none"}:
            return 0
        return self.banner_ms


@dataclass(frozen=True)
class Spacing:
    """Terminal cells, never pixels. Shared by Rich and full-screen layouts."""

    gutter: int = 2
    panel_x: int = 2
    panel_y: int = 1
    section: int = 1
    prompt_rows: int = 2
    sidebar_width: int = 34


@dataclass(frozen=True)
class Components:
    """Declarative component variants; changing identity needs no screen edit."""

    header: str = "horizon"
    separator: str = "─"
    rail: str = "│"
    tab_open: str = "["
    tab_close: str = "]"
    spinner: str = "dots"
    pixel_art: tuple[str, ...] = ()
    pixel_palette: tuple[str, ...] = ()


# --- Theme ----------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    """A complete, swappable visual identity."""

    id: str
    label: str
    description: str
    palette: Palette = field(default_factory=Palette)
    glyphs: Glyphs = field(default_factory=Glyphs)
    banner: BannerSpec = field(default_factory=BannerSpec)
    motion: Motion = field(default_factory=Motion)
    prompt: str = "JOBIA > "
    prompt_suffix: str = "› "
    title: str = "JOBIA"
    subtitle: str = ""
    box: str = "rounded"
    title_align: str = "left"
    scan_style: str = "monokai"
    diff_added: str = "+"
    diff_removed: str = "-"
    diff_context: str = " "
    spacing: Spacing = field(default_factory=Spacing)
    components: Components = field(default_factory=Components)

    def with_overrides(self, **overrides) -> "Theme":
        return replace(self, **overrides)

    # --- Style helpers ---------------------------------------------------

    def style(self, slot: str, caps: Capabilities, **flags) -> str:
        """Build a Rich style for a palette slot, adapted to the terminal."""
        return colour.rich_style(self.palette.slot(slot), caps.color_depth, **flags)

    def markup(self, slot: str, caps: Capabilities, text: str, **flags) -> str:
        """Wrap ``text`` in Rich markup using an adapted palette slot."""
        return f"[{self.style(slot, caps)}]{text}[/]"

    def raw(self, slot: str) -> HexColor:
        """Raw 24-bit value, for prompt_toolkit and gradient maths."""
        return colour.normalize(self.palette.slot(slot))

    def glyph(self, name: str, caps: Capabilities) -> str:
        """Resolve a glyph by attribute name, honouring Unicode support."""
        value = getattr(self.glyphs, name, None)
        if isinstance(value, tuple):
            return value[0] if caps.unicode else value[1]
        return value if isinstance(value, str) else ""

    def box_for(self, caps: Capabilities) -> str:
        """Resolve the border style against what the terminal can draw.

        Themes ask for a character set by name; this is the only place that
        decides which one actually gets used.
        """
        if not caps.unicode:
            return "ascii"
        return self.box

    def prompt_text(self, caps: Capabilities) -> str:
        """The REPL prompt, as plain text.

        Rich markup is deliberately not used: this string is handed to
        ``input()`` and to prompt_toolkit, which render it literally. Colour
        for the prompt comes from ``prompt_toolkit_style`` on the prompt_toolkit
        side, and from the calling layer's own styling on the ``input()`` side.
        """
        if caps.unicode:
            return self.prompt
        from aurora_cli.terminal import ascii_text
        return ascii_text(self.prompt)

    def banner_ramp(self, caps: Capabilities, count: int) -> list[HexColor]:
        if not self.banner.gradient or not caps.rich_palette:
            return [colour.normalize(self.palette.banner)] * count
        return self.palette.ramp(count, ("banner", "banner_alt", "secondary"))

    def prompt_toolkit_style(self, caps: Capabilities) -> dict[str, str]:
        """Style dict for prompt_toolkit, derived from the same palette (100% dark, zero white bars)."""
        if not caps.color:
            return {'prompt': 'bold', 'completion-menu.completion.current': 'reverse',
                    'completion-menu.meta.completion.current': 'reverse',
                    'button.focused': 'reverse', 'radio-selected': 'reverse',
                    'radio-checked': 'bold', 'dialog frame.label': 'bold'}
        primary = colour.to_prompt_toolkit(self.raw("primary"), caps.color_depth)
        secondary = colour.to_prompt_toolkit(self.raw("secondary"), caps.color_depth)
        accent = colour.to_prompt_toolkit(self.raw("accent"), caps.color_depth)
        text = colour.to_prompt_toolkit(self.raw("text"), caps.color_depth)
        muted = colour.to_prompt_toolkit(self.raw("muted"), caps.color_depth)
        border = colour.to_prompt_toolkit(self.raw("border"), caps.color_depth)
        background = colour.to_prompt_toolkit(self.raw('surface'), caps.color_depth)
        surface = colour.to_prompt_toolkit(self.raw('raised'), caps.color_depth)
        return {
            "prompt": f"{primary} bold",
            "completion-menu": f"bg:{surface} {text}",
            "completion-menu.completion": f"bg:{surface} {primary}",
            "completion-menu.completion.current": f"bg:{primary} #060911 bold",
            "completion-menu.meta.completion": f"bg:{background} {muted}",
            "completion-menu.meta.completion.current": f"bg:{secondary} #060911 bold",
            "scrollbar.background": f"bg:{background} {border}",
            "scrollbar.button": f"bg:{primary} {primary}",
            "scrollbar.arrow": f"bg:{background} {primary} bold",
            "dialog": f"bg:{background} {text}",
            "dialog.body": f"bg:{surface} {text}",
            "dialog frame.label": f"bg:{surface} {primary} bold",
            "dialog.body text-area": f"bg:{surface} {text}",
            "dialog.body scrollbar.background": f"bg:{background}",
            "dialog.body scrollbar.button": f"bg:{primary}",
            "button": f"bg:{surface} {primary}",
            "button.focused": f"bg:{primary} #060911 bold",
            "radiolist": f"bg:{surface} {text}",
            "radio-selected": f"bg:{primary} #060911 bold",
            "radio-checked": f"{accent} bold",
            "bottom-toolbar": f"bg:{background} {muted}",
            "bottom-toolbar.on": f"bg:{primary} #060911",
            "arg-toolbar": f"bg:{background} {muted}",
            "search-toolbar": f"bg:{accent} #060911",
            "keyword": f"{secondary} bold",
            "string": accent,
        }
