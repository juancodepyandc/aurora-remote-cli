"""Human command surfaces share a renderer; machine output remains unwrapped."""
from dataclasses import replace
from io import StringIO
import json
from unittest.mock import Mock, MagicMock

import click
from click.testing import CliRunner
import pytest

from aurora_cli import config, display, themes
from aurora_cli.cli import main
from aurora_cli.core import capabilities


def command_paths(group, prefix=()):
    yield prefix
    for name, command in group.commands.items():
        path = (*prefix, name)
        if isinstance(command, click.Group):
            yield from command_paths(command, path)
        else:
            yield path


@pytest.mark.parametrize('width', [60, 80, 120, 200])
@pytest.mark.parametrize('path', list(command_paths(main)), ids=lambda p: ' '.join(p) or 'root')
def test_every_command_help_uses_design_at_supported_widths(path, width, monkeypatch):
    monkeypatch.setenv('COLUMNS', str(width))
    monkeypatch.setenv('JOBIA_UNICODE', '0')
    result = CliRunner().invoke(main, ['--color', 'never', *path, '--help'])
    assert result.exit_code == 0, result.output
    assert 'JOBIA' in result.output and 'OPTIONS' in result.output
    assert '\x1b' not in result.output
    assert result.output.isascii()
    assert max(map(len, result.output.splitlines())) <= width


@pytest.mark.parametrize('args', [
    ['unknown-command'], ['mission'], ['mission', 'test', '--permissions', 'invalid'],
    ['theme', 'set', 'unknown-theme'], ['export3d', 'missing-file.glb'],
])
def test_usage_errors_share_style_and_keep_failure_exit(args):
    result = CliRunner().invoke(main, ['--color', 'never', *args])
    assert result.exit_code == 2
    assert 'ERREUR' in result.stderr and 'Traceback' not in result.output
    assert '\x1b' not in result.output


def test_help_honors_early_theme_and_color_flags(monkeypatch):
    monkeypatch.setenv('NO_COLOR', '1')
    runner = CliRunner()
    never = runner.invoke(main, ['--theme', 'aurora', '--color', 'never', '--help'])
    always = runner.invoke(main, ['--theme', 'aurora', '--color', 'always', '--help'])
    assert never.exit_code == always.exit_code == 0
    assert '\x1b[' not in never.output
    assert '\x1b[' in always.output


def test_theme_list_preview_set_and_legacy_shortcut(monkeypatch):
    runner = CliRunner()
    names = themes.theme_ids()
    result = runner.invoke(main, ['theme', 'list'])
    assert result.exit_code == 0
    assert len(names) >= 8 and all(name in result.output for name in names)
    config.set('theme', names[0])
    for name in names:
        result = runner.invoke(main, ['--color', 'never', 'theme', 'preview', name])
        assert result.exit_code == 0 and result.output.strip(), result.output
        assert config.get('theme') == names[0]
    assert runner.invoke(main, ['theme', 'set', names[-1]]).exit_code == 0
    assert config.get('theme') == names[-1]
    assert runner.invoke(main, ['theme', names[0]]).exit_code == 0
    assert config.get('theme') == names[0]


def test_machine_json_has_no_labels_or_escape_sequences(monkeypatch):
    from aurora_cli import cli
    client = MagicMock()
    client.__enter__.return_value = client
    expected = {'ok': True, 'missions': [{'id': 'm1', 'status': 'running', 'request': '[red]literal'}]}
    client.missions_list.return_value = expected
    monkeypatch.setattr(cli, '_client', lambda: client)
    result = CliRunner().invoke(main, ['--color', 'always', 'missions', 'list', '--json'])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == expected
    assert '\x1b' not in result.stdout and not result.stderr


def test_environment_diagnostic_cannot_expose_credential(monkeypatch):
    monkeypatch.setenv('JOBIA_API_KEY', 'private-fixture-key')
    result = CliRunner().invoke(main, ['--color', 'never', 'env'])
    assert result.exit_code == 0
    assert 'private-fixture-key' not in result.output and 'JOBIA_API_KEY' not in result.output


def _capture_display(monkeypatch, width=60):
    stream = StringIO()
    caps = replace(capabilities.detect(), is_tty=False, animation='none', unicode=False,
                   color_depth='none', ansi_styles=False)
    renderer = display.Display(display.TerminalConsole(file=stream, width=width, color_system=None, unicode=False), themes.get('plain'), caps)
    monkeypatch.setattr(display, 'view', renderer)
    return stream


def test_remote_stream_reconnect_is_visible_without_resubmission(monkeypatch, tmp_path):
    from aurora_cli.mission import run_mission
    stream = _capture_display(monkeypatch)
    client = Mock(server_url='https://bridge.test')
    client.mission_start.return_value = {'ok': True, 'mission_id': 'm-recover'}
    client.mission_stream.return_value = iter([
        {'type': 'tool_start', 'tool': 'read_file'},
        {'type': 'token', 'content': 'Partial [red]literal\x1b[2J'},
        {'type': 'reconnecting', 'attempt': 1},
        {'type': 'mission_complete', 'status': 'completed', 'result': 'Verified final answer'},
    ])
    assert run_mission(client, 'Inspect', workspace=str(tmp_path), session_id='session-test')
    client.mission_start.assert_called_once()
    rendered = stream.getvalue()
    assert 'm-recover' in rendered and 'session-test' in rendered and 'bridge.test' in rendered
    assert 'read_file' in rendered and 'Connexion interrompue' in rendered
    assert rendered.count('Partial [red]literal') == 1
    assert 'Verified final answer' in rendered and '\x1b' not in rendered
    assert rendered.isascii()


def test_blocked_mission_does_not_claim_success(monkeypatch, tmp_path):
    from aurora_cli.mission import run_mission
    stream = _capture_display(monkeypatch)
    client = Mock(server_url='https://bridge.test')
    client.mission_start.return_value = {'mission_id': 'blocked'}
    client.mission_stream.return_value = iter([{'type': 'mission_complete', 'status': 'blocked', 'result': 'Missing capability'}])
    assert not run_mission(client, 'Inspect', workspace=str(tmp_path))
    assert 'ATTENTION' in stream.getvalue() and 'Missing capability' in stream.getvalue()
    assert 'Mission terminee' not in stream.getvalue()


def test_remote_repl_stream_is_literal_once_and_keeps_target_context(monkeypatch):
    from aurora_cli import interactive
    stream = _capture_display(monkeypatch)
    prompts = iter(['Inspect', '/exit'])
    toolbars = []
    class Session:
        def __init__(self, **kwargs):
            pass
        def prompt(self, *args, **kwargs):
            toolbars.append(kwargs['bottom_toolbar'])
            return next(prompts)
    class Engine:
        def process_request(self, request, callback, **kwargs):
            callback('task', {'stream': True, 'data': 'Exactly once [red]\x1b[2J'})
            callback('task', {'stream_done': True, 'status': 'success', 'data': 'Exactly once [red]'})
    client = Mock(server_url='https://bridge.test')
    client.status.return_value = {'ok': True}
    client.session_list.return_value = {'sessions': []}
    client.session_create.return_value = {'session': {'id': 'session-ui'}}
    monkeypatch.setattr(interactive, 'PromptSession', Session)
    monkeypatch.setattr(interactive, 'jobia_engine', Engine())
    interactive.run_interactive(client)
    assert stream.getvalue().count('Exactly once [red]') == 1
    assert '\x1b' not in stream.getvalue()
    assert all('bridge.test' in line and 'session-ui' in line for line in toolbars)
