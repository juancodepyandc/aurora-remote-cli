"""Safe human terminal text, independent from renderer or terminal protocol."""
from __future__ import annotations

import unicodedata
from urllib.parse import urlsplit

from .observations import terminal_text

_ASCII = str.maketrans({
    '─':'-', '━':'=', '│':'|', '┃':'|', '╭':'+', '╮':'+', '╰':'+', '╯':'+',
    '┌':'+', '┐':'+', '└':'+', '┘':'+', '├':'+', '┤':'+', '┼':'+', '┬':'+', '┴':'+',
    '═':'=', '║':'|', '╔':'+', '╗':'+', '╚':'+', '╝':'+',
    '→':'->', '←':'<-', '↔':'<->', '↳':'>', '↻':'~', '›':'>', '❯':'>',
    '✓':'+', '✔':'+', '✖':'x', '×':'x', '▲':'!', '●':'*', '○':'o', '◉':'*',
    '◆':'*', '◇':'o', '◈':'*', '•':'-', '·':'.', '…':'...', '—':'--', '–':'-',
    '‘':"'", '’':"'", '“':'"', '”':'"', '≈':'~', '≥':'>=', '≤':'<=',
})


def ascii_text(value: str) -> str:
    normalized = unicodedata.normalize('NFKD', value.translate(_ASCII))
    return ''.join(c for c in normalized if not unicodedata.combining(c)).encode('ascii', 'replace').decode('ascii')


def safe_text(value, *, unicode: bool = True) -> str:
    text = terminal_text(value)
    return text if unicode else ascii_text(text)


def target_label(url: str) -> str:
    """Never display credentials, paths, query strings or fragments in a HUD."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        port = parsed.port
        return safe_text(host + (f':{port}' if port else '')) or 'non configurée'
    except ValueError:
        return 'adresse invalide'
