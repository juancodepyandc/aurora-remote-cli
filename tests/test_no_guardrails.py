"""Nothing blocks the user: requests queue, restarts resume, quitting detaches."""
import asyncio

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aurora_cli import config, ui


class Scripted:
    """A bridge whose mission is interrupted once by a PC restart."""
    calls = []

    def __init__(self, **kwargs):
        self.server_url = 'https://pc.example'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def mission_start(self, **payload):
        type(self).calls.append(('start', payload['request']))
        return {'ok': True, 'mission_id': f"mis_{len(type(self).calls)}", 'cursor': 0}

    def mission_status(self, mid):
        return {'ok': True, 'id': mid, 'status': 'running'}

    def mission_resume(self, mid, model=''):
        type(self).calls.append(('resume', mid))
        return {'ok': True, 'mission_id': mid, 'cursor': 1}

    def mission_stream(self, mid, last_event_id=0):
        if last_event_id == 0 and mid == 'mis_1' and ('resume', mid) not in type(self).calls:
            yield {'type': 'step_start', 'step': 'Avant redémarrage'}
            yield {'type': 'mission_interrupted', 'message': 'Execution lease expired'}
            return
        yield {'type': 'mission_complete', 'result': f'Résultat {mid}', 'status': 'completed'}

    def doctor(self):
        return {'ok': True, 'ready': True}


def test_restart_resumes_automatically_and_queued_request_follows(monkeypatch):
    config.set('mode', 'remote')
    config.set('server_url', 'https://pc.example')
    config.set('api_key', 'k')
    monkeypatch.setattr(ui.time, 'sleep', lambda seconds: None)
    Scripted.calls = []

    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[], bridge_factory=Scripted, input=pipe, output=DummyOutput())
            app.loop = asyncio.get_running_loop()
            app.task = asyncio.get_running_loop().create_task(app.submit('Première demande'))
            await asyncio.sleep(0)
            app.queue_request('Deuxième demande')
            await asyncio.wait_for(app.task, 5)
            for _ in range(100):
                await asyncio.sleep(.02)
                if app.task is not None and app.task.done() and not app.queued:
                    break
            if app.task is not None:
                await asyncio.wait_for(app.task, 5)
            assert ('resume', 'mis_1') in Scripted.calls
            assert Scripted.calls[-1] == ('start', 'Deuxième demande')
            assert 'Reprise automatique' in app.transcript and 'Résultat mis_1' in app.transcript
            assert 'Résultat mis_2' in app.transcript or 'Résultat mis_3' in app.transcript
    asyncio.run(scenario())


def test_auto_resume_can_be_switched_off():
    config.set('auto_resume', False)
    Scripted.calls = []
    config.set('mode', 'remote')
    config.set('server_url', 'https://pc.example')
    config.set('api_key', 'k')

    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[], bridge_factory=Scripted, input=pipe, output=DummyOutput())
            app.loop = asyncio.get_running_loop()
            await app.submit('Demande')
            await asyncio.sleep(.05)  # events posted by the mission thread
            assert not any(call[0] == 'resume' for call in Scripted.calls)
            assert '/resume ID' in app.transcript
    asyncio.run(scenario())


def test_quit_detaches_a_remote_mission_and_new_conversation_is_free():
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[], input=pipe, output=DummyOutput())
            mission = asyncio.get_running_loop().create_task(asyncio.sleep(30))
            app.task, app.state.id = mission, 'mis_live'
            assert app.detach_mission() and not app.busy
            assert '/attach mis_live' in app.active_toast()['text']
            mission.cancel()
            await asyncio.gather(mission, return_exceptions=True)
    asyncio.run(scenario())


def test_full_capability_is_the_default_and_remains_choosable():
    assert config.DEFAULT_CONFIG['default_permissions'] == 'FULL'
    config.set('default_permissions', 'SAFE')
    assert config.get('default_permissions') == 'SAFE'
