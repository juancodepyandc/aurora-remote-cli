from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from click.testing import CliRunner
from aurora_cli import workspace
from aurora_cli.cli import main
from aurora_cli.core import fetcher, intent, upstream
from aurora_cli.core.catalog import Artifact
from aurora_cli.core.discovery import ScanResult
from aurora_cli.core.providers import ModelInfo, ProviderInfo, ProviderKind, StreamChunk
from aurora_cli.core.router import Router


def test_bare_jobia_launches_workspace_in_terminal(monkeypatch):
    called = Mock()
    monkeypatch.setattr(workspace, 'run_workspace', called)
    import sys
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
    with __import__('click').Context(main) as ctx:
        ctx.invoke(main, theme_opt='', color='auto', anim_opt='none')
    called.assert_called_once()


def test_pipe_renders_dashboard_without_waiting():
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, result.output
    assert 'CRÉER' in result.output
    assert '/theme' in result.output


@pytest.mark.parametrize('prompt', ['dessine Naruto', 'crée un personnage inconnu', 'fais un perso inventé'])
def test_character_requests_select_image(prompt):
    assert 'image' in {a.id for a, _ in intent.match_agents(prompt)}


def test_unrelated_llm_is_not_a_3d_generator():
    from aurora_cli.core.agents import get
    assert not intent._covers(get('3d'), ModelInfo(name='qwen', capability='llm'))


def test_satisfied_step_never_downloads_again():
    from aurora_cli.core.agents import get
    step = intent.Step(agent=get('image'), artifact=Artifact('image', 'light', 'huggingface', 'a/b', 'B'),
                       satisfied_by=[ModelInfo(name='local')], extra_refs=('a/c',))
    assert intent.Recommendation('image', [step], None).planned_downloads() == []


def test_empty_ollama_does_not_hide_remote_fallback():
    router = Router(result=ScanResult(providers=[ProviderInfo(id='o', label='O', kind=ProviderKind.LOCAL, healthy=True)], loose_models=[]))
    router.note_remote(True)
    assert router.route().kind == 'remote'


def test_unverified_runtime_cannot_claim_install_success(monkeypatch):
    monkeypatch.setattr(fetcher.subprocess, 'run', Mock(side_effect=OSError()))
    assert not fetcher._ollama_has('qwen:7b')


def test_ollama_requires_exact_tag(monkeypatch):
    monkeypatch.setattr(fetcher.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='NAME ID SIZE\nqwen:14b abc 9GB\n'))
    assert not fetcher._ollama_has('qwen:7b')
    assert fetcher._ollama_has('qwen:14b')


def test_empty_component_folder_is_not_verified(tmp_path):
    (tmp_path / 'weights').mkdir()
    plan = fetcher.Plan(Artifact('image', 'light', 'huggingface', 'a/b', 'B', include=('weights/*',)),
                        tmp_path, 1, [])
    with pytest.raises(fetcher.ProvisionError, match='Pièce manquante|sans contenu'):
        fetcher.verify(plan)


def test_existing_files_do_not_skip_resume(tmp_path):
    (tmp_path / 'partial.safetensors').write_bytes(b'partial')
    plan = fetcher.Plan(Artifact('image', 'light', 'huggingface', 'a/b', 'B'), tmp_path, 1, [])
    assert not fetcher._already_complete(plan)


@pytest.mark.parametrize('path', ['../secret.py', '.env', '.claude/config.py', '/etc/x.py', 'README.md'])
def test_source_rejects_private_or_irrelevant_files(path):
    with pytest.raises(ValueError):
        upstream.fetch_source(path)


def test_runtime_errors_are_not_saved_as_answers(monkeypatch):
    runtime = SimpleNamespace(stream=lambda *a, **kw: iter([StreamChunk(error='out of memory')]))
    route = SimpleNamespace(kind='local', ok=True, models=[ModelInfo(name='qwen')],
                            runtime=runtime, target='Ollama')
    router = SimpleNamespace(note_remote=lambda x: None, route=lambda: route, pick_model=lambda **kw: 'qwen')
    monkeypatch.setattr(workspace, 'scan', lambda **k: ScanResult(providers=[], loose_models=[]))
    monkeypatch.setattr(workspace, 'profile', lambda: SimpleNamespace(total_ram_gb=24, free_ram_gb=20, under_pressure=False))
    monkeypatch.setattr(workspace, 'Router', lambda **kw: router)
    history = []
    with pytest.raises(RuntimeError, match='out of memory'):
        workspace.execute('bonjour', history)
    assert history == []


def test_close_refuses_self_before_confirm(monkeypatch):
    import os
    from aurora_cli.core import apps
    confirm = Mock()
    monkeypatch.setattr(apps.click, 'confirm', confirm)
    with pytest.raises(ValueError):
        apps.close_app(os.getpid())
    confirm.assert_not_called()


