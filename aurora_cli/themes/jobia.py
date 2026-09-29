"""The default JOBIA theme.

Deliberately restrained: this is what every user sees before they choose
something else, so it has to read well at 80 columns, on 16-colour Windows
consoles, and inside a CI log where animations are stripped.
"""
from __future__ import annotations

from aurora_cli import brand
from aurora_cli.themes.base import (
    BannerSpec,
    Decoration,
    Glyphs,
    Motion,
    Palette,
    Theme,
)

JOBIA_ART = r"""
     ██╗ ██████╗ ██████╗ ██╗ █████╗
     ██║██╔═══██╗██╔══██╗██║██╔══██╗
     ██║██║   ██║██████╔╝██║███████║
██   ██║██║   ██║██╔══██╗██║██╔══██║
╚█████╔╝╚██████╔╝██████╔╝██║██║  ██║
 ╚════╝  ╚═════╝ ╚═════╝ ╚═╝╚═╝  ╚═╝
"""

PALETTE = Palette(
    primary="#4dd0e1",
    secondary="#c77dff",
    accent="#ffd166",
    success="#3ddc84",
    warning="#ffb703",
    error="#ff5f6d",
    info="#63b3ff",
    text="#e8e8e8",
    muted="#7a8699",
    border="#3a4a63",
    banner="#4dd0e1",
    banner_alt="#c77dff",
)

JOBIA = Theme(
    id="jobia",
    label="JOBIA",
    description="Défaut — cyan et violet, calme et lisible partout.",
    palette=PALETTE,
    glyphs=Glyphs(),
    banner=BannerSpec(
        art=JOBIA_ART,
        effect="reveal",
        gradient=True,
        decorations=(Decoration(kind="matrix", density=0.12, seed=11),),
        total_ms=1200,
        caption=f"{brand.APP_FULL_NAME} · {brand.APP_TAGLINE}",
    ),
    motion=Motion(banner_ms=1200, shimmer=True, panel_pulse=True),
    prompt=f"{brand.APP_NAME} > ",
    prompt_suffix="❯ ",
    title=brand.APP_NAME,
    subtitle=brand.APP_TAGLINE,
    box="rounded",
)
