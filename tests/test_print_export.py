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


def test_cli_opens_delivered_assembly_and_forwards_constraints(tmp_path,monkeypatch):
    from contextlib import nullcontext
    mesh=tmp_path/'source.glb';mesh.write_bytes(b'mesh')
    profile=tmp_path/'printer.json';profile.write_text('{"name":"Printer"}')
    constraints=tmp_path/'constraints.json';constraints.write_text('{"protected_zones_mm": [[[1,2,3],[4,5,6]]]}')
    seen={}
    monkeypatch.setattr(print_export,'Bridge',lambda:nullcontext('client'))
    def export(client,source,options,output):
        seen['options']=options;output.write_bytes(b'verified delivery')
        return {'piece_count':3,'pin_count':4}
    def view(path,**kwargs):
        assert path.read_bytes()==b'verified delivery';seen['viewer']=kwargs
        return 'http://127.0.0.1:1234/viewer.html',True
    monkeypatch.setattr(print_export,'prepare_export',export)
    monkeypatch.setattr(print_export,'launch_viewer',view)
    result=CliRunner().invoke(main,['export3d',str(mesh),'--variant','assembly','--profile',str(profile),
        '--constraints',str(constraints),'--viewer','textured','--output',str(tmp_path/'out.zip')])
    assert result.exit_code==0,result.output
    assert 'Viewer : http://127.0.0.1:1234' in result.output
    assert seen['viewer']=={'open_browser':True,'appearance':'textured','exploded':True}
    assert seen['options']['protected_zones_mm']==[[[1,2,3],[4,5,6]]]


def test_cli_view3d_reopens_without_any_bridge_or_export(tmp_path,monkeypatch):
    source=tmp_path/'assembly.zip';source.write_bytes(b'archive')
    monkeypatch.setattr(print_export,'Bridge',lambda:pytest.fail('Reopening must not connect to bridge'))
    calls=[]
    monkeypatch.setattr(print_export,'launch_viewer',lambda path,**kw:(calls.append(kw) or 'http://127.0.0.1:1234',False))
    result=CliRunner().invoke(main,['view3d',str(source),'--no-open','--exploded'])
    assert result.exit_code==0,result.output
    assert calls==[{'open_browser':False,'appearance':'colors','exploded':True}]


def test_cli_forwards_filament_plan_before_verified_delivery(tmp_path,monkeypatch):
    from contextlib import nullcontext
    mesh=tmp_path/'model.stl';mesh.write_bytes(b'mesh')
    profile=tmp_path/'printer.json';profile.write_text('{"name":"Ender-3 V3 SE","color_capability":"single"}')
    filaments=tmp_path/'filaments.json';filaments.write_text('[{"name":"PLA bleu","color":"#245caa"}]')
    seen={}
    monkeypatch.setattr(print_export,'Bridge',lambda:nullcontext('client'))
    def export(client,source,options,output):
        seen.update(options);output.write_bytes(b'archive');return {'piece_count':1,'pin_count':0}
    monkeypatch.setattr(print_export,'prepare_export',export)
    monkeypatch.setattr(print_export,'launch_viewer',lambda *a,**k:('http://127.0.0.1:1234',False))
    result=CliRunner().invoke(main,['export3d',str(mesh),'--variant','assembly','--profile',str(profile),
        '--filaments',str(filaments),'--no-open','--output',str(tmp_path/'out.zip')])
    assert result.exit_code==0,result.output
    assert seen['profile']['color_capability']=='single'
    assert seen['piece_filaments']==[{'name':'PLA bleu','color':'#245caa'}]
    failed=CliRunner().invoke(main,['export3d',str(mesh),'--filaments',str(filaments)])
    assert failed.exit_code!=0 and 'réservé' in failed.output
