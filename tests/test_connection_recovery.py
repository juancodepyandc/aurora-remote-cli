"""The client follows the Aurora PC to its new quick-tunnel address.

Every HTTP exchange is a fixture (``httpx.MockTransport``); no publication,
tunnel or bridge is contacted.
"""
import base64
import json

import httpx
import pytest

from aurora_cli import bridge, config, connect, tunnel

OLD = 'https://old.trycloudflare.com'
NEW = 'https://current.trycloudflare.com'
SHA = 'ca1b315b95c875062e2f9a759b9e15c534909163'
HEALTH = {'ok': True, 'service': 'aurora-bridge'}


def pkt(payload: bytes) -> bytes:
    return f'{len(payload) + 4:04x}'.encode() + payload


ADVERTISEMENT = (pkt(b'# service=git-upload-pack\n') + b'0000'
                 + pkt(SHA.encode() + b' HEAD\0multi_ack symref=HEAD:refs/heads/main\n')
                 + pkt(SHA.encode() + b' refs/heads/main\n') + b'0000')


def publication_client(handler):
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


# --- Discovery -------------------------------------------------------------

def test_fresh_reader_uses_commit_addressed_file():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith('/info/refs'):
            return httpx.Response(200, content=ADVERTISEMENT)
        return httpx.Response(200, text=NEW + '/\n')
    publication = tunnel.read_publication(client_factory=publication_client(handler))
    assert publication == tunnel.Publication(NEW, 'git', SHA)
    assert calls == ['https://github.com/juancodepyandc/aurora-live.git/info/refs?service=git-upload-pack',
                     f'https://raw.githubusercontent.com/juancodepyandc/aurora-live/{SHA}/tunnel.txt']


@pytest.mark.parametrize('git_failure', ['dns', 500, 'garbage'])
def test_git_failure_falls_back_to_api_then_raw(git_failure):
    calls = []
    def handler(request):
        calls.append(request.url.host)
        if request.url.path.endswith('/info/refs'):
            if git_failure == 'dns':
                raise httpx.ConnectError('dns', request=request)
            if git_failure == 'garbage':
                return httpx.Response(200, content=b'zzzz not a pkt line')
            return httpx.Response(git_failure)
        if request.url.host == 'api.github.com':
            return httpx.Response(403, json={'message': 'rate limited'})
        return httpx.Response(200, text=NEW)
    assert tunnel.read_publication(client_factory=publication_client(handler)).url == NEW
    assert calls == ['github.com', 'api.github.com', 'raw.githubusercontent.com']


def test_api_etag_makes_unchanged_reads_free():
    seen = []
    def handler(request):
        if request.url.path.endswith('/info/refs'):
            raise httpx.ConnectError('blocked', request=request)
        seen.append(request.headers.get('If-None-Match'))
        if request.headers.get('If-None-Match') == '"v1"':
            return httpx.Response(304)
        content = base64.b64encode(NEW.encode()).decode()
        return httpx.Response(200, headers={'ETag': '"v1"'},
                              json={'encoding': 'base64', 'content': content})
    factory = publication_client(handler)
    assert tunnel.read_publication(client_factory=factory) == tunnel.Publication(NEW, 'api')
    assert tunnel.read_publication(client_factory=factory) == tunnel.Publication(NEW, 'api')
    assert seen == [None, '"v1"']


def test_empty_publication_is_authoritative_offline():
    calls = []
    def handler(request):
        calls.append(request.url.host)
        if request.url.path.endswith('/info/refs'):
            return httpx.Response(200, content=ADVERTISEMENT)
        return httpx.Response(200, text='')
    with pytest.raises(tunnel.TunnelOffline):
        tunnel.current_address(client_factory=publication_client(handler))
    assert calls == ['github.com', 'raw.githubusercontent.com']


@pytest.mark.parametrize('content', ['http://old.trycloudflare.com', 'https://evil.example',
                                     'https://nested.old.trycloudflare.com', NEW + '/path',
                                     'https://user:pw@x.trycloudflare.com'])
def test_invalid_publication_is_refused_without_fallback(content):
    calls = []
    def handler(request):
        calls.append(request.url.host)
        if request.url.path.endswith('/info/refs'):
            return httpx.Response(200, content=ADVERTISEMENT)
        return httpx.Response(200, text=content)
    with pytest.raises(ValueError):
        tunnel.read_publication(client_factory=publication_client(handler))
    assert calls == ['github.com', 'raw.githubusercontent.com']


