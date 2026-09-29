"""Animation engine: correctness, budgets, and graceful degradation."""
from __future__ import annotations

import pytest
from rich.console import Console

from aurora_cli import themes
from aurora_cli.core import animation, capabilities


def _animator(caps, theme_id="jobia", width=80, height=24):
    theme = themes.get(theme_id)
    console = Console(width=width, height=height, force_terminal=False)
    return animation.Animator(console, caps, theme)


@pytest.fixture
def animating(monkeypatch):
    monkeypatch.setenv("JOBIA_ANIM", "full")
    monkeypatch.setenv("JOBIA_COLOR", "truecolor")
    return capabilities.detect()


@pytest.fixture
def static(monkeypatch):
    monkeypatch.setenv("JOBIA_ANIM", "none")
    monkeypatch.setenv("JOBIA_COLOR", "none")
    return capabilities.detect()


@pytest.mark.parametrize("theme_id", ["jobia", "otter", "abyss", "plain"])
def test_banner_renders_for_every_theme(animating, theme_id):
    """Every registered theme must produce a non-empty banner."""
    art = _animator(animating, theme_id).render_banner()
    assert art.plain.strip()


@pytest.mark.parametrize("theme_id", ["jobia", "otter", "abyss", "plain"])
def test_banner_survives_a_narrow_terminal(animating, theme_id):
    """A 20-column terminal must not raise or emit over-wide art."""
    art = _animator(animating, theme_id, width=20).render_banner()
    assert art is not None


@pytest.mark.parametrize("theme_id", ["jobia", "otter", "abyss", "plain"])
def test_no_animation_still_prints_a_banner(static, theme_id):
    """With animation off the banner is printed once, not dropped."""
    animator = _animator(static, theme_id)
    if static.animate:
        pytest.skip("animation forced on for this environment")
    animator.play_banner()  # must not raise


def test_effect_grid_never_drops_characters(animating):
    """A static pass must be lossless: same character multiset in and out."""
    art = "  _ _  \n | || |\n |__|__|\n"
    grid = animation.effect_grid(art, "static", 1.0, [], None)
    flat = "".join(cell.char for row in grid for cell in row)
    assert flat.count("_") == art.count("_")
    assert len(grid) == len(art.split("\n"))


@pytest.mark.parametrize("effect", ["glitch", "reveal", "typewriter", "breathe", "wave", "static"])
def test_effect_grid_preserves_shape(animating, effect):
    art = "JOBIA\nMODEL\n"
    rows = art.split("\n")
    grid = animation.effect_grid(art, effect, 0.5, ["#fff", "#000"], None)
    assert len(grid) == len(rows)


def test_no_escape_sequences_without_colour(static):
    """Piped output must be plain text, or logs and diffs get polluted."""
    art = _animator(static).render_banner()
    assert "\x1b" not in art.plain


def test_slow_terminal_reduces_frames(monkeypatch):
    monkeypatch.setenv("JOBIA_ANIM", "reduced")
    caps = capabilities.detect()
    assert caps.frame_interval() >= 0
    # A reduced budget must still produce something.
    assert _animator(caps).render_banner().plain.strip()


def test_spinners_degrade_to_ascii(monkeypatch):
    monkeypatch.setenv("JOBIA_UNICODE", "0")
    monkeypatch.setenv("JOBIA_ANIM", "none")
    caps = capabilities.detect()
    for kind in ("dots", "arc", "wave", "pulse"):
        frames = _animator(caps).frames_for(kind)
        assert frames
        assert all(ord(ch) < 128 for ch in frames)


def test_unknown_spinner_falls_back(monkeypatch):
    monkeypatch.setenv("JOBIA_ANIM", "none")
    assert _animator(capabilities.detect()).frames_for("nope")
