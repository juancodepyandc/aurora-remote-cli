import httpx
import pytest
from aurora_cli.device_pairing import pair_device
from aurora_cli import device_pairing,config,connect


def test_pending_code_approval_saved_credential_never_displayed(capsys):
    calls=[];credential='aurora_device:'+'x'*64
    def handle(request):
        calls.append(request)
        if request.url.path.endswith('/pair'):return httpx.Response(200,json={'code':'123456','claim':'nonce','expires_in':30})
        return httpx.Response(200,json={'connected':len(calls)>2,**({'credential':credential} if len(calls)>2 else {})})
    result=pair_device('https://aurora.test',client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(handle),**kw),sleep=lambda n:None)
    assert result==credential and len(calls)==3
    visible = capsys.readouterr().out
    assert '123456' in visible and credential not in visible and 'nonce' not in visible
    assert b'"transport":"cli"' in calls[0].content


def test_remote_plaintext_refused():
    with pytest.raises(ValueError,match='HTTPS'):pair_device('http://untrusted.test')


def test_connect_without_key_uses_approval_not_manual_key(monkeypatch):
    saved=[]
    monkeypatch.setattr(config,'get',lambda *a: '')
    monkeypatch.setattr(device_pairing,'pair_device',lambda url:'aurora_device:'+'x'*64)
    monkeypatch.setattr(connect,'_register',lambda *a:httpx.Response(200,json={'ok':True},request=httpx.Request('POST','https://aurora.test/api/cli/register')))
    monkeypatch.setattr(config,'set_key',lambda *a:saved.append(a))
    assert connect.connect(server='https://aurora.test',then_interactive=False)
    assert saved==[('server_url','https://aurora.test'),('api_key','aurora_device:'+'x'*64)]