def test_remote_bridge_never_receives_the_local_archive_uuid(monkeypatch):
    from aurora_cli import bridge, mission
    calls = []
    monkeypatch.setattr(bridge, 'Bridge', lambda: 'bridge-client')
    monkeypatch.setattr(mission, 'run_mission', lambda client, request, **kwargs:
                        calls.append((client, request, kwargs)) or True)
    history = SimpleNamespace(session_id='local-only-uuid', state={})
    assert workspace._remote_mission('request', history)
    assert calls[0][0] == 'bridge-client' and calls[0][2]['session_id'] == ''


@pytest.mark.parametrize('mode', ['local', 'auto'])
def test_workspace_input_image_uses_direct_local_pipeline_and_explicit_ui(monkeypatch, tmp_path, mode):
    from aurora_cli.core import pipeline
    from aurora_cli.core.agents import get
    source = tmp_path / 'input with spaces.png'
    source.write_bytes(b'fixture for routing only')
    monkeypatch.setattr(workspace, 'scan', lambda **kw: ScanResult(providers=[], loose_models=[]))
    monkeypatch.setattr(workspace, 'profile', lambda: object())
    monkeypatch.setattr(workspace, 'recommend', lambda *a: SimpleNamespace(
        steps=[SimpleNamespace(agent=get('3d'))]))
    monkeypatch.setattr(workspace.config, 'get', lambda *a: mode)
    monkeypatch.setattr(workspace.config, 'is_configured', lambda: True)
    monkeypatch.setattr(workspace, '_remote_mission', lambda *a:
                        pytest.fail('A local input cannot be sent as a path-only remote request'))
    messages, calls = [], []
    for method in ('info', 'hint', 'warning', 'success'):
        monkeypatch.setattr(workspace.display, method, messages.append)
    monkeypatch.setattr(pipeline, 'run_pipeline', lambda request, output_dir, **kw:
        calls.append((request, output_dir, kw)) or pipeline.PipelineResult(False))
    request = f'génère moi cette image en modèle 3d : "{source}"'
    history = []
    workspace.execute(request, history)
    assert calls[0][0] == request
    assert any(f'Image d’entrée : {source}' == message for message in messages)
    assert any('Image fournie → forme 3D' in message for message in messages)
    assert not any('Préparation → référence vérifiée' in message for message in messages)
    assert history[-1]['content'].startswith('3D : non livrée')


def test_same_repository_preserves_both_component_subsets():
    from aurora_cli.core.catalog import merge_downloads
    a = Artifact('3d', 'balanced', 'huggingface', 'a/b', 'shape', include=('shape/*',))
    b = Artifact('3d-texture', 'balanced', 'huggingface', 'a/b', 'paint', include=('paint/*',))
    merged = merge_downloads([a, b])
    assert len(merged) == 1
    assert merged[0].include == ('shape/*', 'paint/*')


def test_cache_metadata_alone_does_not_verify_model(tmp_path):
    (tmp_path / '.cache').mkdir()
    (tmp_path / '.cache' / 'download.json').write_text('{}')
    plan = fetcher.Plan(Artifact('image', 'light', 'huggingface', 'a/b', 'B'), tmp_path, 1, [])
    with pytest.raises(fetcher.ProvisionError, match='sans contenu'):
        fetcher.verify(plan)


def test_conversation_has_a_real_installable_catalogue():
    from aurora_cli.core.catalog import for_agent
    from aurora_cli.core.agents import get
    choices = for_agent(get('chat'))
    assert choices and all(a.runtime == 'ollama' and a.agent_id == 'chat' for a in choices)


def test_local_image_adapter_reuses_pipeline_and_checks_output(monkeypatch, tmp_path):
    from aurora_cli.core import images
    model = tmp_path / 'existing-sdxl'
    model.mkdir()
    (model / 'model_index.json').write_text('{}')
    monkeypatch.setattr(images, 'profile', lambda: SimpleNamespace(free_ram_gb=20))
    install = Mock(side_effect=AssertionError('must reuse existing model'))
    monkeypatch.setattr(images.fetcher, 'install', install)
    from pathlib import Path
    from aurora_cli.core import pipeline, request_spec
    monkeypatch.setattr(request_spec.locations, 'data_dir', lambda: tmp_path / 'data')
    spec = 'stabilityai/stable-diffusion-xl-base-1.0'
    monkeypatch.setattr(pipeline, 'select_image_model', lambda: spec)
    monkeypatch.setattr(pipeline, 'ensure_model', lambda name, capability, **kw: (kw['model_dir'], Path('python')))
    def worker(prompt, output, **kwargs):
        assert kwargs['prepared'][0] == model
        assert kwargs['model_spec'] == spec
        output.write_bytes(b'test-output')
        return pipeline.PipelineStage('image_generation', 'fixture', 'done', output=output)
    monkeypatch.setattr(pipeline, 'stage_generate_image', worker)
    monkeypatch.setattr(pipeline, 'stage_verify_image', lambda *a, **kw:
                        pipeline.PipelineStage('vlm_verification', 'fixture', 'done'))
    result = ScanResult(providers=[], loose_models=[ModelInfo(name=spec, capability='image', path=str(model))])
    assert images.generate('a bicycle', result)
    install.assert_not_called()
