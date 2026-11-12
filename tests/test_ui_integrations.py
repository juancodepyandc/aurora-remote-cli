"""Skills and MCP are usable from the interface, locally and without a bridge."""
import asyncio
import json
import sys
from pathlib import Path

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aurora_cli import mcp_registry, render_server, skills, ui
from aurora_cli.core.conversation import Conversation

FIXTURES = Path(__file__).parent / 'fixtures'


def make_skill(name, description, body='Étapes précises.', triggers=()):
    return skills.create(name, description, body, triggers=triggers)


def test_skill_activation_travels_with_local_and_remote_requests(monkeypatch):
    make_skill('tableurs', 'Spreadsheet expertise', '# Règles\nToujours vérifier les formules.', triggers=('excel',))
    sent = []

    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=Conversation.open(), input=pipe, output=DummyOutput())
            await app.submit('/skill tableurs')
            assert app.active_skills == ['tableurs']
            assert 'Skill actif' in app.active_toast()['text']
            augmented = app.request_text('Calcule la marge')
            assert augmented.startswith('[SKILL ACTIVÉ · tableurs]') and 'Toujours vérifier' in augmented
            assert augmented.endswith('[DEMANDE]\nCalcule la marge')
            sent.append(augmented)
            await app.submit('/skill tableurs')
            assert app.active_skills == [] and app.request_text('x') == 'x'
    asyncio.run(scenario())
    assert sent


def test_relevant_skill_is_suggested_not_forced(monkeypatch):
    make_skill('excel-pro', 'Spreadsheet expertise', triggers=('excel', 'tableur'))

    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=Conversation.open(), input=pipe, output=DummyOutput())
            async def fake_local(raw, automatic=False):
                app.event({'type': 'mission_complete', 'result': 'ok', 'status': 'completed'})
            app.local = fake_local
            from aurora_cli import config
            config.set('mode', 'local')
            await app.submit('Prépare un tableur excel des ventes')
            toast = app.active_toast()
            assert toast and '/skill excel-pro' in toast['text']
            assert app.active_skills == []
    asyncio.run(scenario())


def test_skills_and_mcp_views_list_local_inventories_and_import_from_the_ui(tmp_path, monkeypatch):
    make_skill('revue', 'Relecture de code')
    exported = tmp_path / 'claude.json'
    exported.write_text(json.dumps({'mcpServers': {'catalogue': {
        'command': sys.executable, 'args': [str(FIXTURES / 'mcp_catalog_server.py')]}}}))

    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=Conversation.open(), input=pipe, output=DummyOutput())
            await app.submit('/skills')
            assert app.page == 'skills' and 'revue' in app.body_source()
            await app.submit('/mcp import ' + str(exported))
            assert 'catalogue' in app.transcript
            assert mcp_registry.load_cache()['catalogue']['tools']
            await app.submit('/mcp')
            assert app.page == 'mcp' and '2 outils' in app.body_source()
            await app.submit('/mcp appel catalogue first {}')
            assert '"type": "text"' in app.transcript
            await app.submit('/mcp chercher review')
            assert 'catalogue:review' in app.transcript
            state = render_server.snapshot(app)['state']
            assert {'id': 'skills', 'label': 'Skills'} in state['tabs'] and {'id': 'mcp', 'label': 'MCP'} in state['tabs']
    monkeypatch.setattr(mcp_registry, 'registry_search', lambda *a, **k: [])
    asyncio.run(scenario())


def test_integration_commands_do_not_block_new_requests():
    assert ui.presentation_command('/skills import anthropics/skills')
    assert ui.presentation_command('/mcp refresh') and ui.presentation_command('/connect')
