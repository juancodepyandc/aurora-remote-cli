"""The interface watches its connection and pairs without leaving it."""
import asyncio

import httpx
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aurora_cli import config, connect, device_pairing, render_server, ui


class Scripted:
    """A bridge whose answers follow a script shared by all instances."""
    server_url = 'https://old.trycloudflare.com'
    doctors = []
    alive_answers = []

    def __init__(self, **kwargs):
        self.server_url = config.resolve_server_url() or type(self).server_url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def doctor(self):
        answer = type(self).doctors.pop(0) if type(self).doctors else {'ok': True, 'ready': True}
        if answer.get('moved_to'):
            config.set_key('server_url', answer['moved_to'])
        return {k: v for k, v in answer.items() if k != 'moved_to'}

    def models(self):
        return {'models': [{'name': 'observed:model'}]}

    def alive(self):
        return type(self).alive_answers.pop(0) if type(self).alive_answers else {'ok': True}


def make_app(pipe):
    return ui.WorkspaceApp(history=[], bridge_factory=Scripted, input=pipe, output=DummyOutput())


def configure():
    config.set('server_url', 'https://old.trycloudflare.com')
    config.set('api_key', 'fixture')
    config.set('mode', 'auto')


def test_monitor_detects_restart_and_reconnects_to_the_new_address():
    configure()
    Scripted.doctors = [{'ok': True, 'ready': True},
                        {'ok': True, 'ready': True, 'moved_to': 'https://current.trycloudflare.com'}]
    Scripted.alive_answers = [{'ok': False}]

    async def scenario():
        with create_pipe_input() as pipe:
            app = make_app(pipe)
            await app.discover()
            assert app.connection_state() == 'online'
            delay = await app.monitor_once()
            assert delay == 20.0
            assert app.connection_state() == 'online'
            assert config.resolve_server_url() == 'https://current.trycloudflare.com'
            assert 'current.trycloudflare.com' in app.active_toast()['text']
    asyncio.run(scenario())


def test_offline_pc_is_retried_with_backoff_then_recovers_without_command():
    configure()
    offline = {'ok': False, 'error_kind': 'connection', 'error': 'ConnectError'}
    Scripted.doctors = [offline, offline, {'ok': True, 'ready': True}]
    Scripted.alive_answers = []

    async def scenario():
        with create_pipe_input() as pipe:
            app = make_app(pipe)
            await app.discover()
            assert app.connection_state() == 'offline'
            assert 'Reconnexion automatique' in app.transcript
            assert app._monitor_delay() == 2
            assert await app.monitor_once() == 4
            assert app.connection_state() == 'offline'
            assert await app.monitor_once() == 20.0
            assert app.connection_state() == 'online'
            assert 'de nouveau joignable' in app.active_toast()['text']
    asyncio.run(scenario())


def test_revoked_authorisation_starts_pairing_inside_the_interface(monkeypatch):
    configure()
    Scripted.doctors = [{'ok': False, 'http_status': 401, 'error': 'unknown key'},
                        {'ok': True, 'ready': True}]
    credential = 'aurora_device:' + 'z' * 64
    shown = {}

    def fake_pair(url, *, on_code, on_status, cancel, **kwargs):
        on_code('482913', 300)
        shown['url'] = url
        return credential

    monkeypatch.setattr(device_pairing, 'pair_device', fake_pair)
    monkeypatch.setattr(connect, '_register', lambda url, key: httpx.Response(
        200, json={'ok': True}, request=httpx.Request('POST', url)))
    monkeypatch.setattr(connect, 'discover_tunnel', lambda: 'https://current.trycloudflare.com')

    async def scenario():
        with create_pipe_input() as pipe:
            app = make_app(pipe)
            app.loop = asyncio.get_running_loop()
            await app.discover()
            assert app.connection_state() in {'unpaired', 'pairing'}
            for _ in range(50):
                await asyncio.sleep(.02)
                if app.connection_state() == 'online':
                    break
            assert app.connection_state() == 'online'
            assert '482 913' in app.transcript
            assert config.get('api_key') == credential
            assert config.resolve_server_url() == 'https://current.trycloudflare.com'
            assert credential not in app.transcript
    asyncio.run(scenario())


def test_pairing_can_be_cancelled_and_reports_state_to_native_renderer(monkeypatch):
    config.set('mode', 'auto')

    def slow_pair(url, *, on_code, on_status, cancel, **kwargs):
        on_code('111222', 120)
        cancel.wait(2)
        raise ValueError('Appairage annulé.')

    monkeypatch.setattr(device_pairing, 'pair_device', slow_pair)
    monkeypatch.setattr(connect, 'discover_tunnel', lambda: 'https://current.trycloudflare.com')

    async def scenario():
        with create_pipe_input() as pipe:
            app = make_app(pipe)
            task = asyncio.create_task(app.submit('/connect'))
            for _ in range(50):
                await asyncio.sleep(.02)
                if app.pairing and app.pairing.get('code'):
                    break
            state = render_server.snapshot(app)['state']
            assert state['pairing']['code'] == '111222'
            assert state['connection_state'] == 'pairing'
            assert state['toast'] and '111 222' in state['toast']['text']
            assert app.cancel_pairing()
            await asyncio.wait_for(task, 3)
            assert app.pairing is None
            assert 'Appairage interrompu' in app.transcript
    asyncio.run(scenario())


def test_lan_plaintext_pairing_is_allowed_but_public_plaintext_is_not():
    import pytest
    for host in ('http://192.168.1.20:3001', 'http://10.0.0.5', 'http://aurora.local:3001'):
        assert device_pairing._plaintext_allowed(httpx.URL(host).host)
    with pytest.raises(ValueError, match='HTTPS'):
        device_pairing.pair_device('http://aurora.example.com')
