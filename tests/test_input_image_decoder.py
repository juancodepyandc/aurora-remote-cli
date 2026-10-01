"""Real supplied-image decoding tests, also run inside the Pillow runtime."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aurora_cli.core import image_worker, pipeline


@pytest.fixture
def image_module():
    return pytest.importorskip('PIL.Image')


def test_inspects_uniform_reference_without_applying_generation_criteria(image_module, tmp_path):
    source = tmp_path / 'minimal reference.png'
    image_module.new('RGB', (17, 23), 'white').save(source)
    original = source.read_bytes()
    assert image_worker.inspect_input_image(source) == dict(
        format='PNG', width=17, height=23, mode='RGB', frames=1)
    assert source.read_bytes() == original


def test_real_snapshot_uses_decoder_without_loading_diffusion(image_module, monkeypatch, tmp_path):
    source = tmp_path / 'my source.png'
    image_module.new('RGB', (96, 128), 'orange').save(source)
    destination = tmp_path / 'reference-input.png'
    monkeypatch.setattr(pipeline, 'python_engine', lambda *a: Path(sys.executable))
    result = pipeline.stage_import_reference(source, destination, expected_sha256=pipeline._digest(source))
    assert result.status == 'done'
    assert destination.read_bytes() == source.read_bytes()
    evidence = json.loads(result.log)
    assert evidence['generated'] is False and evidence['image']['width'] == 96


def test_real_decoder_rejects_forged_png(image_module, monkeypatch, tmp_path):
    source = tmp_path / 'not an image.png'
    source.write_bytes(b'this file is not PNG data')
    monkeypatch.setattr(pipeline, 'python_engine', lambda *a: Path(sys.executable))
    result = pipeline.stage_import_reference(source, tmp_path / 'reference.png', expected_sha256=pipeline._digest(source))
    assert result.status == 'failed' and result.error_kind == 'input_validation'
    assert 'remplacement' in result.log


def test_real_decoder_rejects_truncated_image_pixels(image_module, tmp_path):
    source = tmp_path / 'truncated.bmp'
    image_module.new('RGB', (96, 128), 'red').save(source)
    source.write_bytes(source.read_bytes()[:-1000])
    with pytest.raises((OSError, ValueError)):
        image_worker.inspect_input_image(source)


def test_transparent_reference_has_no_reconstructible_subject(image_module, tmp_path):
    source = tmp_path / 'empty.png'
    image_module.new('RGBA', (96, 128), (255, 0, 0, 0)).save(source)
    with pytest.raises(ValueError, match='transparent'):
        image_worker.inspect_input_image(source)


def test_animated_reference_requires_explicit_frame(image_module, tmp_path):
    source = tmp_path / 'animated.gif'
    image_module.new('RGB', (96, 128), 'red').save(source, save_all=True,
        append_images=[image_module.new('RGB', (96, 128), 'blue')], duration=100)
    with pytest.raises(ValueError, match='animée'):
        image_worker.inspect_input_image(source)


def test_exif_orientation_is_taken_into_account_without_mutating_source(image_module, tmp_path):
    source = tmp_path / 'camera.jpg'
    exif = image_module.Exif()
    exif[274] = 6
    image_module.new('RGB', (96, 128), 'red').save(source, exif=exif)
    original = source.read_bytes()
    metadata = image_worker.inspect_input_image(source)
    assert (metadata['width'], metadata['height']) == (128, 96)
    assert source.read_bytes() == original


def test_inspector_entrypoint_is_standalone_and_has_no_torch_requirement(image_module, tmp_path):
    source = tmp_path / 'reference with spaces.png'
    image_module.new('RGB', (96, 128), 'blue').save(source)
    result = subprocess.run([sys.executable, str(Path(image_worker.__file__)), '--inspect-image', str(source)],
                            capture_output=True, text=True, cwd=tmp_path, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['height'] == 128
