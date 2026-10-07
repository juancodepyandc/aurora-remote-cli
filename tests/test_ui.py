"""Full-screen input, real transcript persistence and structured mission events."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aurora_cli import config, ui, workspace
from aurora_cli.bridge import Bridge
from aurora_cli.cli import main


class FakeBridge:
    server_url = 'http://localhost:3001'
    accepted = []
    closed = 0
    def __init__(self,**kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self,*args):
        type(self).closed += 1
    def mission_start(self,request,**kwargs):
        self.accepted.append((request,kwargs))
        return {'ok':True,'mission_id':'mis_test'}
    def mission_stream(self,mid,last_event_id=0):
        yield {'type':'plan','steps':['Inspect actual file'],'criteria':['File tested']}
        yield {'type':'tool_result','tool':'verify','id':'ev_actual','ok':True}
        yield {'type':'model_metrics','tokens_per_second':12.5}
        yield {'type':'mission_complete','result':'Verified response','verification':{'verified':['File tested']}}
    def mission_status(self,mid):
        return {'ok':True,'id':mid,'status':'running','plan':[],'criteria':[],'evidence':[]}
    def doctor(self):
        return {'ok':True,'ready':True}
    def models(self):
        return {'models':[{'name':'observed:local'}]}
    def mission_stop(self,mid):
        return {'ok':True,'status':'stopping'}
    def missions_list(self):
        return {'ok':True,'missions':[]}


def test_pipe_ui_exits_cleanly_without_entering_alternate_screen(monkeypatch):
    monkeypatch.setenv('JOBIA_COLOR','never')
    result = CliRunner().invoke(main,['ui'])
    assert result.exit_code==0,result.output
    assert 'terminal interactif' in result.output
    assert '\x1b[' not in result.output


def test_terminal_control_sequences_are_not_rendered_from_tool_output():
    assert ui.plain('\x1b[31mresult\x1b[0m\x00\x9b')=='result'


def test_no_quality_or_progress_is_invented_without_events():
    state = ui.MissionView()
    assert state.status=='idle'
    assert not state.metrics and not state.evidence and not state.verified
    state.consume({'type':'error','message':'failure'})
    assert state.status=='failed'
    state.consume({'type':'mission_complete','status':'blocked','result':'Missing tool'})
    assert state.status=='blocked'


def test_heartbeat_does_not_claim_work_is_advancing(monkeypatch):
    monkeypatch.setattr(ui.time, 'monotonic', lambda: 100)
    with create_pipe_input() as pipe:
        app = ui.WorkspaceApp(history=[], input=pipe, output=DummyOutput())
        app.state = ui.MissionView(status='running', started=50, last_progress=60)
        app.event({'type': 'heartbeat'})
        assert app.state.last_progress == 60
        text = ''.join(text for _,text in app.activity_text())
        assert 'Aucune avancée reçue depuis 40s' in text
        assert 'connexion vivante' in text
        assert app.state.activity == []
        app.event({'type': 'error', 'message': 'Timeout on reading data from socket'})
        assert 'Mission en échec' in ''.join(t for _,t in app.activity_text())
        assert 'Timeout on reading' in app.transcript


def test_tokens_are_visible_batched_bounded_and_not_committed_before_completion():
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[], input=pipe, output=DummyOutput())
            refresh = Mock(wraps=app.refresh_body)
            app.refresh_body = refresh
            for _ in range(2000):
                app.event({'type': 'token', 'content': 'visible output '})
            assert refresh.call_count == 0
            assert app.state.received_chars == 30000
            assert len(app.partial) == 16000
            assert app.history == []
            await asyncio.sleep(.15)
            assert refresh.call_count == 1
            assert 'visible output' in app.body.text
            app.event({'type': 'mission_complete', 'result': 'Final verified answer'})
            assert not app.partial
            assert 'visible output' not in app.body.text
            assert app.history == [{'role': 'assistant', 'content': 'Final verified answer'}]
            await asyncio.sleep(.15)
    asyncio.run(scenario())


def test_journal_refreshes_while_open_and_partial_output_survives_error():
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[], input=pipe, output=DummyOutput())
            app.page = 'journal'
            app.event({'type': 'tool_start', 'tool': 'read_file'})
            await asyncio.sleep(.15)
            assert 'read_file' in app.body.text
            app.page = 'conversation'
            app.event({'type': 'token', 'content': 'Partial result'})
            app.event({'type': 'error', 'message': 'Connection lost'})
            assert 'Partial result' in app.body.text
            assert 'résultat non encore confirmé' in app.body.text
            assert app.history == []
            await asyncio.sleep(.15)
    asyncio.run(scenario())


@pytest.mark.parametrize('columns,rows', [(72,24),(120,40)])
def test_real_terminal_layout_keeps_activity_visible_at_both_sizes(columns, rows):
    from prompt_toolkit.data_structures import Size
    class SizedOutput(DummyOutput):
        def get_size(self):
            return Size(rows=rows, columns=columns)
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[], input=pipe, output=SizedOutput())
            async def discover():
                pass
            app.discover = discover
            app.state = ui.MissionView(status='running', started=ui.time.monotonic())
            app.event({'type':'plan', 'steps':['Inspecter le projet', 'Tester'], 'criteria':[]})
            app.event({'type':'tool_start', 'tool':'read_file'})
            running = asyncio.create_task(app.run())
            await asyncio.sleep(.15)
            screen = app.app.renderer._last_screen
            rendered = '\n'.join(''.join(screen.data_buffer[y][x].char for x in range(columns)) for y in range(rows))
            assert 'Activité observée' in rendered
            assert 'Outil en cours' in rendered and 'read_file' in rendered
            assert 'Inspecter le projet' in rendered
            assert 'Demande' in rendered
            app.app.exit()
            await running
    asyncio.run(scenario())


def test_full_screen_keyboard_submits_and_quits_without_loading_a_model(monkeypatch):
    FakeBridge.accepted,FakeBridge.closed = [],0
    config.set('mode','remote')
    config.set('default_permissions','AUTONOMOUS')
    config.set('api_key','test')
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(bridge_factory=FakeBridge,input=pipe,output=DummyOutput())
            running = asyncio.create_task(app.run())
            await asyncio.sleep(.03)
            pipe.send_text('Inspect the file\r')
            async def finished():
                while app.state.status!='completed' or app.busy:
                    await asyncio.sleep(.01)
            await asyncio.wait_for(finished(),3)
            assert app.state.criteria==['File tested']
            assert app.state.verified==['File tested']
            assert app.state.metrics['tokens_per_second']==12.5
            assert 'Verified response' in app.transcript
            pipe.send_text('\x11')  # Ctrl+Q
            await asyncio.wait_for(running,2)
            from aurora_cli.core.conversation import Conversation
            persisted = Conversation.open()
            assert persisted[-1]['content']=='Verified response'
            assert FakeBridge.accepted[0][1]['idempotency_key']
            assert FakeBridge.closed>=1
    asyncio.run(scenario())


def test_new_conversation_does_not_delete_previous_transcript():
    from aurora_cli.core.conversation import Conversation
    history = Conversation.open()
    history.append({'role':'user','content':'Preserve me'})
    history.save()
    previous = history.root/f'{history.session_id}.json'
    with create_pipe_input() as pipe:
        app = ui.WorkspaceApp(history=history,input=pipe,output=DummyOutput())
        app.new_conversation()
    assert previous.exists()
    assert 'Preserve me' in previous.read_text()
    assert not app.history


def test_clear_resets_ui_and_persisted_history_without_contacting_bridge():
    from aurora_cli.core.conversation import Conversation
    history = Conversation.open()
    history.append({'role': 'user', 'content': 'Old conversation'})
    history.state['pending_request'] = 'Old pending request'
    history.save()
    bridge = Mock(side_effect=AssertionError('No remote call allowed'))
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=history, bridge_factory=bridge, input=pipe, output=DummyOutput())
            app.journal = 'Old logs'
            app.page = 'journal'
            app.state.id = 'mis_old'
            await app.submit('/clear')
            assert not app.history
            assert app.page == 'conversation' and app.journal == '' and app.state.id == ''
            assert 'Old conversation' not in app.transcript
            assert '1 conversation(s)' in app.transcript
            assert 'pending_request' not in Conversation.open().state
            bridge.assert_not_called()
    asyncio.run(scenario())


def test_clear_cli_all_cleans_multiple_projects(tmp_path):
    from aurora_cli.core.conversation import Conversation
    Conversation.open(project=tmp_path / 'a')
    Conversation.open(project=tmp_path / 'b')
    result = CliRunner().invoke(main, ['clear', '--all'])
    assert result.exit_code == 0, result.output
    assert '2 conversation(s)' in result.output
    assert 'Sauvegarde' in result.output


def test_bridge_preserves_accepted_id_when_dispatch_fails(monkeypatch):
    import httpx
    client = Bridge(server_url='http://localhost',api_key='test')
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(
        503,json={'ok':False,'mission_id':'mis_retained','error':'daemon unavailable'})),base_url='http://localhost')
    with client:
        result = client.mission_start('Request',idempotency_key='stable-key')
    assert not result['ok']
    assert result['mission_id']=='mis_retained'
    assert result['error']=='daemon unavailable'


def test_windows_install_version_is_detected(tmp_path):
    from aurora_cli.install import Target,installed_version
    venv = tmp_path/'venv'
    (venv/'Lib/site-packages/jobia_cli-1.3.0.dist-info').mkdir(parents=True)
    target = Target('windows',tmp_path,venv,venv/'Scripts',venv/'Scripts')
    assert installed_version(target)=='1.3.0'


def test_api_key_environment_override_reaches_the_real_http_client(monkeypatch):
    monkeypatch.setenv('JOBIA_API_KEY','environment-key-for-test')
    with Bridge(server_url='http://localhost') as client:
        assert client._client.headers['Authorization']=='Bearer environment-key-for-test'


def test_worker_plan_cannot_replace_the_parent_mission_criteria():
    state = ui.MissionView()
    state.consume({'type':'plan','steps':['Parent task'],'criteria':['Parent criterion']})
    state.consume({'type':'plan','worker':True,'steps':['Worker task'],'criteria':['Worker criterion']})
    assert state.plan==['Parent task'] and state.criteria==['Parent criterion']


def test_uncertain_acceptance_retries_the_identical_saved_request(monkeypatch):
    config.set('mode','remote')
    calls = []
    class LostReply(FakeBridge):
        def mission_start(self,request,**kwargs):
            calls.append((request,kwargs))
            if len(calls)==1:
                raise RuntimeError('Response lost after acceptance')
            return {'ok':True,'mission_id':'mis_original','replayed':True}
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(bridge_factory=LostReply,input=pipe,output=DummyOutput())
            await app.submit('Exact original task')
            await asyncio.sleep(.01)
            saved = dict(app.history.state['pending_request'])
            # Reopen the on-disk archive, as happens after restarting jobia.
            from aurora_cli.core.conversation import Conversation
            app.history = Conversation.open()
            config.set('default_model','different:local')
            await app.submit('/retry')
            await asyncio.sleep(.01)
            assert calls[0]==calls[1]
            assert calls[1][1]['idempotency_key']==saved['idempotency_key']
            assert calls[1][1]['history']==[]
            assert [m['content'] for m in app.history if m['role']=='user']==['Exact original task']
            assert 'pending_request' not in app.history.state
    asyncio.run(scenario())


@pytest.mark.parametrize('pending', [
    'Original task', '', ['Original task'], 42,
    {'request': 'Original task'},
    {'request': 'Original task', 'workspace': '', 'permissions': 'SAFE',
     'model': '', 'idempotency_key': ''},
    {'request': 'Original task', 'workspace': '', 'permissions': 'SAFE',
     'model': '', 'idempotency_key': 'original', 'history': 'invalid'},
])
def test_retry_incomplete_archive_never_crashes_or_replays(pending):
    from aurora_cli.core.conversation import Conversation
    history = Conversation.open()
    history.state['pending_request'] = pending
    history.save()
    archive = history.root / f'{history.session_id}.json'
    before = archive.read_bytes()
    forbidden_bridge = Mock(side_effect=AssertionError('Must not contact the server'))
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=Conversation.open(), bridge_factory=forbidden_bridge,
                                  input=pipe, output=DummyOutput())
            await app.submit('/retry')
            assert 'aucune mission lancée' in app.transcript
            request = pending if isinstance(pending, str) else pending.get('request', '') if isinstance(pending, dict) else ''
            assert app.prompt.text == request
            assert app.history.state['pending_request'] == pending
            forbidden_bridge.assert_not_called()
    asyncio.run(scenario())
    assert archive.read_bytes() == before


@pytest.mark.parametrize('doctor, expected', [
    ({'ok': True, 'ready': True}, 'Pont prêt'),
    ({'ok': True, 'ready': False, 'checks': [
        {'name': 'Daemon', 'ok': False, 'detail': 'indisponible'}]}, 'Daemon: indisponible'),
    ({'ok': False, 'error': 'HTTP error 530'}, 'HTTP error 530'),
])
def test_diagnostic_shows_actual_endpoint_readiness_and_local_mode(doctor, expected):
    config.set('server_url', 'https://configured.example')
    config.set('api_key', 'test')
    config.set('mode', 'local')
    class DiagnosticBridge(FakeBridge):
        def doctor(self):
            return doctor
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(bridge_factory=DiagnosticBridge, input=pipe, output=DummyOutput())
            await app.discover()
            assert expected in app.health
            assert 'https://configured.example' in app.health
            assert 'Mode local actif' in app.health
            if doctor.get('ready') is False:
                assert 'Pont prêt' not in app.health
    asyncio.run(scenario())


def test_remote_followup_keeps_prior_turns_separate_from_the_current_goal():
    config.set('mode','remote')
    FakeBridge.accepted = []
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(bridge_factory=FakeBridge,input=pipe,output=DummyOutput())
            await app.submit('Initial exact task')
            await asyncio.sleep(.02)
            await app.submit('Refine the previous result')
            await asyncio.sleep(.02)
            request,payload = FakeBridge.accepted[-1]
            assert request=='Refine the previous result'
            assert payload['history']==[{'role':'user','content':'Initial exact task'},
                                        {'role':'assistant','content':'Verified response'}]
    asyncio.run(scenario())
