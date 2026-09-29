"""Theme registry and resolution.

Resolution order, first hit wins:

1. an explicit argument (CLI flag or API call)
2. the ``JOBIA_THEME`` environment variable
3. ``theme`` in ``config.json``
4. automatic selection from terminal capabilities

Automatic selection is what makes the CLI work "everywhere" without the user
thinking about it: a pipe gets ``plain``, everything else gets ``jobia``.
"""
from __future__ import annotations

import os
from typing import Iterable

from aurora_cli import brand
from aurora_cli.core.capabilities import Capabilities
from aurora_cli.themes.base import Theme
from aurora_cli.themes.extra import ABYSS, PLAIN
from aurora_cli.themes.jobia import JOBIA
from aurora_cli.themes.otter import OTTER

#: Registered themes, in menu order. The first entry is the default.
_REGISTRY: tuple[Theme, ...] = (JOBIA, OTTER, ABYSS, PLAIN)

_BY_ID: dict[str, Theme] = {theme.id: theme for theme in _REGISTRY}

#: Also accept the pre-rename identifier so old config files keep working.
_ALIASES: dict[str, str] = {
    "default": JOBIA.id,
    "nexus": JOBIA.id,
    "cyber": JOBIA.id,
    "loutre": OTTER.id,
    "mono": PLAIN.id,
    "none": PLAIN.id,
}

DEFAULT_THEME_ID = JOBIA.id


def all_themes() -> tuple[Theme, ...]:
    """Every registered theme, in menu order."""
    return _REGISTRY


def theme_ids() -> list[str]:
    return [theme.id for theme in _REGISTRY]


def is_known(name: str) -> bool:
    return _resolve_name(name) is not None


def get(name: str) -> Theme | None:
    """Look up a theme by id or alias, or ``None`` if unknown."""
    resolved = _resolve_name(name)
    return _BY_ID.get(resolved) if resolved else None


def default() -> Theme:
    return _BY_ID[DEFAULT_THEME_ID]


def _resolve_name(name: str) -> str | None:
    if not name:
        return None
    key = name.strip().lower()
    if key in _BY_ID:
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    return None


def auto_select(caps: Capabilities) -> Theme:
    """Pick a theme that suits what the terminal can actually do."""
    if caps.color_depth == "none" or not caps.is_tty:
        return _BY_ID[PLAIN.id]
    if not caps.unicode:
        return _BY_ID[PLAIN.id]
    return default()


def resolve(name: str = "", caps: Capabilities | None = None,
            config_theme: str = "") -> tuple[Theme, str]:
    """Resolve a theme, reporting which source won.

    Returns the theme and a short origin string (``argument``, ``env``,
    ``config`` or ``auto``) so the user can see *why* a theme was picked.
    An unknown name never raises: it falls back and is reported, because a
    typo in a config file should not prevent the CLI from starting.

    Terminal capabilities are deliberately *not* applied here. A theme is
    returned as authored; colour depth, Unicode support and motion are
    adapted at render time, so asking for ``otter`` in a 16-colour console
    still gives you the otter, just drawn in sixteen colours.
    """
    from aurora_cli.core import capabilities as caps_module

    capabilities = caps if caps is not None else caps_module.current()

    for source, candidate in (
        ("argument", name),
        ("env", os.environ.get(brand.env("theme"), "")),
        ("config", config_theme),
    ):
        if not candidate:
            continue
        theme = get(candidate)
        if theme is not None:
            return theme, source
        if source == "argument":
            # An explicit bad flag is a user error worth reporting loudly.
            return auto_select(capabilities), f"unknown:{candidate}"

    return auto_select(capabilities), "auto"


def describe(themes: Iterable[Theme] | None = None) -> str:
    """One-line-per-theme summary for the CLI menu."""
    lines = []
    for theme in (themes if themes is not None else _REGISTRY):
        marker = " (défaut)" if theme.id == DEFAULT_THEME_ID else ""
        lines.append(f"{theme.id:<8} {theme.label:<8} {theme.description}{marker}")
    return "\n".join(lines)
