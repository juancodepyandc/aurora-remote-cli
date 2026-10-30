"""Launch an optional local renderer, without installing or replaying work."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import click

from . import display


def native_binary():
    explicit = os.environ.get('JOBIA_NATIVE_BIN')
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    from .renderer_install import managed_binary
    managed = managed_binary()
    if managed.is_file() and os.access(managed, os.X_OK):
        return str(managed)
    name = 'aurora-native.exe' if os.name == 'nt' else 'aurora-native'
    source = Path(__file__).resolve().parents[1]/'native'/'target'/'release'/name
    if source.is_file() and os.access(source, os.X_OK):
        return str(source)
    return shutil.which(name)


def launch_native(mode=None):
    """Return False only before launch when Python fallback is appropriate."""
    mode = mode or os.environ.get('JOBIA_RENDERER', 'auto')
    if mode not in {'auto','native','python'}:
        raise click.ClickException('JOBIA_RENDERER doit valoir auto, native ou python.')
    if mode == 'python':
        return False
    binary = native_binary()
    if binary is None:
        if mode == 'auto':
            if os.environ.get('JOBIA_NATIVE_BIN'):
                display.warning('JOBIA_NATIVE_BIN ne désigne aucun exécutable. Interface Python activée ; '
                                'jobia renderer install prépare le renderer natif.')
            return False
        raise click.ClickException(
            'Renderer natif absent. Lancez jobia renderer install (sans Cargo), '
            'ou utilisez jobia ui --renderer python. Compilation facultative : cargo build --release --locked '
            '--manifest-path native/Cargo.toml ; définissez JOBIA_NATIVE_BIN vers le binaire '
            'compilé.')
    caps = display.view.caps
    env = os.environ.copy()
    env.update(JOBIA_THEME=display.view.theme.id, JOBIA_COLOR=caps.color_depth,
               JOBIA_ANIM=caps.animation, JOBIA_UNICODE='1' if caps.unicode else '0',
               JOBIA_RENDER_TTY='1')
    # Preserve an explicit color policy over NO_COLOR as in the CLI parent.
    if caps.color_depth != 'none':
        env.pop('NO_COLOR',None)
    try:
        # POSIX can replace this launcher: retain only Rust + the Python engine.
        # No second mission dispatch is attempted if the renderer later exits.
        if sys.platform != 'win32':
            os.execve(binary,[binary,'--backend',sys.executable],env)
            return True
        result = subprocess.run([binary,'--backend',sys.executable],env=env,check=False)
    except OSError as exc:
        raise click.ClickException('Démarrage du renderer impossible : '+str(exc)) from exc
    if result.returncode:
        raise click.ClickException(
            f'Renderer interrompu (code {result.returncode}). Aucune mission relancée. '
            'Consultez jobia missions list avant une reprise explicite.')
    return True
