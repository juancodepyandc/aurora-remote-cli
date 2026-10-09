import hashlib

import httpx
import pytest
from click.testing import CliRunner

from aurora_cli import print_export
from aurora_cli.cli import main


class Client:
    def __init__(self,handler,state):
        self._client=httpx.Client(base_url='https://bridge.invalid',transport=httpx.MockTransport(handler))
        self.state=state;self.doctors=0
    def doctor(self):self.doctors+=1;return {'ok':True}
    def get(self,path):return self.state


def test_existing_mesh_upload_and_archive_hash_before_receipt(tmp_path):
    mesh=tmp_path/'mesh.stl';mesh.write_bytes(b'actual source')
    archive=b'actual ZIP receipt';requests=[]
    def handler(request):
        requests.append(request)
        if request.method=='POST':
            assert b'actual source' in request.content and b'"mode": "geometry"' in request.content
            return httpx.Response(202,json={'ok':True,'job_id':'eng_fixture'})
        return httpx.Response(200,content=archive)
    state={'ok':True,'state':'done','report':{'units':'mm'},'archive_sha256':hashlib.sha256(archive).hexdigest()}
    client=Client(handler,state);output=tmp_path/'output.zip'
    assert print_export.prepare_export(client,mesh,{'mode':'geometry'},output)=={'units':'mm'}
    assert output.read_bytes()==archive and mesh.read_bytes()==b'actual source'
    assert client.doctors==1 and [r.method for r in requests]==['POST','GET']
    client._client.close()


def test_hash_mismatch_does_not_publish_output_and_preserves_existing(tmp_path):
    source=tmp_path/'source.glb';source.write_bytes(b'mesh');output=tmp_path/'output.zip'
    def handler(request):
        return httpx.Response(202,json={'ok':True,'job_id':'fixture'}) if request.method=='POST' else httpx.Response(200,content=b'changed')
    client=Client(handler,{'ok':True,'state':'done','archive_sha256':'0'*64})
    with pytest.raises(RuntimeError,match='empreinte'):print_export.prepare_export(client,source,{},output)
    assert not output.exists() and not list(tmp_path.glob('.aurora-export-*'))
    output.write_bytes(b'keep')
    with pytest.raises(ValueError,match='existe'):print_export.prepare_export(client,source,{},output)
    assert output.read_bytes()==b'keep';client._client.close()


def test_cli_assembly_demands_profile_before_upload(tmp_path):
    mesh=tmp_path/'source.stl';mesh.write_bytes(b'mesh')
    result=CliRunner().invoke(main,['export3d',str(mesh),'--variant','assembly'])
    assert result.exit_code!=0 and '--profile' in result.output
