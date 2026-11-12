"""The linear REPL survives every advertised command (audit regressions)."""
from unittest.mock import Mock

from aurora_cli import config, interactive


def run_commands(monkeypatch, capsys, commands):
    answers = iter(commands)

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        def prompt(self, *args, **kwargs):
            try:
                return next(answers)
            except StopIteration:
                raise EOFError from None

    monkeypatch.setattr(interactive, 'PromptSession', Session)
    client = Mock()
    client.server_url = 'https://pc.example'
    client.status.return_value = {'ok': True, 'bridge': True, 'ollama': True, 'models_count': 2,
                                  'hardware': {'gpu': 'RTX', 'ram_gb': 32}}
    client.session_list.return_value = {'ok': True, 'sessions': []}
    client.session_create.return_value = {'ok': True, 'session_id': 's-1', 'permissions': 'FULL'}
    client.permissions_set.return_value = {'ok': True}
    interactive.run_interactive(client)
    return capsys.readouterr().out, client


def test_modules_agi_status_and_permissions_do_not_crash(monkeypatch, capsys):
    out, client = run_commands(monkeypatch, capsys, ['/modules', '/agi vérifier', '/status', '/permissions SAFE'])
    assert 'MODULES' in out and 'Conversation' in out
    assert 'Analyse' in out
    assert '[#' not in out  # no literal Rich markup in /status
    client.permissions_set.assert_called_once_with('SAFE')
    assert config.get('default_permissions') == 'SAFE'
