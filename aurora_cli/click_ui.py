"""One presentation contract for Click help, parse errors and command failures.

Click remains responsible for parsing and exit codes. Only human-facing output
is rendered here; JSON, completion protocols and command return values are not
wrapped in terminal decoration.
"""
from __future__ import annotations

from dataclasses import replace
from io import StringIO
import sys

import click
from rich.text import Text

from . import brand, config, display, themes
from .core import capabilities


def _prepare_help(args):
    """Read global presentation flags before Click's eager --help callback."""
    options = {}
    args = list(args)
    index = 0
    while index < len(args):
        token = args[index]
        if token == '--' or not token.startswith('-'):
            break
        name, separator, value = token.partition('=')
        if name in {'--theme', '--color', '--animation'}:
            if not separator and index + 1 < len(args):
                index += 1
                value = args[index]
            options[name[2:]] = value
        index += 1
    caps = capabilities.detect()
    if options.get('color') in {'always', 'never'}:
        enabled = options['color'] == 'always'
        caps = replace(caps, color_depth='truecolor' if enabled else 'none', ansi_styles=enabled)
    if options.get('animation') in {'full', 'reduced', 'none'}:
        caps = replace(caps, animation=options['animation'] if caps.is_tty else 'none')
    theme, _ = themes.resolve(options.get('theme', ''), caps, config.get('theme', ''))
    display.bind(theme=theme, caps=caps)


def _help(command, ctx):
    view = display.view
    stream = StringIO()
    console = display.TerminalConsole(file=stream, width=max(40, view.console.width), unicode=view.caps.unicode,
                      color_system=view.console.color_system,
                      force_terminal=view.caps.color, no_color=not view.caps.color,
                      highlight=False)
    console.print(Text.assemble(
        (brand.APP_NAME + '  /  ', view.style('primary', bold=True)),
        (view.text(ctx.command_path), view.style('text', bold=True))))
    console.print(Text(view.text(command.help or command.short_help or ''), style=view.style('text')))
    console.print()
    console.print(Text(view.text(command.get_usage(ctx)), style=view.style('accent')))
    records = [parameter.get_help_record(ctx) for parameter in command.get_params(ctx)]
    rows = [(Text(view.text(record[0]), overflow='fold'), Text(view.text(record[1]), overflow='fold'))
            for record in records if record]
    if rows:
        console.print(view.table('OPTIONS', [('Option', 'primary'), ('Description', 'text')], rows))
    if isinstance(command, click.Group):
        rows = []
        for name in command.list_commands(ctx):
            child = command.get_command(ctx, name)
            if child is not None and not child.hidden:
                rows.append((name, child.get_short_help_str(limit=160)))
        if rows:
            console.print(view.table('COMMANDES', [('Commande', 'primary'), ('Description', 'text')], rows))
        console.print(Text(view.text(f'{ctx.command_path} COMMANDE --help pour les détails.'),
                           style=view.style('muted')))
    if command.epilog:
        console.print(Text(view.text(command.epilog), style=view.style('muted')))
    return stream.getvalue().rstrip('\n')


def _error(exc):
    view = display.view
    console = display.TerminalConsole(file=sys.stderr, width=view.console.width, unicode=view.caps.unicode,
                      color_system=view.console.color_system,
                      force_terminal=view.caps.color, no_color=not view.caps.color,
                      highlight=False)
    console.print(Text.assemble(
        (view.glyph('fail') + ' ERREUR  ', view.style('error', bold=True)),
        (view.text(exc.format_message()), view.style('text'))))
    if isinstance(exc, click.UsageError) and exc.ctx is not None:
        console.print(Text(view.text(exc.ctx.get_usage()), style=view.style('muted')))
        console.print(Text(view.text(f'{exc.ctx.command_path} --help pour les commandes et options.'),
                           style=view.style('muted')))


class AuroraCommand(click.Command):
    def get_help(self, ctx):
        ctx.color = display.view.caps.color
        return _help(self, ctx)


class AuroraGroup(click.Group, AuroraCommand):
    command_class = AuroraCommand
    group_class = type

    def make_context(self, info_name, args, parent=None, **extra):
        if parent is None:
            _prepare_help(args)
            extra.setdefault('color', display.view.caps.color)
        return super().make_context(info_name, args, parent=parent, **extra)

    def main(self, *args, standalone_mode=True, **kwargs):
        if not standalone_mode:
            return super().main(*args, standalone_mode=False, **kwargs)
        try:
            result = super().main(*args, standalone_mode=False, **kwargs)
        except click.ClickException as exc:
            _error(exc)
            raise SystemExit(exc.exit_code) from exc
        except click.Abort as exc:
            _error(click.ClickException('Action annulée.'))
            raise SystemExit(1) from exc
        except Exception as exc:
            _error(click.ClickException(f'{type(exc).__name__} : {exc}'))
            raise SystemExit(1) from exc
        raise SystemExit(result if isinstance(result, int) else 0)


class ThemeGroup(AuroraGroup):
    """Keep `theme otter` as a shortcut for the explicit `theme set otter`."""
    def resolve_command(self, ctx, args):
        if args and args[0] not in self.commands and not args[0].startswith('-'):
            args.insert(0, 'set')
        return super().resolve_command(ctx, args)
