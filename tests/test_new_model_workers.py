from types import SimpleNamespace
from pathlib import Path
import pytest
from aurora_cli.core.image_worker import generation_settings, load_diffusion_pipeline, supported_call


def test_flux_klein_has_distilled_settings_not_flux1_dev_settings():
    settings = generation_settings('opaque-cache', {'_class_name': 'Flux2KleinPipeline'})
    assert settings['num_inference_steps'] == 4
    assert settings['guidance_scale'] == 1
    assert settings['width'] == 1024


def test_klein_requires_its_actual_installed_pipeline_class(tmp_path):
    with pytest.raises(RuntimeError, match='0.37'):
        load_diffusion_pipeline(tmp_path, {'_class_name': 'Flux2KleinPipeline'}, device='cuda',
            torch_module=SimpleNamespace(), diffusers_module=SimpleNamespace())


def test_flux_passes_bfloat16_and_local_weights_only(tmp_path):
    calls = []
    loader = SimpleNamespace(from_pretrained=lambda path, **kw: calls.append((path,kw)) or 'pipeline')
    assert load_diffusion_pipeline(tmp_path, {'_class_name': 'Flux2KleinPipeline'}, device='cuda',
        torch_module=SimpleNamespace(bfloat16='bf16'), diffusers_module=SimpleNamespace(Flux2KleinPipeline=loader)) == 'pipeline'
    assert calls == [(str(tmp_path), {'torch_dtype':'bf16', 'local_files_only':True})]


def test_remote_pipeline_code_is_not_loaded(tmp_path):
    with pytest.raises(RuntimeError, match='personnalisé'):
        load_diffusion_pipeline(tmp_path, {'custom_pipeline': 'malicious.py'}, device='cpu',
            torch_module=None, diffusers_module=None)


def test_negative_conditioning_is_not_sent_to_unsupported_pipeline():
    class Pipe:
        def __call__(self, prompt, width):
            pass
    assert supported_call(Pipe(), {'prompt':'test', 'width':1024, 'negative_prompt':'noise'}) == {'prompt':'test', 'width':1024}
    with pytest.raises(RuntimeError, match='incompatibles'):
        supported_call(Pipe(), {'prompt':'test', 'unknown_parameter':True})
