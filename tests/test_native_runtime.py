import json
import hashlib
from types import SimpleNamespace
from pathlib import Path
import pytest
from aurora_cli.adapters import AdapterRegistry
from aurora_cli.core import native_runtime, trellis_worker
from test_model_selection import host


def test_native_requires_effective_cuda_toolkit(monkeypatch):
    monkeypatch.setattr(native_runtime.shutil, 'which', lambda _: None)
    monkeypatch.setattr(native_runtime, '_checked', lambda *a, **kw: pytest.fail('Unexpected install'))
    runner = AdapterRegistry().runners['trellis2']
    with pytest.raises(RuntimeError, match='nvcc'):
        native_runtime.ensure_native(runner, 'microsoft/TRELLIS.2-4B', machine=host())


def test_source_revision_is_immutable(tmp_path):
    with pytest.raises(ValueError, match='immuable'):
        native_runtime.clone_reviewed('https://example.org/source.git', 'main', tmp_path / 'source', tmp_path, 'test')


def test_pinned_component_downloads_do_not_overwrite_another_revision():
    from aurora_cli.core.catalog import Artifact
    from aurora_cli.core.fetcher import hf_target_dir
    first = Artifact('3d', 'component', 'huggingface', 'vendor/model', 'model', revision='a'*40)
    second = Artifact('3d', 'component', 'huggingface', 'vendor/model', 'model', revision='b'*40)
    assert hf_target_dir(first) != hf_target_dir(second)


def test_dirty_or_different_sources_are_not_overwritten(monkeypatch, tmp_path):
    revision = 'a' * 40
    target = tmp_path / 'repo'
    target.mkdir()
    commands = []
    def check(command, *args, **kwargs):
        commands.append(command)
        return revision if 'rev-parse' in command else 'wrong/repository'
    monkeypatch.setattr(native_runtime, '_checked', check)
    with pytest.raises(RuntimeError, match='Aucun|aucun'):
        native_runtime.clone_reviewed('https://example.org/source.git', revision, target, tmp_path, 'test')
    assert not any('checkout' in c or 'reset' in c for c in commands)


def test_failed_install_never_writes_ready_receipt(monkeypatch, tmp_path):
    from aurora_cli.core import locations
    monkeypatch.setattr(locations, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(native_runtime.shutil, 'which', lambda _: '/nvcc')
    monkeypatch.setattr(native_runtime, 'clone_reviewed', lambda *a: (_ for _ in ()).throw(RuntimeError('build failed')))
    with pytest.raises(RuntimeError, match='build failed'):
        native_runtime.ensure_native(AdapterRegistry().runners['trellis2'], 'microsoft/TRELLIS.2-4B', machine=host())
    assert not list(tmp_path.rglob('runtime.json'))
    assert not list(tmp_path.rglob('.install.lock'))


def test_pbr_export_preserves_material_attributes_and_png(tmp_path):
    recorded = {}
    def export(path, **kwargs):
        recorded.update(export_options=kwargs)
        Path(path).write_bytes(b'fixture')
    def to_glb(**kwargs):
        recorded.update(kwargs)
        return SimpleNamespace(export=export)
    mesh = SimpleNamespace(vertices='vertices', faces='faces', attrs='PBR-volume',
        coords='coords', layout='PBR-layout', voxel_size=.01)
    trellis_worker.export_pbr(mesh, tmp_path / 'asset.glb', postprocess=SimpleNamespace(to_glb=to_glb),
                             texture_size=2048, target_faces=100000)
    assert recorded['attr_volume'] == 'PBR-volume' and recorded['attr_layout'] == 'PBR-layout'
    assert recorded['texture_size'] == 2048 and recorded['export_options'] == {'extension_webp': False}


def test_native_conditioning_is_retained_and_not_run_twice(tmp_path):
    calls = []
    original = object()
    class Conditioned:
        def save(self, path):
            Path(path).write_bytes(b'native conditioning fixture')
    conditioned = Conditioned()
    def preprocess(image):
        assert image is original
        calls.append('preprocess')
        return conditioned
    def run(image, **kwargs):
        assert image is conditioned and kwargs == dict(pipeline_type='1536_cascade', preprocess_image=False)
        calls.append('run')
        return ['mesh']
    mesh, path = trellis_worker.run_conditioned(SimpleNamespace(preprocess_image=preprocess, run=run),
        original, tmp_path / 'job' / 'model.glb', 1536)
    assert calls == ['preprocess', 'run'] and mesh == 'mesh'
    assert path.name == 'model.conditioning.png' and path.read_bytes() == b'native conditioning fixture'


def test_component_code_is_verified_before_loading(tmp_path):
    source = tmp_path / 'component.py'
    source.write_bytes(b'reviewed fixture')
    runner = SimpleNamespace(dependencies=[{'spec':'vendor/model', 'hashes':{
        'component.py':hashlib.sha256(source.read_bytes()).hexdigest()}}])
    native_runtime.verify_dependencies(runner, {'vendor/model':str(tmp_path)})
    source.write_bytes(b'changed source')
    with pytest.raises(RuntimeError, match='non conforme'):
        native_runtime.verify_dependencies(runner, {'vendor/model':str(tmp_path)})


def test_trellis_local_components_are_complete_and_weights_not_modified(tmp_path):
    weights = tmp_path / 'weights'
    weights.mkdir()
    checkpoint = weights / 'ckpts' / 'shape'
    checkpoint.parent.mkdir()
    for suffix in ('.json', '.safetensors'):
        Path(str(checkpoint) + suffix).write_bytes(b'fixture')
    dependency = tmp_path / 'component'
    dependency.mkdir()
    config = {'args': {'models': {'shape': 'ckpts/shape'},
        'image_cond_model': {'args': {'model_name': 'vendor/conditioner'}},
        'rembg_model': {'args': {'model_name': 'vendor/background'}}}}
    original = json.dumps(config)
    (weights / 'pipeline.json').write_text(original)
    local = trellis_worker.local_pipeline_config(dict(weights=str(weights), dependencies={
        'vendor/conditioner': str(dependency), 'vendor/background': str(dependency)}), tmp_path / 'config')
    resolved = json.loads((local / 'pipeline.json').read_text())
    assert (local / resolved['args']['models']['shape']).resolve() == checkpoint
    assert (weights / 'pipeline.json').read_text() == original
    Path(str(checkpoint) + '.safetensors').unlink()
    with pytest.raises(RuntimeError, match='incomplet'):
        trellis_worker.local_pipeline_config(dict(weights=str(weights)), tmp_path / 'bad')
