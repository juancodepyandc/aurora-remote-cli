"""Two extra themes that prove the registry is data-driven.

``abyss`` is the dark, high-contrast option. ``plain`` disables colour and
motion entirely and is what falls back automatically when the terminal
reports no colour support, so it is written to stay readable as text.
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

# --- abyss ----------------------------------------------------------------

ABYSS_ART = r"""
        ▄▄▄▄▄▄▄▄▄▄▄▄
     ▄█████████████████▄
    ████  ·          ·  ████
   █████     ▁▁▁▁▁     █████
   █████   ▄████████▄   █████
    ▀███   ▀▀██████▀▀   ███▀
      ▀███▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀█▀
      ╭────────────────────╮
     ╱  ▄▄▄          ▄▄▄  ╲
    ◕   █   ▄▄▄▄▄   █     ◕
     ╲  ▀▀▀ █████ ▀▀▀   ╱
      ╲        ╲       ╱
       ╲        ╲     ╱
        ╰────────╲───╯
"""

ABYSS = Theme(
    id="abyss",
    label="Abyss",
    description="Bleu profond, étoiles et braises. Pour les longues nuits.",
    palette=Palette(
        primary="#7aa2f7",
        secondary="#bb9af7",
        accent="#e0af68",
        success="#9ece6a",
        warning="#e0af68",
        error="#f7768e",
        info="#7dcfff",
        text="#c0caf5",
        muted="#565f89",
        border="#2b3350",
        banner="#7aa2f7",
        banner_alt="#bb9af7",
    ),
    banner=BannerSpec(
        art=ABYSS_ART,
        effect="breathe",
        decorations=(
            Decoration(kind="stars", density=0.35, seed=23),
            Decoration(kind="sparks", density=0.18, seed=29),
        ),
        total_ms=1800,
        caption="descente dans le bleu",
    ),
    motion=Motion(banner_ms=1800, shimmer=True, panel_pulse=True, scanline=True),
    prompt="abyss > ",
    prompt_suffix="▸ ",
    title="Abyss",
    subtitle="Descente dans le bleu",
    box="heavy",
)

# --- plain ----------------------------------------------------------------

PLAIN_ART = r"""
   _  _  _   ___  ____    _    _   _ ____
  | |/ / | | |_ _|/ ___|  / \  | \ | |  _ \
  | ' <  | |  | | \___ \ / _ \ |  \| | |_) |
  | . \ | |  | |  ___) |  ___ \| |\  |  _ <
  |_|\_\| |_| |_| |____/ /_/  \_\_| \_| |_|_\
      JOBIA · Juan Of Bike IA
"""

PLAIN = Theme(
    id="plain",
    label="Plain",
    description="Sans couleur ni animation. Pipelines, logs, CI, lecteurs d'écran.",
    palette=Palette(
        primary="#808080", secondary="#808080", accent="#808080",
        success="#808080", warning="#808080", error="#808080", info="#808080",
        text="#c0c0c0", muted="#808080", border="#808080",
        banner="#c0c0c0", banner_alt="#808080",
    ),
    glyphs=Glyphs(
        border_heavy=("+", "+", "+", "+", "+", "+", "+", "+"),
        ok=("[ok]", "[ok]"),
        fail=("[!!]", "[!!]"),
        warn=("[!]", "[!]"),
        info=("[i]", "[i]"),
        bullet=("-", "-"),
        arrow=(">", ">"),
        spin_dots="|/-\\",
    ),
    banner=BannerSpec(
        art=PLAIN_ART,
        effect="static",
        gradient=False,
        decorations=(),
        total_ms=0,
    ),
    motion=Motion(banner_ms=0, shimmer=False, panel_pulse=False,
                 typewriter_help=False, respect_reduced_motion=True),
    prompt="jobia> ",
    prompt_suffix="> ",
    title="JOBIA",
    subtitle="mode texte",
    box="ascii",
    scan_style="ansi_dark",
)
