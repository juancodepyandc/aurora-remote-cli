"""Selection must mean a compatible recipe, not an attractive model name."""
from types import SimpleNamespace
import pytest
from click.testing import CliRunner

from aurora_cli.adapters import AdapterRegistry
from aurora_cli.core import catalog, model_selection as selection, pipeline
from aurora_cli.core.machine import MachineProfile


def host(**values):
    return MachineProfile(**(dict(os_name='Linux', arch='x86_64', accelerator='NVIDIA CUDA',
        vram_gb=24, total_ram_gb=64, free_ram_gb=48, free_disk_gb=200) | values))


def options(role, machine, **kwargs):
    return selection.choices(role, machine=machine, installed_probe=lambda _: False, **kwargs)


def test_cuda_recipe_selects_trellis_with_textures():
    accepted, rejected = options('3d', host(), require_textures=True)
    assert accepted[0].artifact.ref == 'microsoft/TRELLIS.2-4B'
    assert accepted[0].runner_id == 'trellis2'
    assert accepted[0].to_dict()['quality_status'] == 'catalogue_preference'


@pytest.mark.parametrize('values', [
    dict(os_name='Darwin', accelerator='Apple Metal', unified_memory=True, vram_gb=64),
    dict(os_name='Windows'), dict(vram_gb=16), dict(accelerator='AMD ROCm'),
    dict(accelerator='CPU', vram_gb=0), dict(os_name='Unknown'),
])
def test_trellis_is_not_selected_on_incompatible_host(values):
    accepted, rejected = options('3d', host(**values))
    assert all(c.runner_id != 'trellis2' for c in accepted)
    assert rejected['microsoft/TRELLIS.2-4B']


def test_low_current_ram_selects_small_recipe_not_total_ram():
    accepted, _ = options('image', host(free_ram_gb=11))
    assert accepted[0].artifact.ref == 'stabilityai/sdxl-turbo'
    assert all(c.artifact.ram_gb <= 9 for c in accepted)


def test_no_fake_two_gigabyte_floor_when_ram_is_exhausted():
    accepted, _ = options('speech', host(free_ram_gb=1))
    assert accepted == []
    assert catalog.usable_ram_gb(host(free_ram_gb=1)) == 0


def test_disk_is_checked_before_installing():
    accepted, rejected = options('image', host(free_disk_gb=3))
    assert not accepted and all('disque' in reason for reason in rejected.values())


def test_existing_installation_does_not_need_download_space():
    accepted, _ = selection.choices('image', machine=host(free_disk_gb=0), installed_probe=lambda _: True)
    assert accepted


def test_proven_metric_can_rank_but_is_not_universal_quality():
    candidate = SimpleNamespace(spec='stabilityai/sdxl-turbo', score=.9)
    accepted, _ = options('image', host(), proven=candidate)
    assert accepted[0].artifact.ref == candidate.spec
    assert accepted[0].to_dict()['quality_status'] == 'evaluated_metric_not_universal_quality'


def test_failure_memory_is_environment_scoped_and_expires(tmp_path):
    now = [100]
    memory = selection.SelectionMemory(tmp_path / 'failures.json', clock=lambda: now[0])
    memory.failure(host(), 'image', 'stabilityai/sdxl-turbo', 'OOM', ttl_s=60)
    assert memory.blocked(host(), 'image', 'stabilityai/sdxl-turbo') == 'OOM'
    assert not memory.blocked(host(accelerator='Apple Metal'), 'image', 'stabilityai/sdxl-turbo')
    now[0] = 161
    assert not memory.blocked(host(), 'image', 'stabilityai/sdxl-turbo')


def test_semantic_failure_does_not_condemn_model_for_other_subjects(tmp_path):
    memory = selection.SelectionMemory(tmp_path / 'memory.json')
    memory.failure(host(), '3d', 'microsoft/TRELLIS.2-4B', 'wrong silhouette', context='fixture-A')
    accepted, _ = options('3d', host(), memory=memory, context='fixture-B')
    assert accepted[0].runner_id == 'trellis2'
    accepted, rejected = options('3d', host(), memory=memory, context='fixture-A')
    assert accepted[0].runner_id == 'hunyuan3d'
    assert rejected['microsoft/TRELLIS.2-4B'] == 'wrong silhouette'


@pytest.mark.parametrize('role', ['code', 'resume', 'vision', 'audio', 'speech'])
def test_shared_policy_resolves_multiple_domains(role):
    accepted, _ = options(role, host())
    assert accepted and AdapterRegistry().runners[accepted[0].runner_id]


def test_music_is_not_whisper_or_a_fictional_tts_engine():
    accepted, _ = options('musique', host())
    assert accepted == []


def test_tts_and_stt_are_different_runners():
    assert options('speech', host())[0][0].runner_id == 'vits-french'
    assert options('audio', host())[0][0].runner_id == 'whisper'


def test_native_model_is_never_prepared_with_hunyuan(monkeypatch, tmp_path):
    from aurora_cli.core import native_runtime
    calls = []
    monkeypatch.setattr(pipeline, 'profile', lambda: host())
    monkeypatch.setattr(pipeline, 'find_model', lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, 'ensure_3d_engine', lambda: pytest.fail('Wrong engine'))
    monkeypatch.setattr(native_runtime, 'ensure_native', lambda runner, spec:
        calls.append((runner.id, spec)) or (tmp_path, tmp_path / 'python'))
    assert pipeline.ensure_model('microsoft/TRELLIS.2-4B', '3d')[0] == tmp_path
    assert calls == [('trellis2', 'microsoft/TRELLIS.2-4B')]


def test_native_incompatibility_stops_before_download(monkeypatch):
    monkeypatch.setattr(pipeline, 'profile', lambda: host(os_name='Darwin', unified_memory=True))
    monkeypatch.setattr(pipeline, 'find_model', lambda *a, **kw: pytest.fail('No download/search needed'))
    with pytest.raises(RuntimeError, match='Systèmes'):
        pipeline.ensure_model('microsoft/TRELLIS.2-4B', '3d')


def test_choose_cli_is_read_only_and_reports_rejections(monkeypatch):
    from aurora_cli.cli import main
    monkeypatch.setattr(selection, 'profile', lambda: host(os_name='Darwin', accelerator='Apple Metal', unified_memory=True))
    monkeypatch.setattr(selection, 'installed', lambda _: False)
    monkeypatch.setattr(pipeline, 'ensure_model', lambda *a, **kw: pytest.fail('Read-only command installed something'))
    result = CliRunner().invoke(main, ['choose', '3d', '--json'])
    assert result.exit_code == 0, result.output
    assert 'trellis2' not in result.output
    assert 'microsoft/TRELLIS.2-4B' in result.output and 'Systèmes' in result.output