def test_concurrent_failures_share_one_read(monkeypatch):
    reads = []
    def handler(request):
        reads.append(request.url.host)
        if request.url.path.endswith('/info/refs'):
            return httpx.Response(200, content=ADVERTISEMENT)
        return httpx.Response(200, text=NEW)
    factory = publication_client(handler)
    assert tunnel.current_address(client_factory=factory, max_age=30) == NEW
    assert tunnel.current_address(client_factory=factory, max_age=30) == NEW
    assert len(reads) == 2


def test_publisher_can_be_overridden_for_a_fork(monkeypatch):
    monkeypatch.setenv('JOBIA_LIVE_REPO', 'someone/their-live@status')
    assert tunnel.publisher() == ('someone/their-live', 'status')
    monkeypatch.setenv('JOBIA_LIVE_REPO', '../../etc')
    assert tunnel.publisher() == (tunnel.DEFAULT_PUBLISHER, 'main')


# --- Bridge relocation -----------------------------------------------------

def install(monkeypatch, handler, *, saved_url=OLD, discovered=NEW):
    state = {'server_url': saved_url, 'api_key': 'fixture'}
    monkeypatch.setattr(config, 'load', lambda: dict(state))
    monkeypatch.setattr(config, 'set_key', lambda key, value: state.__setitem__(key, value))
    monkeypatch.setattr(connect, 'discover_tunnel', lambda: discovered)
    monkeypatch.setattr(bridge.httpx, 'HTTPTransport', lambda **kw: httpx.MockTransport(handler))
    return state


def fixture_handler(calls, *, old_status=530, new_responses=None, old_exception=None):
    def handler(request):
        calls.append((request.method, request.url.host, request.url.path))
        if request.url.host == 'old.trycloudflare.com':
            if old_exception:
                raise old_exception('fixture', request=request)
            return httpx.Response(old_status, text='edge')
        if request.url.path in ('/api/health', '/api/ping'):
            return httpx.Response(200, json=HEALTH)
        return (new_responses or {}).get(request.url.path, httpx.Response(200, json={'ok': True}))
    return handler


@pytest.mark.parametrize('path', ['/api/cli/doctor', '/api/cli/status', '/api/cli/mcp/list',
                                  '/api/cli/skills/list', '/api/cli/missions', '/api/cli/artifacts/token'])
def test_every_read_follows_the_pc_and_saves_the_address(monkeypatch, path):
    calls = []
    state = install(monkeypatch, fixture_handler(calls))
    with bridge.Bridge() as client:
        result = client.get(path)
        assert result['ok'] and client.server_url == NEW
        assert result['relocated_to'] == NEW
    assert calls == [('GET', 'old.trycloudflare.com', path), ('GET', 'current.trycloudflare.com', '/api/health'),
                     ('GET', 'current.trycloudflare.com', path)]
    assert state['server_url'] == NEW


@pytest.mark.parametrize('failure', [530, httpx.ConnectError, httpx.ReadTimeout, 504])
def test_mission_start_is_replayed_with_the_same_idempotency_key(monkeypatch, failure):
    calls, bodies = [], []
    def handler(request):
        if request.url.path == '/api/cli/mission/start':
            bodies.append(json.loads(request.content))
        if request.url.host == 'old.trycloudflare.com':
            calls.append('old')
            if isinstance(failure, int):
                return httpx.Response(failure, text='edge')
            raise failure('fixture', request=request)
        if request.url.path == '/api/health':
            return httpx.Response(200, json=HEALTH)
        calls.append('new')
        return httpx.Response(200, json={'ok': True, 'mission_id': 'mis_same', 'replayed': True, 'cursor': 0})
    install(monkeypatch, handler)
    with bridge.Bridge() as client:
        result = client.mission_start('Original', references=[])
        assert result['ok'] and result['mission_id'] == 'mis_same'
        assert client._mission_hosts['mis_same'] == NEW
    assert len(bodies) == 2 and bodies[0] == bodies[1]
    assert len(bodies[0]['idempotency_key']) == 32


def test_writes_are_replayed_only_when_never_delivered(monkeypatch):
    calls = []
    install(monkeypatch, fixture_handler(calls, old_exception=httpx.ConnectError))
    with bridge.Bridge() as client:
        assert client.skills_create('fixture', description='d')['ok']
    assert [c[1] for c in calls] == ['old.trycloudflare.com', 'current.trycloudflare.com', 'current.trycloudflare.com']

    calls.clear()
    install(monkeypatch, fixture_handler(calls, old_exception=httpx.ReadTimeout))
    with bridge.Bridge() as client:
        result = client.skills_create('fixture', description='d')
        assert not result['ok'] and client.server_url == OLD
    assert calls == [('POST', 'old.trycloudflare.com', '/api/cli/skills/create')]


