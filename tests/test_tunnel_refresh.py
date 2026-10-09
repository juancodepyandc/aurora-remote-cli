import httpx
import pytest

from aurora_cli import bridge, config, connect


OLD = 'https://old.trycloudflare.com'
NEW = 'https://current.trycloudflare.com'


@pytest.mark.parametrize('failure', ['dns', 'edge'])
def test_doctor_refreshes_saved_tunnel_after_authenticated_diagnostic(monkeypatch, failure):
    monkeypatch.setattr(config, 'load', lambda: {'server_url': OLD, 'api_key': 'fixture'})
    monkeypatch.setattr(config, 'get', lambda *a: 'fixture')
    monkeypatch.setattr(connect, 'discover_tunnel', lambda: NEW)
    saved = []
    monkeypatch.setattr(config, 'set_key', lambda *args: saved.append(args))
    calls = []
    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.url.host == 'old.trycloudflare.com':
            if failure == 'dns':
                raise httpx.ConnectError('DNS', request=request)
            return httpx.Response(530, text='offline')
        assert request.headers['Authorization'] == 'Bearer fixture'
        return httpx.Response(200, json={'ok': True, 'ready': True})
    monkeypatch.setattr(bridge.httpx, 'HTTPTransport', lambda **kw: httpx.MockTransport(handler))
    with bridge.Bridge() as client:
        assert client.doctor()['ready'] is True
        assert client.server_url == NEW
        assert client.status()['ok']
    assert saved == [('server_url', NEW)]
    assert calls == [('GET', OLD+'/api/cli/doctor'), ('GET', NEW+'/api/cli/doctor'),
                     ('GET', NEW+'/api/cli/status')]


@pytest.mark.parametrize('explicit,environment,status', [(True, False, 530), (False, True, 530),
                                                       (False, False, 401)])
def test_explicit_hosts_and_auth_errors_do_not_rediscover(monkeypatch, explicit, environment, status):
    monkeypatch.setattr(config, 'load', lambda: {'server_url': OLD})
    if environment:
        monkeypatch.setenv('JOBIA_SERVER_URL', OLD)
    monkeypatch.setattr(connect, 'discover_tunnel', lambda: pytest.fail('Unexpected discovery'))
    monkeypatch.setattr(bridge.httpx, 'HTTPTransport', lambda **kw: httpx.MockTransport(
        lambda request: httpx.Response(status, json={'ok': False})))
    with bridge.Bridge(server_url=OLD if explicit else '', api_key='fixture') as client:
        assert not client.doctor()['ok']
        assert client.server_url == OLD


def test_unverified_new_tunnel_is_never_saved_or_used_for_mission(monkeypatch):
    monkeypatch.setattr(config, 'load', lambda: {'server_url': OLD})
    monkeypatch.setattr(connect, 'discover_tunnel', lambda: NEW)
    monkeypatch.setattr(config, 'set_key', lambda *args: pytest.fail('Unverified URL saved'))
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(530 if request.url.host == 'old.trycloudflare.com' else 401,
                              json={'ok': False})
    monkeypatch.setattr(bridge.httpx, 'HTTPTransport', lambda **kw: httpx.MockTransport(handler))
    with bridge.Bridge(api_key='fixture') as client:
        assert not client.doctor()['ok']
        assert client.server_url == OLD
        assert not client.mission_start('Original')['ok']
    assert calls[-1] == OLD+'/api/cli/mission/start'
