"""The otter theme.

A sea otter floating on its back, holding a shell. The banner bobs on a sine
wave, bubbles rise in front of it and the water ripples underneath, which is
why the effect is ``wave`` rather than a reveal: the point of this theme is
that it looks *alive* rather than that it appears quickly.

It is also the most demanding theme, so it carries a full ASCII fallback for
terminals without block-drawing characters.
"""
from __future__ import annotations

from aurora_cli.themes.base import (
    BannerSpec,
    Decoration,
    Glyphs,
    Motion,
    Palette,
    Theme,
)

OTTER_ART = r"""
       ▄▄▄▄▄▄▄▄▄▄
    ▄█████████████████▄
   █████ ●         ● █████
  ██████    ▄▄▄▄    ██████
  ██████  ▄███████▄  ██████
   ▀████  ▀▀█████▀▀  ████▀
     ▀███████████████▀
   ╭════════════════════╮
  ╱  ╭──────╮ ╭──────╮  ╲
 ◕   │  ◍   ╰─╯  ◍   │   ◕
  ╲  ╰──────────────╯  ╱
   ╲        ▄██▄       ╱
    ╲     ▄██████▄     ╱
     ╰──────────────╯
"""

OTTER_ART_ASCII = r"""
     ,--------.
   .'          `.
  /  o      o    \
 |      __        |
 |     (oo)       |
  \   `----'     /
   `.  paws   shell .'
     `--.___.--'
"""

PALETTE = Palette(
    primary="#6ee7b7",
    secondary="#38bdf8",
    accent="#fbbf24",
    success="#7ee787",
    warning="#fbbf24",
    error="#fb7185",
    info="#7dd3fc",
    text="#eaf6f2",
    muted="#6b8f88",
    border="#2f5d57",
    banner="#5eead4",
    banner_alt="#a78bfa",
)

OTTER = Theme(
    id="otter",
    label="Otter",
    description="Loutre sur le dos — vagues, bulles et clapot d'eau.",
    palette=PALETTE,
    glyphs=Glyphs(
        spin_wave="◜◝◞◟",
        bubble="o",
        bubble_big="O",
        water="~",
        ripple="≈",
    ),
    banner=BannerSpec(
        art=OTTER_ART,
        art_ascii=OTTER_ART_ASCII,
        effect="wave",
        gradient=True,
        decorations=(
            Decoration(kind="water", density=0.5, seed=3),
            Decoration(kind="bubbles", density=0.55, seed=17),
            Decoration(kind="stars", density=0.06, seed=5),
        ),
        total_ms=2200,
        caption="~ loutre en service ~",
    ),
    motion=Motion(banner_ms=2200, shimmer=True, panel_pulse=True, typewriter_help=True),
    prompt="otter > ",
    prompt_suffix="~ ",
    title="Otter",
    subtitle="Loutre sur le dos",
    box="round",
    diff_added="+",
    diff_removed="-",
)