def test_accepted_mission_follows_only_after_the_new_host_knows_it(monkeypatch):
    calls = []
    responses = {'/api/cli/mission/mis_a/status': httpx.Response(200, json={'ok': True, 'id': 'mis_a'})}
    install(monkeypatch, fixture_handler(calls, new_responses=responses))
    with bridge.Bridge() as client:
        client._mission_hosts['mis_a'] = OLD
        assert client.mission_stop('mis_a')['ok']
        assert client._mission_hosts['mis_a'] == NEW
    assert calls[-2:] == [('GET', 'current.trycloudflare.com', '/api/cli/mission/mis_a/status'),
                          ('POST', 'current.trycloudflare.com', '/api/cli/mission/mis_a/stop')]


def test_unknown_mission_stays_on_its_original_host(monkeypatch):
    calls = []
    responses = {'/api/cli/mission/mis_a/status': httpx.Response(404, json={'ok': False})}
    install(monkeypatch, fixture_handler(calls, new_responses=responses))
    with bridge.Bridge() as client:
        client._mission_hosts['mis_a'] = OLD
        assert not client.mission_status('mis_a')['ok']
        assert client._mission_hosts['mis_a'] == OLD
    assert ('GET', 'current.trycloudflare.com', '/api/cli/mission/mis_a/status') in calls


def test_authentication_errors_never_relocate(monkeypatch):
    calls = []
    install(monkeypatch, fixture_handler(calls, old_status=401))
    monkeypatch.setattr(connect, 'discover_tunnel', lambda: pytest.fail('401 must not rediscover'))
    with bridge.Bridge() as client:
        assert client.doctor()['http_status'] == 401 and client.server_url == OLD


def test_explicit_lan_host_never_moves_but_an_explicit_dead_tunnel_does(monkeypatch):
    calls = []
    install(monkeypatch, fixture_handler(calls))
    monkeypatch.setattr(connect, 'discover_tunnel', lambda: pytest.fail('LAN host must not move'))
    def lan(request):
        return httpx.Response(503, text='down')
    monkeypatch.setattr(bridge.httpx, 'HTTPTransport', lambda **kw: httpx.MockTransport(lan))
    with bridge.Bridge(server_url='http://192.168.1.20:3001', api_key='k') as client:
        assert not client.status()['ok'] and client.server_url == 'http://192.168.1.20:3001'

    calls.clear()
    install(monkeypatch, fixture_handler(calls))
    monkeypatch.setenv('JOBIA_SERVER_URL', OLD)
    with bridge.Bridge() as client:
        assert client.status()['ok'] and client.server_url == NEW


def test_unverified_new_address_is_neither_used_nor_saved(monkeypatch):
    calls = []
    def handler(request):
        calls.append((request.url.host, request.url.path))
        if request.url.host == 'old.trycloudflare.com':
            return httpx.Response(530, text='edge')
        return httpx.Response(200, json={'ok': True, 'service': 'something-else'})
    state = install(monkeypatch, handler)
    with bridge.Bridge() as client:
        assert not client.mission_start('Original')['ok']
        assert client.server_url == OLD
    assert state['server_url'] == OLD
    assert ('current.trycloudflare.com', '/api/cli/mission/start') not in calls


def test_new_address_refusing_our_identity_is_not_saved(monkeypatch):
    calls = []
    responses = {'/api/cli/status': httpx.Response(401, json={'ok': False, 'error': 'unknown key'})}
    state = install(monkeypatch, fixture_handler(calls, new_responses=responses))
    with bridge.Bridge() as client:
        assert client.status()['http_status'] == 401
    assert state['server_url'] == OLD


