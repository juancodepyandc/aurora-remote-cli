import base64
import httpx
import pytest
from aurora_cli import bridge, config, connect

OLD='https://old.trycloudflare.com'
NEW='https://current.trycloudflare.com'

@pytest.mark.parametrize('path',['/api/cli/status','/api/cli/version','/api/cli/tools','/api/cli/models'])
def test_shared_metadata_recovers_without_prior_doctor(monkeypatch,path):
    monkeypatch.setattr(config,'load',lambda:{'server_url':OLD})
    monkeypatch.setattr(config,'set_key',lambda *a:None)
    monkeypatch.setattr(connect,'discover_tunnel',lambda:NEW)
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.host,request.url.path))
        return httpx.Response(530,json={'ok':False}) if request.url.host=='old.trycloudflare.com' else httpx.Response(200,json={'ok':True})
    monkeypatch.setattr(bridge.httpx,'HTTPTransport',lambda **kw:httpx.MockTransport(handler))
    with bridge.Bridge(api_key='fixture') as b:
        assert b.get(path)['ok'] and b.server_url==NEW
    assert calls==[('GET','old.trycloudflare.com',path),('GET','current.trycloudflare.com','/api/cli/doctor'),('GET','current.trycloudflare.com',path)]

@pytest.mark.parametrize('method,path',[('get','/api/cli/mission/mis_accepted'),('get','/api/cli/artifacts/download'),('post','/api/cli/mission/start'),('delete','/api/cli/mission/mis_accepted')])
def test_accepted_work_and_writes_never_rediscover(monkeypatch,method,path):
    monkeypatch.setattr(config,'load',lambda:{'server_url':OLD})
    monkeypatch.setattr(connect,'discover_tunnel',lambda:pytest.fail('Work must remain anchored'))
    monkeypatch.setattr(bridge.httpx,'HTTPTransport',lambda **kw:httpx.MockTransport(lambda r:httpx.Response(530,json={'ok':False})))
    with bridge.Bridge(api_key='fixture') as b:
        assert not getattr(b,method)(path)['ok'] and b.server_url==OLD

@pytest.mark.parametrize('failure',[403,429,503,'dns'])
def test_api_unavailable_uses_raw_discovery_reader(monkeypatch,failure):
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        request=httpx.Request('GET',url)
        if url==connect.TUNNEL_SOURCE:
            if failure=='dns':raise httpx.ConnectError('dns',request=request)
            return httpx.Response(failure,request=request)
        return httpx.Response(200,text=NEW,request=request)
    monkeypatch.setattr(connect.httpx,'get',get)
    assert connect.discover_tunnel()==NEW
    assert calls==[connect.TUNNEL_SOURCE,connect.TUNNEL_RAW_SOURCE]

@pytest.mark.parametrize('content',['','http://old.trycloudflare.com','https://evil.example','https://nested.old.trycloudflare.com',NEW+'/path'])
def test_offline_and_invalid_api_are_authoritative(monkeypatch,content):
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        return httpx.Response(200,json={'encoding':'base64','content':base64.b64encode(content.encode()).decode()},request=httpx.Request('GET',url))
    monkeypatch.setattr(connect.httpx,'get',get)
    with pytest.raises(ValueError):connect.discover_tunnel()
    assert calls==[connect.TUNNEL_SOURCE]

def test_environment_host_is_never_replaced_during_pairing(monkeypatch):
    monkeypatch.setenv('JOBIA_SERVER_URL',OLD)
    monkeypatch.setattr(connect,'_register',lambda *a:(_ for _ in ()).throw(httpx.ConnectError('offline')))
    monkeypatch.setattr(connect,'discover_tunnel',lambda:pytest.fail('Explicit environment host replaced'))
    assert not connect.connect(api_key='fixture',then_interactive=False)
