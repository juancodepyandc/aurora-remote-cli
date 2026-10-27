"""Rendering contracts: every theme, output capability, contrast and safe data."""
import dataclasses
import io

import pytest
from rich.console import Console

from aurora_cli import display, graphics, themes
from aurora_cli.core import capabilities
from aurora_cli.terminal import ascii_text, target_label


def caps(**overrides):
    base = capabilities.Capabilities('linux', True, 'truecolor', True, 'none', 80, 24, True, False)
    return dataclasses.replace(base, **overrides)


@pytest.mark.parametrize('theme', themes.all_themes(), ids=lambda t: t.id)
@pytest.mark.parametrize('width', [60, 80, 120, 200])
def test_theme_component_matrix_fits_width(theme, width):
    output = io.StringIO()
    console = Console(file=output, width=width, color_system=None, highlight=False)
    view = display.Display(console, theme, caps(width=width))
    view.banner(animate=False)
    view.success('Fichier reçu et vérifié')
    view.warning('Connexion interrompue ; reprise avec le curseur conservé')
    view.error('Outil refusé ; autorisation requise')
    view.console.print(view.markdown('## Réponse\nTexte **important**.\n```python\nprint("[red]literal[/red]")\n```'))
    view.code_diff('code.py', ['@@ -42,2 +42,2 @@', '-old', '+new', ' context'])
    view.console.print(view.table('Sessions', [('Session', 'text'), ('État', 'muted')], [('session-long-'*15, '[red]literal[/red]')]))
    from rich.cells import cell_len
    assert all(cell_len(line) <= width for line in output.getvalue().splitlines())
    assert '[red]literal[/red]' in output.getvalue()
    assert '42' in output.getvalue()


def luminance(hex_color):
    values = [int(hex_color[i:i+2], 16)/255 for i in (1, 3, 5)]
    channels = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in values]
    return sum(v*w for v,w in zip(channels, [.2126, .7152, .0722]))


@pytest.mark.parametrize('theme', themes.all_themes(), ids=lambda t:t.id)
def test_themes_are_complete_and_readable(theme):
    for slot in ('text', 'muted', 'primary', 'success', 'warning', 'error'):
        for background in ('surface', 'raised'):
            light, dark = sorted([luminance(theme.raw(slot)), luminance(theme.raw(background))], reverse=True)
            assert (light+.05)/(dark+.05) >= 4.5, (theme.id, slot, background)
    assert theme.banner.art_ascii.isascii()
    assert theme.components.header and theme.components.pixel_art


def test_eight_identities_change_more_than_color():
    all_themes = themes.all_themes()
    assert len(all_themes) >= 8
    assert len({t.components.header for t in all_themes}) == len(all_themes)
    assert len({t.box for t in all_themes}) >= 4
    assert len({t.banner.effect for t in all_themes}) >= 5
    assert len({t.spacing for t in all_themes}) >= 4


def test_no_color_overrides_environment_palette_and_pipe_has_no_motion(monkeypatch):
    monkeypatch.setenv('NO_COLOR', '1')
    monkeypatch.setenv('JOBIA_COLOR', 'truecolor')
    monkeypatch.setenv('JOBIA_ANIM', 'full')
    detected = capabilities.detect(io.StringIO())
    assert detected.color_depth == 'none'
    assert not detected.animate
    assert detected.image_protocol == 'none'


def test_environment_diagnostic_never_collects_secret_names(monkeypatch):
    monkeypatch.setenv('JOBIA_API_KEY', 'synthetic-secret')
    monkeypatch.setenv('JOBIA_PRIVATE_THING', 'synthetic-private')
    assert 'synthetic-' not in str(capabilities.detect().env_overrides)


@pytest.mark.parametrize('depth', ['ansi','color256','truecolor','none'])
def test_depth_and_ascii_rendering(depth):
    output = io.StringIO()
    view = display.Display(theme=themes.get('otter'), caps=caps(color_depth=depth, unicode=False))
    view.console.file = output
    view.banner(animate=False)
    view.success('Succès → fichier vérifié')
    result = output.getvalue()
    assert result.isascii()
    if depth == 'none':
        assert '\x1b' not in result
    elif depth == 'ansi':
        assert '38;2;' not in result and '38;5;' not in result
    elif depth == 'color256':
        assert '38;2;' not in result


def test_untrusted_text_is_literal_and_target_is_redacted():
    output = io.StringIO()
    view = display.Display(Console(file=output), themes.default(), caps())
    view.error('[red]literal[/red]\x1b[2J')
    assert '[red]literal[/red]' in output.getvalue()
    assert '\x1b[2J' not in output.getvalue()
    assert target_label('https://user:secret@example.invalid:443/path?key=secret#secret') == 'example.invalid:443'
    assert ascii_text('Succès → vérifié') == 'Succes -> verifie'


def test_pixel_encoders_and_fallbacks():
    theme = themes.get('otter')
    assert graphics.png(theme).startswith(b'\x89PNG\r\n\x1a\n')
    assert graphics.image_sequence(theme, 'kitty').startswith('\x1b_G')
    assert graphics.image_sequence(theme, 'iterm2').startswith('\x1b]1337;File=')
    assert graphics.image_sequence(theme, 'sixel').startswith('\x1bPq')
    assert '▀' in graphics.block_art(theme, caps()).plain
    assert graphics.block_art(theme, caps(unicode=False)).plain.isascii()


def test_image_capabilities_are_conservative(monkeypatch):
    class TTY:
        def isatty(self): return True
    monkeypatch.delenv('NO_COLOR', raising=False)
    monkeypatch.setenv('JOBIA_COLOR', 'truecolor')
    monkeypatch.setenv('TERM_PROGRAM', 'iTerm.app')
    monkeypatch.delenv('TMUX', raising=False)
    monkeypatch.delenv('KITTY_WINDOW_ID', raising=False)
    monkeypatch.setenv('TERM', 'xterm-256color')
    assert capabilities.detect(TTY()).image_protocol == 'iterm2'
    monkeypatch.setenv('TMUX', 'fixture')
    assert capabilities.detect(TTY()).image_protocol == 'none'
