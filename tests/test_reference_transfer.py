import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from aurora_cli.bridge import Bridge
from aurora_cli import mission


def make_client(handler):
    client=Bridge(server_url='https://fixed.test',api_key='fixture')
    client._client.close()
    client._client=httpx.Client(base_url=client.server_url,transport=httpx.MockTransport(handler))
    return client


def test_actual_reference_bytes_are_uploaded_before_mission_post(tmp_path):
    image=tmp_path/'reference with spaces.png';image.write_bytes(b'actual reference bytes')
    requests=[]
    def handler(request):
        body=json.loads(request.content);requests.append((request.url.path,body))
        if request.url.path=='/api/cli/inputs':
            content=base64.b64decode(body['data'])
            assert content==image.read_bytes()
            assert hashlib.sha256(content).hexdigest()==body['sha256']
            return httpx.Response(200,json=dict(ok=True,id='inp_'+body['sha256']+'.png',sha256=body['sha256'],size=len(content)))
        assert len(requests)==2
        assert body['references'][0]['label']==str(image)
        return httpx.Response(200,json={'ok':True,'mission_id':'fixture'})
    with make_client(handler) as client:
        assert client.mission_start(f'Convertis cette image en 3D : "{image}"')['ok']
    assert [p for p,_ in requests]==['/api/cli/inputs','/api/cli/mission/start']


def test_failed_upload_never_dispatches_a_path_only_mission(tmp_path):
    image=tmp_path/'ref.png';image.write_bytes(b'bytes');paths=[]
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(400,json={'ok':False,'error':'corrupt upload'})
    with make_client(handler) as client:
        with pytest.raises(RuntimeError,match='non confirmé'):
            client.mission_start(f'Convertis cette image en 3D : "{image}"')
    assert paths==['/api/cli/inputs']


def test_unknown_acceptance_retains_original_key_and_uploaded_receipts(tmp_path):
    source=tmp_path/'input.csv';source.write_bytes(b'value\n7\n')
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append((request.url.path,body))
        if request.url.path=='/api/cli/inputs':
            return httpx.Response(200,json=dict(ok=True,id='inp_'+body['sha256']+'.csv',sha256=body['sha256'],size=body['size']))
        raise httpx.ReadTimeout('',request=request)
    history=SimpleNamespace(state={},save=Mock())
    with make_client(handler) as client:
        assert not mission.run_mission(client,'Inspect the supplied CSV',workspace=str(tmp_path),dispatch_state=history,input_files=[source])
        original=json.dumps(history.state['pending_request'],sort_keys=True)
        source.write_bytes(b'changed after uncertain acceptance')
        assert not mission.run_mission(client,'Inspect the supplied CSV',workspace=str(tmp_path),dispatch_state=history,input_files=[source])
    assert json.dumps(history.state['pending_request'],sort_keys=True)==original
    starts=[body for path,body in calls if path.endswith('/start')]
    assert len(starts)==2 and starts[0]==starts[1]
    assert len([p for p,_ in calls if p=='/api/cli/inputs'])==1


def test_accepted_stream_failure_is_followed_without_second_post(tmp_path):
    starts=[]
    def handler(request):
        if request.method=='POST':
            starts.append(json.loads(request.content))
            return httpx.Response(200,json={'ok':True,'mission_id':'mis_retained'})
        return httpx.Response(200,json={'ok':True,'status':'running','stream_start_cursor':0})
    history=SimpleNamespace(state={},save=Mock())
    with make_client(handler) as client:
        client.mission_stream=lambda *a,**k: iter([])
        assert not mission.run_mission(client,'Work',workspace=str(tmp_path),dispatch_state=history)
        assert not mission.run_mission(client,'Work',workspace=str(tmp_path),dispatch_state=history)
    assert len(starts)==1 and history.state['active_mission_id']=='mis_retained'


def test_ready_fixed_host_is_selected_even_with_a_client_image(monkeypatch,tmp_path):
    from aurora_cli import workspace,routing
    from aurora_cli.core.agents import get
    source=tmp_path/'ref.png';source.write_bytes(b'image')
    monkeypatch.setattr(workspace,'scan',lambda **k:SimpleNamespace(providers=[],loose_models=[]))
    monkeypatch.setattr(workspace,'profile',lambda:object())
    monkeypatch.setattr(workspace,'recommend',lambda *a:SimpleNamespace(steps=[SimpleNamespace(agent=get('3d'))]))
    monkeypatch.setattr(workspace.config,'get',lambda key,default=None:'auto' if key=='mode' else default)
    monkeypatch.setattr(workspace.config,'is_configured',lambda:True)
    monkeypatch.setattr(routing,'remote_readiness',lambda:(True,'Ready'))
    remote=Mock(return_value=True);monkeypatch.setattr(workspace,'_remote_mission',remote)
    assert workspace.execute(f'Convertis cette image en 3D : "{source}"',[])
    remote.assert_called_once()
