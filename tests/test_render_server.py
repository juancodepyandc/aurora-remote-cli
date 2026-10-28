"""Protocol actions are bounded, explicit and preserve mission lifecycle."""
import asyncio
from io import StringIO
import json
from unittest.mock import AsyncMock

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aurora_cli import config,display,themes,ui
from aurora_cli.render_server import RenderServer,snapshot


def command(name,value=''):
    return {'version':1,'type':'command','command':name,'value':value}


def test_snapshot_has_no_credentials_and_shared_complete_catalogue(monkeypatch):
    config.set('server_url','https://login:secret@target.example:443/path?key=sensitive')
    config.set('api_key','never-render-this')
    with create_pipe_input() as pipe:
        app = ui.WorkspaceApp(history=[],input=pipe,output=DummyOutput())
        state = snapshot(app)['state']
    assert state['target']=='target.example:443'
    encoded = json.dumps(state)
    assert 'never-render-this' not in encoded and 'sensitive' not in encoded and 'login:' not in encoded
    assert len(state['themes'])>=8 and state['welcome']
    assert '/permissions' in [item['name'] for item in state['commands']]
    assert state['latency']=='n/d' and state['latency_kind']=='diagnostic HTTP'


def test_theme_preview_cancel_commit_and_unknown_protocol(monkeypatch):
    monkeypatch.setattr(display,'view',display.Display(theme=themes.get('otter')))
    config.set('theme','otter')
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[],input=pipe,output=DummyOutput())
            output = StringIO()
            server = RenderServer(app,output)
            await server.handle(command('theme_preview','phosphor'))
            assert display.view.theme.id=='phosphor' and config.get('theme')=='otter'
            await server.handle(command('theme_cancel'))
            assert display.view.theme.id=='otter'
            await server.handle(command('theme_preview','glacier'))
            await server.handle(command('theme_commit','glacier'))
            assert display.view.theme.id=='glacier' and config.get('theme')=='glacier'
            await server.handle({'version':99,'type':'command','command':'submit','value':'do work'})
            await server.handle(command('shell','touch forbidden'))
            assert app.task is None
            assert all(json.loads(line)['type']=='error' for line in output.getvalue().splitlines())
            server.close()
    asyncio.run(scenario())


def test_busy_quit_model_and_stop_do_not_change_mission(monkeypatch):
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[],input=pipe,output=DummyOutput())
            app.stop = AsyncMock()
            output = StringIO()
            server = RenderServer(app,output)
            app.task = asyncio.create_task(asyncio.sleep(10))
            app.models = ['observed']
            await server.handle(command('quit'))
            await server.handle(command('model','observed'))
            await server.handle(command('new'))
            assert not server.closed and config.get('default_model','')!='observed'
            await server.handle(command('stop'))
            await asyncio.sleep(0)
            app.stop.assert_awaited_once()
            app.task.cancel()
            await asyncio.gather(app.task,return_exceptions=True)
            await server.handle(command('quit'))
            assert server.closed and json.loads(output.getvalue().splitlines()[-1])['type']=='exit'
            server.close()
    asyncio.run(scenario())


def test_gateway_emits_mission_updates_and_coalesces_tokens():
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[],input=pipe,output=DummyOutput())
            server = RenderServer(app,StringIO())
            app.loop = asyncio.get_running_loop()
            for _ in range(200):
                app.event({'type':'token','content':'stream '})
            await asyncio.sleep(.17)
            messages = [json.loads(line) for line in server.output.getvalue().splitlines()]
            assert len(messages)<=2
            assert 'stream' in messages[-1]['state']['body']
            assert messages[-1]['state']['welcome'] is False
            app.event({'type':'reconnecting','attempt':2})
            server.emit()
            assert json.loads(server.output.getvalue().splitlines()[-1])['state']['connection']=='reconnexion'
            server.close()
    asyncio.run(scenario())


def test_oversized_line_closes_without_executing_a_following_fragment(monkeypatch):
    from aurora_cli.render_server import MAX_COMMAND_BYTES,serve
    monkeypatch.setattr(ui.WorkspaceApp,'discover',AsyncMock())
    submit = AsyncMock()
    monkeypatch.setattr(ui.WorkspaceApp,'submit',submit)
    stream = StringIO('x'*(MAX_COMMAND_BYTES+3)+'\n'+json.dumps(command('submit','must not run'))+'\n')
    output = StringIO()
    asyncio.run(serve(stream,output))
    submit.assert_not_awaited()
    assert json.loads(output.getvalue().splitlines()[-1])['type']=='error'


def test_native_startup_exposes_retained_mission_without_dispatch(monkeypatch):
    from aurora_cli.core.conversation import Conversation
    from aurora_cli.render_server import serve
    class History(list):
        session_id = 'retained-session'
        state = {'active_mission_id':'retained-mission',
                 'pending_request':{'request':'Retrouver mon travail'}}
    monkeypatch.setattr(Conversation,'open',lambda:History())
    monkeypatch.setattr(ui.WorkspaceApp,'discover',AsyncMock())
    submit = AsyncMock()
    monkeypatch.setattr(ui.WorkspaceApp,'submit',submit)
    output = StringIO()
    asyncio.run(serve(StringIO(json.dumps(command('quit'))+'\n'),output))
    first = json.loads(output.getvalue().splitlines()[0])['state']
    assert '/attach retained-mission' in first['body']
    assert 'format de reprise incomplet' in first['body']
    submit.assert_not_awaited()


def test_presentation_commands_keep_running_mission_and_block_new_work():
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[],input=pipe,output=DummyOutput())
            server = RenderServer(app,StringIO())
            mission = asyncio.create_task(asyncio.sleep(60))
            app.task = mission
            try:
                await server.handle(command('submit','/view journal'))
                await asyncio.sleep(.01)
                assert app.page == 'journal'
                assert app.task is mission and not mission.done()
                await server.handle(command('submit','Une autre mission'))
                assert app.task is mission and app.busy
                assert 'Une mission est active' in app.transcript
                app.health = 'Accès refusé : appareil à reconnecter'
                assert snapshot(app)['state']['health'] == app.health
            finally:
                mission.cancel()
                await asyncio.gather(mission,return_exceptions=True)
                server.close()
    asyncio.run(scenario())


def test_invalid_retry_restores_an_exact_draft_without_dispatch():
    class History(list):
        state = {'pending_request':{'request':'Vérifie le reçu 猫'}}
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=History(),input=pipe,output=DummyOutput())
            app._remote = lambda *a,**k: (_ for _ in ()).throw(AssertionError('No dispatch'))
            await app.submit('/retry')
            draft = snapshot(app)['state']['draft_restore']
            assert draft == {'revision':1,'text':'Vérifie le reçu 猫'}
            assert 'Vérifie le reçu 猫' in app.transcript
            assert 'pending_request' in app.history.state
    asyncio.run(scenario())
