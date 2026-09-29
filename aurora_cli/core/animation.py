"""Animation engine.

Everything visual that moves is produced here, from theme data plus terminal
capabilities. Three properties matter more than the effects themselves:

* **Bounded.** Every animation has a hard millisecond budget and cannot
  exceed it no matter how slow the terminal is.
* **Degradable.** Non-tty, dumb terminals, ``NO_COLOR`` and slow links all
  collapse to a single static frame that carries the same information.
* **Deterministic.** Decoration particles are seeded, so a given frame index
  always renders the same glyphs. Tests can assert on it.

Effects are declared by the theme; the implementations live here, so adding
an ambient effect never forks a theme file.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from rich.console import Console, RenderableType
from rich.live import Live
from rich.text import Text

from aurora_cli.core import color as colour
from aurora_cli.core.capabilities import Capabilities
from aurora_cli.themes.base import BannerSpec, Decoration, Glyphs, Theme

# --- Canvas ---------------------------------------------------------------


@dataclass
class Cell:
    char: str = " "
    color: str = ""
    bold: bool = False


class Canvas:
    """A small character grid that layers art and decorations together.

    Working on a grid rather than on strings is what lets bubbles rise *in
    front of* the artwork and stars twinkle *behind* it, instead of the two
    being concatenated and drifting out of alignment.
    """

    def __init__(self, width: int, height: int, background: str = ""):
        self.width = max(1, width)
        self.height = max(1, height)
        self.cells: list[list[Cell]] = [
            [Cell(" ", background) for _ in range(self.width)] for _ in range(self.height)
        ]

    def text(self, x: int, y: int, value: str, color: str = "", bold: bool = False) -> None:
        """Draw a string, clipping to the canvas."""
        for offset, char in enumerate(value):
            self.cell(x + offset, y, char, color, bold)

    def cell(self, x: int, y: int, char: str, color: str = "", bold: bool = False) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.cells[y][x] = Cell(char, color, bold)

    def overlay(self, x: int, y: int, char: str, color: str = "") -> None:
        """Draw only where a cell is empty (background layer)."""
        if 0 <= x < self.width and 0 <= y < self.height and self.cells[y][x].char == " ":
            self.cells[y][x] = Cell(char, color)

    def to_text(self) -> Text:
        out = Text()
        for row in self.cells:
            for item in row:
                if item.bold:
                    out.append(item.char, f"bold {item.color}" if item.color else "bold")
                else:
                    out.append(item.char, style=item.color)
            out.append("\n")
        return out


# --- Particle systems -----------------------------------------------------


class ParticleSystem:
    """Base particle system. Subclasses only implement :meth:`render`."""

    def __init__(self, spec: Decoration, width: int, height: int, glyphs: Glyphs,
                 unicode_ok: bool):
        self.spec = spec
        self.width = width
        self.height = height
        self.glyphs = glyphs
        self.unicode = unicode_ok
        self.rng = random.Random(spec.seed)

    def _pick(self, attribute: str, fallback: str) -> str:
        value = getattr(self.glyphs, attribute, None)
        if isinstance(value, tuple):
            return value[0] if self.unicode else value[1]
        return value if isinstance(value, str) else fallback

    def render(self, canvas: Canvas, frame: int, time_s: float, behind: bool) -> None:
        raise NotImplementedError


class Water(ParticleSystem):
    """A rippling surface beneath the artwork."""

    def render(self, canvas: Canvas, frame: int, time_s: float, behind: bool) -> None:
        row = self.height - 1
        if row < 0:
            return
        glyph = self.glyphs.water if self.unicode else "~"
        tint = self.spec.color or "#2b6ca3"
        for x in range(canvas.width):
            phase = x * 0.45 + time_s * 2.4
            wave = math.sin(phase) + 0.4 * math.sin(phase * 2.3)
            offset = 0 if wave > 0.55 else 1
            canvas.cell(x, row - offset, glyph, colour.blend(tint, "#7fd4ff", 0.4 + 0.3 * wave))


class Bubbles(ParticleSystem):
    """Bubbles rising from the water line and drifting sideways."""

    def render(self, canvas: Canvas, frame: int, time_s: float, behind: bool) -> None:
        glyph_small = self.glyphs.bubble if self.unicode else "o"
        glyph_big = self.glyphs.bubble_big if self.unicode else "O"
        count = max(1, int(self.width * self.height * 0.012 * self.spec.density * 2))
        tint = self.spec.color or "#9be7ff"
        for index in range(count):
            seed_x = (index * 37 + self.spec.seed * 11) % max(1, self.width)
            speed = 0.35 + ((index * 13) % 10) / 12.0
            phase = time_s * speed + (index * 0.7) % 3.0
            y = self.height - 1 - (phase % max(1.0, self.height))
            drift = math.sin(phase * 1.4 + index) * 0.8
            x = int(seed_x + drift)
            if not (0 <= x < self.width):
                continue
            symbol = glyph_big if (index + frame // 3) % 4 == 0 else glyph_small
            shade = 0.25 + 0.5 * ((math.sin(phase * 2) + 1) / 2)
            canvas.cell(x, int(y), symbol, colour.blend(tint, "#ffffff", shade))


class Stars(ParticleSystem):
    """Slow twinkling, drawn behind the artwork."""

    def render(self, canvas: Canvas, frame: int, time_s: float, behind: bool) -> None:
        count = max(1, int(self.width * self.height * 0.05 * self.spec.density))
        tint = self.spec.color or "#cfe3ff"
        for index in range(count):
            x = (index * 53 + self.spec.seed * 7) % max(1, self.width)
            y = (index * 31 + self.spec.seed * 3) % max(1, max(1, self.height - 2))
            twinkle = (math.sin(time_s * 1.6 + index * 0.9) + 1) / 2
            symbol = "*" if twinkle > 0.55 else "·"
            canvas.overlay(x, y, symbol, colour.blend(tint, "#ffffff", 0.2 + 0.8 * twinkle))


class Rain(ParticleSystem):
    """Falling streaks, for darker themes."""

    def render(self, canvas: Canvas, frame: int, time_s: float, behind: bool) -> None:
        count = max(1, int(self.width * self.spec.density))
        tint = self.spec.color or "#4cc9f0"
        for index in range(count):
            x = (index * 29 + self.spec.seed * 5) % max(1, self.width)
            offset = (time_s * (2.2 + (index % 4) * 0.5) + index) % max(1, self.height)
            y = int(offset)
            canvas.overlay(x, y, "|" if self.unicode else ".", colour.blend(tint, "#ffffff", 0.3))


class Sparks(ParticleSystem):
    """Embers drifting upward, the counterpart to rain."""

    def render(self, canvas: Canvas, frame: int, time_s: float, behind: bool) -> None:
        count = max(1, int(self.width * self.spec.density))
        tint = self.spec.color or "#ffb703"
        for index in range(count):
            x = (index * 41 + self.spec.seed * 9) % max(1, self.width)
            offset = (self.height - (time_s * (1.4 + (index % 3) * 0.4) + index)) % max(1, self.height)
            glyphs = ("✦", "·", "✧") if self.unicode else ("*", ".", "+")
            canvas.overlay(x, int(offset), glyphs[index % len(glyphs)],
                           colour.blend(tint, "#ffffff", 0.4))


class Matrix(ParticleSystem):
    """Falling code glyphs, the classic terminal backdrop."""

    def render(self, canvas: Canvas, frame: int, time_s: float, behind: bool) -> None:
        # Single-width glyphs only: a full-width character occupies two cells
        # and would shear the whole grid.
        pool = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789$#*+=<>{}[]/\\|"
        columns = max(1, int(self.width * 0.45 * max(0.2, self.spec.density)))
        tint = self.spec.color or "#3ddc84"
        for column in range(columns):
            x = int(column * self.width / columns) + self.spec.seed % 3
            head = int((time_s * (3.0 + (column % 5) * 0.6) + column * 2) % (self.height + 6))
            for trail in range(5):
                y = head - trail
                if not (0 <= y < self.height):
                    continue
                fade = 1.0 - trail / 5.0
                char = pool[(column * 7 + trail * 13 + int(time_s * 8)) % len(pool)]
                canvas.overlay(x, y, char, colour.blend("#0b2b18", tint, fade))


class Snow(ParticleSystem):
    """Gentle drifting flakes."""

    def render(self, canvas: Canvas, frame: int, time_s: float, behind: bool) -> None:
        count = max(1, int(self.width * self.spec.density))
        tint = self.spec.color or "#eaf4ff"
        for index in range(count):
            x = int((index * 17) % self.width) + int(math.sin(time_s + index) * 1.5)
            if not (0 <= x < self.width):
                continue
            y = int((time_s * 0.9 + index * 1.3) % max(1, self.height))
            canvas.overlay(x, y, "*" if self.unicode else ".", tint)


_DECORATIONS: dict[str, type[Decoration]] = {
    "water": Water,
    "bubbles": Bubbles,
    "stars": Stars,
    "rain": Rain,
    "snow": Snow,
    "sparks": Sparks,
    "matrix": Matrix,
}

#: Drawn before the artwork, so the art always stays readable.
_BACKGROUND = frozenset({"stars", "matrix", "snow"})

#: Drawn after the artwork, so they read as being in front of it.
_FOREGROUND = frozenset({"water", "bubbles", "rain", "sparks"})


def build_decorations(specs: Sequence[Decoration], width: int, height: int,
                      glyphs: Glyphs, unicode_ok: bool) -> list[ParticleSystem]:
    built = []
    for spec in specs:
        factory = _DECORATIONS.get(spec.kind)
        if factory is not None:
            built.append(factory(spec, width, height, glyphs, unicode_ok))
    return built


# --- Banner effects -------------------------------------------------------


def color_at(ramp: Sequence[str], row: int, column: int, rows: int, cols: int) -> str:
    """Sample the gradient along the art's diagonal.

    Indexing the ramp by a *normalised* diagonal rather than by a fixed
    stride is what stops the banner from turning into a per-character
    rainbow: the colour sweeps once across the whole piece.
    """
    if not ramp or not cols or not rows:
        return ""
    position = (row / max(1, rows - 1) * 0.35) + (column / max(1, cols - 1) * 0.65)
    return ramp[min(len(ramp) - 1, int(position * (len(ramp) - 1)))]


def style_of(ramp: Sequence[str], row: int, column: int, *, bold: bool = True,
             rows: int = 0, cols: int = 0) -> str:
    picked = color_at(ramp, row, column, rows or row + 1, cols or column + 1)
    if not picked:
        return "bold" if bold else ""
    return f"{'bold ' if bold else ''}{picked}"


def _grid(rows: Sequence[str]) -> list[list[Cell]]:
    return [[Cell(char) for char in row] for row in rows]


def effect_grid(art: str, effect: str, progress: float, ramp: Sequence[str],
                rng: random.Random | None = None) -> list[list[Cell]]:
    """Render the art at ``progress`` in ``[0, 1]`` under the named effect.

    Effects return a character grid rather than formatted text so that
    decorations can be composited underneath and in front of the artwork
    without the two fighting over escape sequences.

    ``rng`` is optional; without one a fresh generator is used, which keeps
    the effect reproducible within a frame and avoids a per-frame allocation
    on callers that do not care about seeding.
    """
    if rng is None:
        rng = random.Random()
    if effect == "glitch":
        return _glitch(art, progress, ramp, rng)
    if effect == "typewriter":
        return _typewriter(art, progress, ramp)
    if effect == "breathe":
        return _breathe(art, progress, ramp)
    if effect == "wave":
        return _wave(art, progress, ramp)
    if effect == "static":
        return _static(art, ramp)
    return _reveal(art, progress, ramp)


def _paint(rows: list[list[Cell]], row: int, column: int, char: str,
           ramp: Sequence[str], *, bold: bool = True) -> None:
    if char == " " or row >= len(rows) or column >= len(rows[row]):
        return
    total_rows, total_cols = len(rows), max(len(r) for r in rows)
    rows[row][column] = Cell(
        char, color_at(ramp, row, column, total_rows, total_cols), bold
    )


def _static(art: str, ramp: Sequence[str]) -> list[list[Cell]]:
    rows = _grid(art.split("\n"))
    for row_index, row in enumerate(rows):
        for column in range(len(row)):
            _paint(rows, row_index, column, row[column].char, ramp)
    return rows


def _reveal(art: str, progress: float, ramp: Sequence[str]) -> list[list[Cell]]:
    """Characters appear in reading order as progress advances."""
    rows = _grid(art.split("\n"))
    total = sum(len(row) for row in rows) or 1
    budget = int(total * max(0.0, min(1.0, progress)))
    consumed = 0
    for row_index, row in enumerate(rows):
        for column in range(len(row)):
            consumed += 1
            if consumed <= budget:
                _paint(rows, row_index, column, row[column].char, ramp)
    return rows


def _typewriter(art: str, progress: float, ramp: Sequence[str]) -> list[list[Cell]]:
    """Reveal with a per-column wipe, so the art assembles in place."""
    rows = _grid(art.split("\n"))
    total_rows = len(rows) or 1
    total_cols = max((len(r) for r in rows), default=1) or 1
    for row_index, row in enumerate(rows):
        for column in range(len(row)):
            # Column-major threshold: left columns appear before right ones.
            threshold = (column / total_cols) * 0.6 + (row_index / total_rows) * 0.4
            if progress >= threshold:
                _paint(rows, row_index, column, row[column].char, ramp)
    return rows


def _glitch(art: str, progress: float, ramp: Sequence[str], rng: random.Random) -> list[list[Cell]]:
    """Decaying corruption: glyphs are replaced early, then resolve to clean."""
    rows = _grid(art.split("\n"))
    total = sum(len(row) for row in rows) or 1
    visible = min(total, int(total * min(1.0, progress * 1.4)))
    corruption = max(0.0, 1.0 - progress) ** 1.5
    shards = "\u2593\u2592\u2591\u259a\u259e"
    consumed = 0
    for row_index, row in enumerate(rows):
        for column in range(len(row)):
            consumed += 1
            if consumed > visible:
                continue
            char = row[column].char
            if char == " ":
                continue
            if rng.random() < corruption * 0.45:
                _paint(rows, row_index, column, rng.choice(shards), ramp, bold=False)
            else:
                _paint(rows, row_index, column, char, ramp)
    return rows


def _breathe(art: str, progress: float, ramp: Sequence[str]) -> list[list[Cell]]:
    """Luminance oscillation on the diagonal gradient, revealed on one clock."""
    rows = _grid(art.split("\n"))
    pulse = 0.5 + 0.5 * math.sin(progress * math.pi * 3.0)
    total = sum(len(row) for row in rows) or 1
    visible = int(total * min(1.0, progress * 1.6))
    consumed = 0
    for row_index, row in enumerate(rows):
        for column in range(len(row)):
            consumed += 1
            if consumed > visible or row[column].char == " ":
                continue
            base = color_at(ramp, row_index, column, len(rows), max(len(r) for r in rows))
            rows[row_index][column] = Cell(
                row[column].char, colour.blend(base, "#ffffff", 0.2 + 0.4 * pulse), True
            )
    return rows


def _wave(art: str, progress: float, ramp: Sequence[str]) -> list[list[Cell]]:
    """Horizontal sine offset per row, reading as a body floating on water."""
    source = art.split("\n")
    total_rows = len(source) or 1
    total_cols = max((len(r) for r in source), default=1) or 1
    phase = progress * math.pi * 4.0

    offsets = [max(0, int(round(math.sin(phase + index * 0.55) * 1.2)))
               for index in range(len(source))]
    width = max((len(row) + offsets[i]) for i, row in enumerate(source)) or 1
    shifted: list[list[Cell]] = [[Cell(" ") for _ in range(width)] for _ in source]

    for row_index, row in enumerate(source):
        for column, char in enumerate(row):
            if char == " ":
                continue
            shifted[row_index][column + offsets[row_index]] = Cell(
                char, color_at(ramp, row_index, column, total_rows, total_cols), True
            )
    return shifted


# --- Animator -------------------------------------------------------------


class Animator:
    """Renders theme banners and status motion within a fixed budget."""

    def __init__(self, console: Console, caps: Capabilities, theme: Theme):
        self.console = console
        self.caps = caps
        self.theme = theme
        self._rng = random.Random(1337)

    # --- Banner ----------------------------------------------------------

    def render_banner(self, spec: BannerSpec | None = None) -> RenderableType:
        """Produce the fully revealed banner as a Rich renderable.

        Decorations are included so ``theme preview`` shows the same
        composition the user will actually see on startup.
        """
        spec = spec or self.theme.banner
        art = spec.art_for(self.caps.unicode)
        if not art:
            return Text("")
        width = self._art_width(art, spec)
        height = self._art_height(art, spec)
        decorations = build_decorations(spec.decorations, width, height,
                                         self.theme.glyphs, self.caps.unicode)
        return self._compose(art, spec, time_s=1.0, frame=1, full=True,
                             width=width, height=height, decorations=decorations)

    def play_banner(self, spec: BannerSpec | None = None) -> None:
        """Animate the banner, or print it once when animation is disabled."""
        spec = spec or self.theme.banner
        art = spec.art_for(self.caps.unicode)
        if not art:
            return
        if not self.caps.animate or self.caps.animation == "static":
            self.console.print(self._compose(art, spec, time_s=1.0, frame=1, full=True))
            return

        budget = self.theme.motion.effective(self.caps)
        if spec.total_ms:
            budget = min(budget, spec.total_ms) if self.theme.motion.banner_ms else spec.total_ms
        steps = self._frame_count(budget)
        if steps <= 0:
            self.console.print(self._compose(art, spec, time_s=1.0, frame=1, full=True))
            return
        interval = self.caps.frame_interval()
        ramp = self.theme.banner_ramp(self.caps, 12)
        width = self._art_width(art, spec)
        height = self._art_height(art, spec)
        decorations = build_decorations(spec.decorations, width, height,
                                         self.theme.glyphs, self.caps.unicode)
        started = time.monotonic()

        with Live(console=self.console, auto_refresh=False, transient=False,
                  vertical_overflow="crop") as live:
            for index in range(steps + 1):
                progress = index / steps
                elapsed = time.monotonic() - started
                live.update(self._compose(art, spec, time_s=elapsed, frame=index,
                                          progress=progress, ramp=ramp,
                                          decorations=decorations, width=width, height=height))
                live.refresh()
                if progress >= 1.0 or elapsed * 1000 >= budget:
                    break
                time.sleep(interval)

    def _frame_count(self, budget_ms: int) -> int:
        if budget_ms <= 0:
            return 0
        interval = self.caps.frame_interval()
        count = int((budget_ms / 1000.0) / interval)
        return max(4, min(count, 90))

    def _art_width(self, art: str, spec: BannerSpec) -> int:
        art_width = max((len(line) for line in art.split("\n")), default=0)
        if spec.caption:
            art_width = max(art_width, len(spec.caption))
        return min(art_width, max(40, self.caps.clamp_width()))

    def _art_height(self, art: str, spec: BannerSpec) -> int:
        rows = len(art.split("\n"))
        has_floor = any(d.kind in ("water", "bubbles") for d in spec.decorations)
        return rows + (2 if has_floor else 0)

    def _compose(self, art: str, spec: BannerSpec, *, time_s: float, frame: int,
                 progress: float | None = None, ramp: Sequence[str] | None = None,
                 decorations: Sequence[ParticleSystem] | None = None,
                 width: int = 0, height: int = 0, full: bool = False) -> RenderableType:
        """Blend background decorations, art and foreground particles.

        The art is drawn through the active effect, so a glitch really
        replaces glyphs and a wave really shifts rows, instead of the effect
        being computed and thrown away.
        """
        ramp = ramp if ramp is not None else self.theme.banner_ramp(self.caps, 12)
        progress = 1.0 if progress is None else progress
        rows = art.split("\n")
        width = width or max((len(r) for r in rows), default=0)
        height = height or len(rows)

        canvas = Canvas(width, height)
        for system in decorations or ():
            if system.spec.kind in _BACKGROUND:
                system.render(canvas, frame, time_s, behind=True)

        grid = effect_grid(art, "static" if full else spec.effect, progress, ramp, self._rng)
        # Centre the art so ambient particles balance on both sides instead of
        # piling up next to the glyphs.
        art_width = max((len(r) for r in rows), default=0)
        offset = max(0, (width - art_width) // 2)
        for row_index, row in enumerate(grid):
            if row_index >= canvas.height:
                break
            for column, cell in enumerate(row):
                target = column + offset
                if target >= canvas.width or cell.char == " ":
                    continue
                canvas.cells[row_index][target] = cell

        for system in decorations or ():
            if system.spec.kind in _FOREGROUND:
                system.render(canvas, frame, time_s, behind=False)

        rendered = canvas.to_text()
        while rendered.plain.endswith("\n"):
            rendered.right_crop(1)
        if spec.caption:
            rendered.append("\n")
            rendered.append(spec.caption, style=self.theme.style("muted", self.caps, italic=True))
        return rendered

    # --- Spinners and status --------------------------------------------

    def frames_for(self, kind: str) -> list[str]:
        """Return the glyph cycle for a spinner, ASCII-safe if needed."""
        unicode_ok = self.caps.unicode
        table = {
            "dots": self.theme.glyphs.spin_dots if unicode_ok else "|/-\\",
            "arc": self.theme.glyphs.spin_arc if unicode_ok else "-\\|/",
            "wave": self.theme.glyphs.spin_wave if unicode_ok else "_.-~",
            "pulse": "█▉▊▋▌▍▎▏" if unicode_ok else "#",
        }
        return list(table.get(kind, table["dots"]))

    def shimmer_bar(self, width: int, progress: float, time_s: float) -> str:
        """A gradient progress bar that breathes even when not advancing."""
        width = max(4, width)
        filled = int(max(0.0, min(1.0, progress)) * width)
        shimmer = int((math.sin(time_s * 4.0) + 1) / 2 * 3)
        if self.theme.motion.shimmer and self.caps.rich_palette:
            cells = []
            ramp = self.theme.palette.ramp(8, ("primary", "secondary", "banner_alt"))
            for index in range(width):
                if index < filled:
                    cells.append(colour.blend(ramp[index % len(ramp)], "#ffffff",
                                              0.35 if abs(index - shimmer) < 2 else 0.0))
                else:
                    cells.append(self.theme.raw("muted"))
            return "".join(f"\x1b[38;2;{colour.to_rgb(c)[0]};{colour.to_rgb(c)[1]};{colour.to_rgb(c)[2]}m█\x1b[0m" for c in cells)
        bar_char = self.theme.glyph("cursor", self.caps) or "#"
        return bar_char * filled + ("·" if self.caps.unicode else "-") * (width - filled)
