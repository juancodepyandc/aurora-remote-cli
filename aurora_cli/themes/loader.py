"""Validated, packaged JSON themes. No executable theme hooks."""
from __future__ import annotations

import json
from pathlib import Path

from .base import BannerSpec, Components, Decoration, Glyphs, Motion, Palette, Spacing, Theme


def load_theme(name: str) -> Theme:
    return load_path(Path(__file__).with_name('data') / (name + '.json'))


def load_path(path: Path) -> Theme:
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.pop('schema', None) != 1:
        raise ValueError(f'Unsupported theme schema: {path.name}')
    for key in ('id', 'label', 'description'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f'Missing theme {key}: {path.name}')
    data['palette'] = Palette(**data.get('palette', {}))
    glyphs = data.get('glyphs', {})
    data['glyphs'] = Glyphs(**{k: tuple(v) if isinstance(v, list) else v for k,v in glyphs.items()})
    banner = data.get('banner', {})
    banner['decorations'] = tuple(Decoration(**d) for d in banner.get('decorations', []))
    banner['frames'] = tuple(banner.get('frames', []))
    data['banner'] = BannerSpec(**banner)
    data['motion'] = Motion(**data.get('motion', {}))
    spacing = Spacing(**data.get('spacing', {}))
    if any(not isinstance(value, int) or not 0 <= value <= 48 for value in vars(spacing).values()):
        raise ValueError(f'Invalid spacing: {path.name}')
    data['spacing'] = spacing
    components = data.get('components', {})
    for key in ('pixel_art', 'pixel_palette', 'wordmark', 'ornament'):
        components[key] = tuple(components.get(key, []))
    if components.get('composition', 'split') not in {
            'split', 'reverse', 'center', 'horizon', 'editorial', 'console', 'poster', 'gallery'}:
        raise ValueError(f'Invalid composition: {path.name}')
    for key in ('wordmark', 'ornament'):
        rows = components[key]
        if len(rows) > 8 or any(not isinstance(row, str) or len(row) > 80 or
                                any(ord(c) < 32 or ord(c) == 127 for c in row) for row in rows):
            raise ValueError(f'Invalid {key}: {path.name}')
    data['components'] = Components(**components)
    return Theme(**data)


def bundled_themes() -> tuple[Theme, ...]:
    directory = Path(__file__).with_name('data')
    themes = [load_path(p) for p in sorted(directory.glob('*.json'))]
    themes.sort(key=lambda theme: (theme.id != 'otter', theme.id))
    return tuple(themes)
