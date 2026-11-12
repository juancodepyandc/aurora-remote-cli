"""Shared visual layer: synchronized spinners, badges, palette and pairing card."""
import asyncio
import json
import time
from io import StringIO

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aurora_cli import config, render_server, skills, ui, visuals


def test_one_clock_drives_every_spinner_and_none_is_static():
    assert visuals.spinner('dots', unicode=True, animation='full', elapsed=0.0) == '⠋'
    assert visuals.spinner('dots', unicode=True, animation='full', elapsed=0.25) == '⠹'
    assert visuals.spinner('dots', unicode=True, animation='reduced', elapsed=0.25) == '⠙'
    assert visuals.spinner('wave', unicode=False, animation='none', elapsed=9.0) == '_'
    glyph, tone = visuals.badge('reconnecting', unicode=True, animation='full', elapsed=0.25, spinner_kind='dots')
    assert glyph == '⠹' and tone == 'warning'
    assert visuals.badge('offline', unicode=False, animation='full', elapsed=1, spinner_kind='dots') == ('o', 'error')


def test_big_pairing_digits_and_ascii_fallback():
    rows = visuals.big_code('482913', True)
    assert len(rows) == 3 and all(len(row) == len(rows[0]) for row in rows)
    assert visuals.big_code('482913', False) == ['4 8 2   9 1 3']
    assert visuals.progress_cells(1, 4, 8, True) == ('██', '░░░░░░')


def test_palette_reaches_commands_views_themes_skills_and_models():
    skills.create('tableurs', 'Feuilles de calcul')

    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[], input=pipe, output=DummyOutput())
            app.models = ['qwen3:8b']
            app.load_integrations(remote=False)
            kinds = {item['kind'] for item in visuals.palette_items(app)}
            assert {'commande', 'vue', 'thème', 'skill', 'modèle'} <= kinds
            app.open_palette()
            app.palette_input.text = 'tabl'
            assert app.palette_matches()[0]['label'] == 'tableurs'
            app.run_palette_selection()
            await asyncio.sleep(.05)
            assert app.active_skills == ['tableurs']
            app.open_palette()
            app.palette_input.text = 'glacier'
            app.run_palette_selection()
            assert config.get('theme') == 'glacier'
    asyncio.run(scenario())


def test_native_palette_protocol_and_snapshot_fields():
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[], input=pipe, output=DummyOutput())
            server = render_server.RenderServer(app, StringIO())
            try:
                await server.handle({'version': 1, 'type': 'command', 'command': 'palette', 'value': ''})
                state = render_server.snapshot(app, picker=server.picker)['state']
                assert server.picker == 'palette' and state['palette']
                assert {'connection_state', 'connection_label', 'ticking', 'tab_counts', 'active_skills'} <= state.keys()
                await server.handle({'version': 1, 'type': 'command', 'command': 'picker_cancel', 'value': ''})
                assert render_server.snapshot(app, picker=server.picker)['state']['palette'] is None
                assert app.animation_mode() == 'none'  # native animates locally
            finally:
                server.close()
    asyncio.run(scenario())


def test_pairing_card_and_header_badge_in_the_python_renderer():
    with create_pipe_input() as pipe:
        app = ui.WorkspaceApp(history=[], input=pipe, output=DummyOutput())
        app.connection = 'appairage'
        app.pairing = {'code': '123456', 'target': 'x.trycloudflare.com', 'expires_at': time.monotonic() + 61,
                       'status': ''}
        card = ''.join(text for _, text in app.pairing_fragments())
        assert 'Associer cet ordinateur' in card and 'Expire dans 1:0' in card
        header = ''.join(text for _, text in app.header())
        assert 'code 123 456' in header
        assert json.dumps(render_server.snapshot(app)['state']['pairing'])