def test_mission_polling_survives_a_pc_restart(monkeypatch):
    monkeypatch.setattr(bridge.time, 'sleep', lambda _: None)
    served = []
    def handler(request):
        host, path = request.url.host, request.url.path
        if path == '/api/health':
            return httpx.Response(200, json=HEALTH)
        if path.endswith('/status'):
            return httpx.Response(200, json={'ok': True, 'id': 'mis_live'})
        cursor = int(request.headers['Last-Event-ID'])
        served.append((host, cursor))
        if host == 'old.trycloudflare.com':
            if cursor == 0:
                return httpx.Response(200, json={'ok': True, 'events': [{'id': 1, 'event': {'type': 'tool_result'}}],
                                                 'cursor': 1, 'terminal': False})
            return httpx.Response(530, text='tunnel gone')
        return httpx.Response(200, json={'ok': True, 'events': [{'id': 2, 'event': {'type': 'mission_complete'}}],
                                         'cursor': 2, 'terminal': True})
    install(monkeypatch, handler)
    with bridge.Bridge() as client:
        client._mission_hosts['mis_live'] = OLD
        events = [e['type'] for e in client.mission_stream('mis_live')]
    assert events == ['tool_result', 'mission_complete']
    assert served == [('old.trycloudflare.com', 0), ('old.trycloudflare.com', 1), ('current.trycloudflare.com', 1)]


def test_stream_reconnection_is_bounded(monkeypatch):
    monkeypatch.setattr(bridge.time, 'sleep', lambda _: None)
    requests = []
    def handler(request):
        requests.append(request)
        raise httpx.ConnectError('down', request=request)
    client = bridge.Bridge(server_url='http://127.0.0.1:9', api_key='k')
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler), base_url='http://127.0.0.1:9')
    client.reconnect_attempts = 2
    with client:
        events = list(client.stream_sse('/api/cli/mission/m/stream', method='GET', resume=True))
    assert [e['type'] for e in events] == ['reconnecting', 'reconnecting', 'error']
    assert len(requests) == 3


def test_pairing_follows_a_dead_environment_tunnel(monkeypatch):
    monkeypatch.setenv('JOBIA_SERVER_URL', OLD)
    registered = []
    def register(url, key):
        registered.append(url)
        if url == OLD:
            raise httpx.ConnectError('offline')
        return httpx.Response(200, json={'ok': True}, request=httpx.Request('POST', url))
    monkeypatch.setattr(connect, '_register', register)
    monkeypatch.setattr(connect, 'discover_tunnel', lambda: NEW)
    assert connect.connect(api_key='fixture', then_interactive=False)
    assert registered == [OLD, NEW]
    assert config.load()['server_url'] == NEW


def test_cli_config_and_single_dash_check_options(monkeypatch):
    from click.testing import CliRunner
    from aurora_cli.cli import main

    state = {'server_url': OLD, 'api_key': ''}
    monkeypatch.setattr(config, 'load', lambda: dict(state))
    monkeypatch.setattr(config, 'get', lambda k, default='': state.get(k, default))
    monkeypatch.setattr(config, 'set_key', lambda k, v: state.__setitem__(k, v))
    monkeypatch.setattr(connect, 'discover_tunnel', lambda: NEW)

    def fake_get(url, timeout=8.0, follow_redirects=True):
        req = httpx.Request('GET', url)
        if 'dead.trycloudflare.com' in url:
            return httpx.Response(530, text='edge error', request=req)
        return httpx.Response(200, json={'ok': True, 'service': 'aurora-bridge'}, request=req)

    monkeypatch.setattr(httpx, 'get', fake_get)
    runner = CliRunner()
    res = runner.invoke(main, ['--color', 'never', '--config', NEW, '-check'])
    assert res.exit_code == 0, res.output
    assert state['server_url'] == NEW and 'HTTP 200' in res.output and 'ms' in res.output
    res_dead = runner.invoke(main, ['--color', 'never', '--config', 'https://dead.trycloudflare.com', '-check'])
    assert res_dead.exit_code == 1 and state['server_url'] == NEW
    state['server_url'] = OLD
    res_auto = runner.invoke(main, ['--color', 'never', '--config', 'auto', '-check'])
    assert res_auto.exit_code == 0, res_auto.output
    assert state['server_url'] == NEW


def test_successive_clients_share_one_keep_alive_pool(monkeypatch):
    created = []

    class Pool(httpx.MockTransport):
        closed = False

        def __init__(self, **kwargs):
            created.append(self)
            super().__init__(lambda request: httpx.Response(200, json={'ok': True}))

        def close(self):
            type(self).closed = True

    monkeypatch.setattr(bridge.httpx, 'HTTPTransport', Pool)
    for _ in range(3):
        with bridge.Bridge(server_url='http://127.0.0.1:3001', api_key='k') as client:
            assert client.status()['ok']
    assert len(created) == 1 and not Pool.closed
