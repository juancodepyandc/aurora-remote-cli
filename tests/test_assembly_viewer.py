import json
from pathlib import Path
import threading
import zipfile

import httpx
import pytest

from aurora_cli.assembly_viewer import make_server, launch_viewer, validate_archive


def package(tmp_path):
    path=tmp_path/'assembly.zip'
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('assembly.json',json.dumps({'mode':'assembly','textured_assembly_available':False}))
        archive.writestr('viewer.html','<html>Assembly viewer</html>')
        archive.writestr('assembled.glb',b'glb')
        archive.writestr('viewer_assets/build/three.module.js','export const version=1;')
        archive.writestr('secret.txt','private')
    return path


def test_real_http_loopback_viewer_serves_only_render_assets(tmp_path):
    archive=package(tmp_path);server=make_server(archive)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        assert server.server_address[0]=='127.0.0.1'
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False) as client:
            assert client.get('/viewer.html?exploded=1').status_code==200
            assert client.get('/assembled.glb').content==b'glb'
            script=client.get('/viewer_assets/build/three.module.js')
            assert script.status_code==200 and script.headers['content-type']=='text/javascript'
            assert client.get('/secret.txt').status_code==404
            assert client.get('/%2e%2e/secret.txt').status_code==404
            assert client.get('/viewer.html',headers={'Host':'attacker.invalid'}).status_code==403
            assert "connect-src 'self'" in script.headers['content-security-policy']
    finally:
        server.shutdown();server.server_close();thread.join(2)
    assert not list(tmp_path.glob('viewer.html')) # never extracts user paths


def test_detached_server_is_ready_after_cli_returns(tmp_path,monkeypatch):
    import aurora_cli.assembly_viewer as viewer
    archive=package(tmp_path);processes=[];original=viewer.subprocess.Popen
    def spawn(*a,**kw):
        process=original(*a,**kw);processes.append(process);return process
    monkeypatch.setattr(viewer.subprocess,'Popen',spawn)
    monkeypatch.setattr(viewer.webbrowser,'open',lambda url:pytest.fail('no-open requested'))
    try:
        url,opened=launch_viewer(archive,open_browser=False)
        assert not opened and 'appearance=colors' in url
        assert httpx.get(url,trust_env=False).status_code==200
    finally:
        for process in processes:
            process.terminate();process.wait(timeout=5)


def test_absent_texture_fails_before_starting_viewer(tmp_path):
    with pytest.raises(ValueError,match='textures'):
        launch_viewer(package(tmp_path),open_browser=False,appearance='textured')


def test_zip_traversal_and_old_archive_rejected(tmp_path):
    path=package(tmp_path)
    with zipfile.ZipFile(path,'a') as archive:archive.writestr('../outside','malicious')
    with pytest.raises(ValueError,match='Chemins'):validate_archive(path)
    old=tmp_path/'old.zip'
    with zipfile.ZipFile(old,'w') as archive:archive.writestr('assembled.glb','old')
    with pytest.raises(ValueError,match='bridge à jour'):validate_archive(old)
