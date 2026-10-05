"""Partial worker diagnostics survive deadlines; failed assets never succeed."""
import asyncio
import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

from click.testing import CliRunner
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
import pytest

from aurora_cli import cli, ui
from aurora_cli.core import engine3d, pipeline


def deadline(command, **kwargs):
    raise subprocess.TimeoutExpired(command, kwargs['timeout'],
        output=b'CUDA driver unavailable\nlast worker progress\n', stderr='worker traceback')


def assert_diagnostic(stage):
    assert stage.status == 'failed' and stage.error_kind == 'timeout'
    assert stage.output is None
    assert 'CUDA driver unavailable' in stage.log and 'worker traceback' in stage.log
    assert 'TimeoutExpired' in stage.log


def test_image_deadline_keeps_partial_worker_output(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.subprocess, 'run', deadline)
    stage = pipeline.stage_generate_image('Original subject', tmp_path / 'image.png',
        model_name='stabilityai/sdxl-turbo', prepared=(tmp_path, Path('/fixture/python')))
    assert_diagnostic(stage)


def test_reference_decoder_timeout_keeps_original_image(monkeypatch, tmp_path):
    source = tmp_path / 'reference.png'
    source.write_bytes(b'original reference fixture')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = tmp_path / 'retained.png'
    monkeypatch.setattr(pipeline, 'python_engine', lambda *args: Path('/fixture/python'))
    monkeypatch.setattr(pipeline.subprocess, 'run', deadline)
    stage = pipeline.stage_import_reference(source, destination, expected_sha256=digest)
    assert_diagnostic(stage)
    assert source.read_bytes() == destination.read_bytes() == b'original reference fixture'


def test_visual_mesh_deadline_retains_renderer_diagnostic(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.subprocess, 'run', deadline)
    stage = pipeline.stage_verify_mesh(tmp_path / 'reference.png', tmp_path / 'mesh.glb',
        prepared=(tmp_path, Path('/fixture/python')), model_name='tencent/Hunyuan3D-2.1')
    assert_diagnostic(stage)


def test_review_preprocessing_timeout_keeps_diagnostic_without_llm_call(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, 'start_ollama', lambda: None)
    monkeypatch.setattr(pipeline, 'find_ollama_model', lambda name: True)
    monkeypatch.setattr(pipeline, '_release_ollama_model', lambda name: None)
    monkeypatch.setattr(pipeline, 'prepare_review_image', lambda *args: deadline(['fixture'], timeout=60))
    stage = pipeline.stage_verify_image(tmp_path / 'image.png', 'Original subject', model_name='fixture:vision')
    assert_diagnostic(stage)


@pytest.mark.parametrize('failure', ['missing_engine', 'missing_image', 'worker'])
def test_generate_3d_failure_exits_nonzero(monkeypatch, tmp_path, failure):
    image = tmp_path / 'reference.png'
    if failure != 'missing_image':
        image.write_bytes(b'fixture')
    engine = None if failure == 'missing_engine' else SimpleNamespace(available=True, name='fixture engine')
    monkeypatch.setattr(engine3d, 'find_hunyuan3d', lambda: engine)
    calls = []
    def failed(*args, **kwargs):
        calls.append(args)
        return False, 'CUDA driver unavailable; retained worker diagnostic'
    monkeypatch.setattr(engine3d, 'generate_mesh', failed)
    result = CliRunner().invoke(cli.main, ['generate-3d', '--image', str(image),
        '--output', str(tmp_path / 'mesh.glb')])
    assert result.exit_code == 1
    assert bool(calls) == (failure == 'worker')
    if failure == 'worker':
        assert 'CUDA driver unavailable' in result.output


@pytest.mark.parametrize('doctor,expected', [
    ({'ok': True, 'ready': False}, 'indisponible ou dégradé'),
    ({'ok': True, 'ready': True, 'gpu_ready': False,
      'checks': [{'name': 'ComfyUI', 'ok': False}]}, 'GPU indisponible'),
    ({'ok': True, 'ready': True, 'checks': [{'name': 'ComfyUI', 'ok': False}]}, 'ComfyUI indisponible'),
    ({'ok': False, 'error': 'ReadTimeout : délai de lecture dépassé'}, 'ReadTimeout'),
])
def test_ui_diagnostic_uses_full_budget_and_surfaces_media_readiness(monkeypatch, doctor, expected):
    monkeypatch.setattr(ui.config, 'is_configured', lambda: True)
    budgets = []
    class Client:
        def __init__(self, **kwargs):
            budgets.append(kwargs['timeout'])
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def doctor(self):
            return doctor
        def models(self):
            return {'models': []}
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(bridge_factory=Client, input=pipe, output=DummyOutput())
            await app.discover()
            assert expected in app.health
            assert 'Pont prêt' not in app.health
    asyncio.run(scenario())
    assert budgets == [30]


def test_ui_warns_about_observed_large_model_without_changing_selection(monkeypatch):
    monkeypatch.setattr(ui.config, 'is_configured', lambda: True)
    monkeypatch.setattr(ui.config, 'get', lambda key, default='': 'chosen:large' if key == 'default_model' else default)
    monkeypatch.setattr(ui.config, 'set', lambda *args: pytest.fail('Diagnosis cannot replace the chosen model'))
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def doctor(self):
            return {'ok': True, 'ready': True, 'gpu_ready': False,
                    'default_model': 'default:small', 'hardware': {'ram_gb': 30},
                    'models': [{'name': 'chosen:large', 'size': 51.7 * 1024 ** 3}]}
        def models(self):
            return {'models': [{'name': 'chosen:large'}]}
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(bridge_factory=Client, input=pipe, output=DummyOutput())
            await app.discover()
            await app.discover()
            assert app.transcript.count('51.7 Gio de poids sur disque') == 1
            assert '30.0 Gio de RAM, GPU indisponible' in app.transcript
            assert 'reste à mesurer' in app.transcript
    asyncio.run(scenario())


def test_doctor_json_keeps_remote_memory_and_timeout_evidence(monkeypatch):
    client = SimpleNamespace(close=lambda: None, doctor=lambda: {
        'ok': False, 'ready': False, 'gpu_ready': False,
        'hardware': {'ram_gb': 30, 'ram_available_gb': 27, 'vram_total_gb': 0},
        'default_model': 'observed:small', 'models': [{'name': 'observed:small', 'size': 123}],
        'error_kind': 'timeout', 'error_type': 'ReadTimeout', 'timeout_seconds': 30,
        'error': 'ReadTimeout : délai de lecture dépassé',
    })
    monkeypatch.setattr(cli, '_client', lambda: client)
    result = CliRunner().invoke(cli.main, ['doctor', '--remote', '--json'])
    import json
    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload['hardware']['ram_available_gb'] == 27
    assert payload['default_model'] == 'observed:small' and payload['gpu_ready'] is False
    assert payload['error_type'] == 'ReadTimeout' and payload['timeout_seconds'] == 30
