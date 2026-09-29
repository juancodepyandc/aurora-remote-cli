"""Theme registry, resolution order, and terminal adaptation."""
from __future__ import annotations

import pytest

from aurora_cli import themes
from aurora_cli.core import capabilities
from aurora_cli.core.color import to_ansi16, to_ansi256
from aurora_cli.themes.base import Glyphs


@pytest.fixture
def no_color(monkeypatch):
    monkeypatch.setenv("JOBIA_COLOR", "none")
    return capabilities.detect()


@pytest.fixture
def color16(monkeypatch):
    monkeypatch.setenv("JOBIA_COLOR", "16")
    monkeypatch.setenv("JOBIA_ANIM", "none")
    return capabilities.detect()


def test_default_theme_is_jobia():
    assert themes.DEFAULT_THEME_ID == "jobia"
    assert themes.default().id == "jobia"


def test_otter_is_selectable():
    assert themes.get("otter").id == "otter"
    assert themes.get("loutre").id == "otter"  # documented alias


@pytest.mark.parametrize("alias,expected", [
    ("default", "jobia"),
    ("nexus", "jobia"),   # pre-rename id must keep resolving
    ("cyber", "jobia"),
    ("loutre", "otter"),
    ("mono", "plain"),
    ("none", "plain"),
])
def test_legacy_aliases_still_resolve(alias, expected):
    assert themes.get(alias).id == expected


def test_unknown_name_is_reported_not_raised(monkeypatch):
    monkeypatch.delenv("JOBIA_THEME", raising=False)
    theme, origin = themes.resolve("does-not-exist")
    # Falls back rather than crashing, and says why.
    assert theme.id in themes.theme_ids()
    assert "unknown" in origin


def test_resolution_order(monkeypatch):
    caps = capabilities.detect()
    monkeypatch.setenv("JOBIA_THEME", "abyss")
    # Explicit argument beats the environment.
    assert themes.resolve("otter", caps)[1] == "argument"
    # Environment beats config.
    assert themes.resolve("", caps, config_theme="plain")[1] == "env"
    # Config beats automatic selection.
    monkeypatch.delenv("JOBIA_THEME")
    assert themes.resolve("", caps, config_theme="plain")[1] == "config"


def test_pipe_gets_plain_theme(no_color):
    """A non-TTY must not emit escape sequences or fancy art."""
    assert themes.auto_select(no_color).id == "plain"


def test_color_is_quantized_for_16_colour_terminals(color16):
    theme = themes.get("otter")
    style = theme.style("primary", color16)
    # 16-colour output must be a named ANSI code, not an RGB triple.
    assert "38;2;" not in style


def test_no_colour_yields_no_escape_sequences(no_color):
    style = themes.get("jobia").style("primary", no_color)
    assert "\x1b" not in style


def test_glyphs_fall_back_to_ascii(monkeypatch):
    monkeypatch.setenv("JOBIA_UNICODE", "0")
    caps = capabilities.detect()
    assert caps.unicode is False
    theme = themes.get("otter")
    art = theme.banner.art_for(caps.unicode)
    assert all(ord(ch) < 128 for ch in art)


def test_every_theme_has_distinct_identity():
    ids = themes.theme_ids()
    assert len(ids) == len(set(ids))
    for theme in themes.all_themes():
        assert theme.id and theme.label and theme.description


def test_prompt_text_is_plain_not_markup():
    """prompt_toolkit renders the prompt literally, so no markup may leak in."""
    for theme in themes.all_themes():
        assert "[" not in theme.prompt_text(capabilities.detect())


def test_ansi_helpers_produce_valid_codes():
    assert to_ansi256(0x6EE7B7).startswith("color(")
    assert to_ansi16(0xFFFFFF).startswith("ansi(")
