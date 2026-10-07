"""Choose a host before dispatch; uncertainty must never duplicate work."""
import asyncio
from unittest.mock import AsyncMock

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
import pytest

from aurora_cli import config, ui
from aurora_cli.routing import remote_readiness


class ObservedBridge:
    server_url = 'https://test.trycloudflare.com'
    readiness = {'ok':True,'ready':True}
    def __init__(self,**kwargs):
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self,*args):
        pass
    def doctor(self):
        self.calls.append('doctor')
        return self.readiness
    def mission_start(self,**kwargs):
        self.calls.append(('start',kwargs))
        return {'ok':True,'mission_id':'mis_retained'}
    def mission_status(self,mid):
        return {'ok':True,'status':'running'}
    def mission_stream(self,*args,**kwargs):
        yield {'type':'mission_complete','status':'completed','result':'Observed result'}


@pytest.mark.parametrize('ready',[True,False])
def test_auto_selects_remote_only_after_observing_readiness(ready):
    config.set('mode','auto');config.set('server_url','https://configured.example');config.set('api_key','test')
    bridge = ObservedBridge();bridge.readiness = {'ok':True,'ready':ready}
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(bridge_factory=lambda **kwargs:bridge,input=pipe,output=DummyOutput())
            app.local = AsyncMock()
            await app.submit('Exact task')
            await asyncio.sleep(.01)
            assert bridge.calls[0]=='doctor'
            starts = [call for call in bridge.calls if isinstance(call,tuple)]
            if ready:
                assert len(starts)==1
                assert starts[0][1]['request']=='Exact task'
                assert starts[0][1]['workspace']==''
                app.local.assert_not_awaited()
                assert app.state.id=='mis_retained'
            else:
                assert not starts
                app.local.assert_awaited_once_with('Exact task',automatic=True)
                assert 'pending_request' not in app.history.state
    asyncio.run(scenario())


def test_unknown_post_acceptance_never_falls_back_locally_and_retains_key():
    config.set('mode','auto');config.set('server_url','https://configured.example');config.set('api_key','test')
    class Uncertain(ObservedBridge):
        def mission_start(self,**kwargs):
            self.calls.append(('start',kwargs))
            return {'ok':False,'error':'Reply lost','acceptance_unknown':True}
    bridge = Uncertain()
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(bridge_factory=lambda **kwargs:bridge,input=pipe,output=DummyOutput())
            app.local = AsyncMock()
            await app.submit('Exact task')
            app.local.assert_not_awaited()
            key = app.history.state['pending_request']['idempotency_key']
            bridge.readiness = {'ok':False,'error':'Unreachable'}
            await app.submit('/retry')
            app.local.assert_not_awaited()
            assert bridge.calls.count('doctor')==1
            starts = [call for call in bridge.calls if isinstance(call,tuple)]
            assert len(starts)==2 and starts[0]==starts[1]
            assert starts[1][1]['idempotency_key']==key
    asyncio.run(scenario())


def test_explicit_local_uses_no_remote_probe_or_dispatch():
    config.set('mode','local');config.set('server_url','https://configured.example');config.set('api_key','test')
    def forbidden(**kwargs):
        raise AssertionError('Local mode must stay on this computer')
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(bridge_factory=forbidden,input=pipe,output=DummyOutput())
            app.local = AsyncMock()
            await app.submit('Local task')
            app.local.assert_awaited_once_with('Local task')
    asyncio.run(scenario())


def test_plan_revision_preserves_criteria_and_verification_in_ui():
    view = ui.MissionView()
    view.consume({'type':'plan','steps':['Generate','Verify'],'criteria':['Exact requested asset']})
    view.verified = ['Exact requested asset']
    view.consume({'type':'plan_revision','steps':['Deliver verified result'],'criteria':['Weaker claim'],'reason':'Observed output exists'})
    assert view.criteria==['Exact requested asset'] and view.verified==['Exact requested asset']
    assert view.plan==['Deliver verified result']
    view.consume({'type':'plan_revision','worker':True,'steps':['Worker plan']})
    assert view.plan==['Deliver verified result']


def test_unreachable_preflight_is_observation_only():
    class Unreachable(ObservedBridge):
        def doctor(self):
            raise OSError('Connection refused')
    ready,reason = remote_readiness(Unreachable)
    assert not ready and 'Connection refused' in reason
