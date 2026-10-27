"""Shared terminal composition for the Python and native Aurora workspaces.

Rendering is pure: it never probes a machine, reads configuration or starts a
timer. The caller supplies observed state and the selected declarative theme.
"""
from __future__ import annotations

from io import StringIO
from bisect import bisect_right

from prompt_toolkit.lexers import Lexer
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.utils import get_cwidth
from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.theme import Theme as RichTheme

from .terminal import safe_text


def fit(text: str, width: int, unicode: bool = True) -> str:
    """Clip by terminal cells, including wide glyphs and combining characters."""
    text = safe_text(text, unicode=unicode).replace('\n', ' ')
    if get_cwidth(text) <= width:
        return text
    suffix = '…' if unicode else '~'
    available = max(0, width - 1)
    kept = ''
    for char in text:
        if get_cwidth(kept + char) > available:
            break
        kept += char
    return kept + suffix if width > 0 else ''


def color_depth(caps):
    return {'none': ColorDepth.DEPTH_1_BIT, 'ansi': ColorDepth.DEPTH_4_BIT,
            'color8': ColorDepth.DEPTH_4_BIT, 'color256': ColorDepth.DEPTH_8_BIT,
            'truecolor': ColorDepth.DEPTH_24_BIT}[caps.color_depth]


def _style(style, colored: bool) -> str:
    if style is None:
        return ''
    parts = []
    if colored:
        for attr, prefix in (('color', 'fg:'), ('bgcolor', 'bg:')):
            color = getattr(style, attr)
            if color and color.type.name != 'DEFAULT':
                rgb = color.get_truecolor()
                parts.append(prefix + rgb.hex)
    for flag in ('bold', 'italic', 'underline', 'reverse', 'strike'):
        if getattr(style, flag, False):
            parts.append(flag)
    return ' '.join(parts)


class RenderedLexer(Lexer):
    """A lexer over an already laid-out document; preserves cursor positions."""
    def __init__(self):
        self.lines = [[]]

    def lex_document(self, document):
        lines = self.lines
        return lambda number: lines[number] if number < len(lines) else []


class TerminalInputProcessor(Processor):
    """Adapt only the displayed input; keep the user's exact payload intact."""
    def __init__(self, unicode_enabled):
        self.unicode_enabled = unicode_enabled

    def apply_transformation(self, transformation_input):
        fragments, offsets = [], [0]
        for style, text, *rest in transformation_input.fragments:
            rendered = ''
            for char in text:
                adapted = safe_text(char, unicode=self.unicode_enabled())
                rendered += adapted
                offsets.append(offsets[-1] + len(adapted))
            fragments.append((style, rendered, *rest))
        return Transformation(
            fragments,
            source_to_display=lambda position: offsets[min(position,len(offsets)-1)],
            display_to_source=lambda position:max(0,bisect_right(offsets,position)-1))


def render_document(source: str, width: int, theme, caps, *, journal=False):
    """Render Markdown/code/diffs once per changed body or terminal width.

    Rich segments are converted directly, so no ANSI/OSC stream is interpreted.
    Body storage and the streaming tail are bounded by the workspace, separately
    from the lossless conversation archive.
    """
    source = safe_text(source, unicode=caps.unicode)
    palette = theme.palette
    styles = {
        'markdown.h1': f'bold {palette.primary}',
        'markdown.h2': f'bold {palette.secondary}',
        'markdown.h3': f'bold {palette.primary}',
        'markdown.code': f'{palette.accent}',
        'markdown.code_block': palette.text,
        'markdown.link': f'underline {palette.secondary}',
        'markdown.block_quote': palette.muted,
    }
    systems = {'ansi': 'standard', 'color8': 'standard', 'color256': '256', 'truecolor': 'truecolor'}
    console = Console(file=StringIO(), width=max(20, width), force_terminal=False,
                      color_system=systems.get(caps.color_depth), no_color=not caps.color,
                      theme=RichTheme(styles), highlight=False, legacy_windows=False)
    content = (Syntax(source, 'json', theme=theme.scan_style, background_color='default', word_wrap=True)
               if journal else Markdown(source, code_theme=theme.scan_style, hyperlinks=False))
    lines = []
    for segments in console.render_lines(content, pad=False):
        line = []
        for segment in segments:
            if not segment.control:
                text = safe_text(segment.text, unicode=caps.unicode)
                line.append((_style(segment.style, caps.color), text))
        # Rich pads code blocks. Keeping that padding under a read-only cursor
        # makes prompt-toolkit horizontally scroll every line to the right.
        while line and not line[-1][1].rstrip():
            line.pop()
        if line:
            style,text = line[-1]
            line[-1] = (style,text.rstrip())
        lines.append(line)
    text = '\n'.join(''.join(fragment[1] for fragment in line) for line in lines)
    return text, lines
