"""Release installer: verify bytes before execution; preserve an existing binary."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from aurora_cli import __version__, renderer, renderer_install as native


@pytest.mark.parametrize('system,machine,target',[
    ('Darwin','arm64','aarch64-apple-darwin'),('Darwin','x86_64','x86_64-apple-darwin'),
    ('Windows','AMD64','x86_64-pc-windows-msvc'),('Linux','x86_64','x86_64-unknown-linux-gnu')])
def test_release_architecture_is_explicit(system,machine,target):
    assert native.target_triple(system,machine)==target


def client(payload=b'binary fixture',checksum=None,status=200):
    name=native.asset_name('aarch64-apple-darwin')
    digest=checksum or hashlib.sha256(payload).hexdigest()
    def respond(request):
        assert request.url.host=='github.com'
        assert f'/v{__version__}/' in request.url.path
        content=f'{digest}  {name}\n'.encode() if request.url.path.endswith('.sha256') else payload
        return httpx.Response(status,content=content)
    return httpx.Client(transport=httpx.MockTransport(respond))


def test_verified_release_installs_atomically_and_can_be_reused_offline(tmp_path):
    target=tmp_path/'aurora-native';verify=Mock()
    with client() as download:
        assert native.install_native(client=download,destination=target,target='aarch64-apple-darwin',verify=verify)==target
    assert target.read_bytes()==b'binary fixture'
    assert verify.call_count==1
    with httpx.Client(transport=httpx.MockTransport(lambda r:pytest.fail('No network for cached renderer'))) as offline:
        assert native.install_native(client=offline,destination=target,target='aarch64-apple-darwin',verify=verify)==target


@pytest.mark.parametrize('failure',['hash','version','http','size'])
def test_failed_update_preserves_existing_binary(tmp_path,monkeypatch,failure):
    target=tmp_path/'aurora-native';target.write_bytes(b'existing version')
    verify=Mock(side_effect=RuntimeError('wrong version') if failure=='version' else None)
    if failure=='size':monkeypatch.setattr(native,'MAX_BINARY_BYTES',2)
    with client(checksum='0'*64 if failure=='hash' else None,status=404 if failure=='http' else 200) as download:
        with pytest.raises(RuntimeError):
            native.install_native(client=download,destination=target,target='aarch64-apple-darwin',verify=verify)
    assert target.read_bytes()==b'existing version'
    assert not list(tmp_path.glob('.aurora-*'))
    if failure!='version':verify.assert_not_called()


def test_installer_probes_exact_version_and_protocol(monkeypatch,tmp_path):
    run=Mock(return_value=SimpleNamespace(returncode=0,stdout=f'aurora-native {__version__} protocol 1\n'))
    monkeypatch.setattr(native.subprocess,'run',run)
    native.probe(tmp_path/'binary')
    run.return_value.stdout='aurora-native old protocol 1'
    with pytest.raises(RuntimeError,match='version'):native.probe(tmp_path/'binary')


def test_invalid_export_cannot_hide_installed_renderer(tmp_path,monkeypatch):
    target=tmp_path/'aurora-native';target.write_bytes(b'fixture');target.chmod(0o755)
    monkeypatch.setenv('JOBIA_NATIVE_BIN',str(tmp_path/'absent'))
    monkeypatch.setattr(native,'managed_binary',lambda:target)
    assert renderer.native_binary()==str(target)


def test_unknown_architecture_does_not_download():
    with pytest.raises(RuntimeError,match='non distribué'):native.target_triple('Linux','riscv64')
