"""Small theme sprites: native terminal images with Unicode/ASCII fallbacks.

Only bundled declarative pixels are encoded. No files, URLs, terminal queries,
image decoders, subprocesses or runtime dependencies are involved.
"""
from __future__ import annotations

import base64
import struct
import zlib

from rich.text import Text

from .core.color import to_rgb


def pixels(theme, scale: int = 1):
    palette = [to_rgb(c) for c in theme.components.pixel_palette]
    rows = theme.components.pixel_art
    if not rows or not palette:
        return []
    width = max(map(len, rows))
    result = []
    for row in rows:
        cells = [palette[int(c, 16)] for c in row.ljust(width, '0')]
        scaled = [color for color in cells for _ in range(scale)]
        result.extend([scaled] * scale)
    return result


def png(theme) -> bytes:
    rows = pixels(theme, scale=8)
    if not rows:
        return b''
    def chunk(kind, content):
        return struct.pack('!I', len(content)) + kind + content + struct.pack('!I', zlib.crc32(kind + content))
    raw = b''.join(b'\0' + bytes(c for rgb in row for c in rgb) for row in rows)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', len(rows[0]), len(rows), 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def image_sequence(theme, protocol: str) -> str:
    """One bounded image, with no permanent storage in the terminal."""
    encoded = base64.b64encode(png(theme)).decode('ascii')
    if not encoded:
        return ''
    if protocol == 'kitty':
        chunks = [encoded[i:i+4096] for i in range(0, len(encoded), 4096)]
        return ''.join('\x1b_G' + ('a=T,f=100,t=d,q=2,c=16,r=8,' if i == 0 else '')
                       + f'm={int(i < len(chunks)-1)};' + chunk + '\x1b\\'
                       for i, chunk in enumerate(chunks))
    if protocol == 'iterm2':
        return '\x1b]1337;File=inline=1;width=16;height=8;preserveAspectRatio=1:' + encoded + '\x07'
    if protocol == 'sixel':
        rows = pixels(theme, scale=4)
        colors = list(dict.fromkeys(rgb for row in rows for rgb in row))
        result = ['\x1bPq', f'"1;1;{len(rows[0])};{len(rows)}']
        for i, rgb in enumerate(colors):
            result.append(f'#{i};2;' + ';'.join(str(round(c*100/255)) for c in rgb))
        for y in range(0, len(rows), 6):
            for i, rgb in enumerate(colors):
                result.append(f'#{i}')
                for x in range(len(rows[0])):
                    mask = sum(1 << dy for dy in range(min(6, len(rows)-y)) if rows[y+dy][x] == rgb)
                    result.append(chr(63 + mask))
                result.append('$')
            result.append('-')
        return ''.join(result) + '\x1b\\'
    return ''


def block_art(theme, caps) -> Text:
    if not caps.unicode:
        return Text(theme.banner.art_ascii)
    if not caps.color:
        return Text(theme.banner.art)
    rows = pixels(theme)
    result = Text()
    for y in range(0, len(rows), 2):
        for x, upper in enumerate(rows[y]):
            lower = rows[min(y+1, len(rows)-1)][x]
            foreground = '#%02x%02x%02x' % upper
            background = '#%02x%02x%02x' % lower
            result.append('▀', style=foreground + ' on ' + background)
        result.append('\n')
    return result


def preview(view, theme) -> None:
    protocol = view.caps.image_protocol
    if protocol != 'none':
        view.console.file.write(image_sequence(theme, protocol) + '\n')
        view.console.file.flush()
    else:
        view.console.print(block_art(theme, view.caps))
