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
    # 1. jobia --config "https://current.trycloudflare.com" -check
    res = runner.invoke(main, ['--color', 'never', '--config', NEW, '-check'])
    assert res.exit_code == 0, res.output
    assert state['server_url'] == NEW
    assert 'HTTP 200' in res.output and 'ms' in res.output

    # 2. jobia --config "https://dead.trycloudflare.com" -check fails and keeps previous URL
    res_dead = runner.invoke(main, ['--color', 'never', '--config', 'https://dead.trycloudflare.com', '-check'])
    assert res_dead.exit_code == 1
    assert state['server_url'] == NEW

    # 3. jobia --config auto -check discovers and saves live tunnel
    state['server_url'] = OLD
    res_auto = runner.invoke(main, ['--color', 'never', '--config', 'auto', '-check'])
    assert res_auto.exit_code == 0, res_auto.output
    assert state['server_url'] == NEW

    # 4. jobia --config "https://plays-excerpt-women-energy.trycloudflare.com" sets URL directly
    res_set = runner.invoke(main, ['--color', 'never', '--config', 'https://plays-excerpt-women-energy.trycloudflare.com'])
    assert res_set.exit_code == 0, res_set.output
    assert state['server_url'] == 'https://plays-excerpt-women-energy.trycloudflare.com'

