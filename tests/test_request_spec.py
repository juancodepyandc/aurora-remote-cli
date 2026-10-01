from pathlib import Path
import os

import pytest

from aurora_cli.core import request_spec


def test_delivery_not_in_visual_subject(monkeypatch, tmp_path):
    monkeypatch.setattr(request_spec, "documents_dir", lambda: tmp_path)
    req = request_spec.parse_creation_request(
        "génère moi un modèle 3D Nick de Zootopie et mets le dans le dossier Documents")
    assert req.subject == "Nick de Zootopie"
    assert req.output_dir.is_relative_to(tmp_path / "JOBIA")


def test_unrelated_documents_not_delivery():
    req = request_spec.parse_creation_request("un meuble 3D contenant des documents anciens")
    assert req.subject == "un meuble 3D contenant des documents anciens"
    assert "outputs" in req.output_dir.parts


def test_explicit_destination_override_keeps_subject_clean(tmp_path):
    req = request_spec.parse_creation_request("a robot and save it in Documents", tmp_path)
    assert req.subject == "a robot"
    assert req.output_dir == tmp_path


def test_quoted_path_and_same_request_resume(tmp_path):
    prompt = f'crée un modèle 3D de robot et mets le dans "{tmp_path}/my files"'
    req = request_spec.parse_creation_request(prompt)
    assert req.subject == "robot"
    assert req.output_dir.is_relative_to(tmp_path / "my files")
    assert req == request_spec.parse_creation_request(prompt)
    assert req.output_dir != request_spec.parse_creation_request(prompt.replace("robot", "chat")).output_dir


def test_linux_documents_respects_xdg(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(request_spec.sys, "platform", "linux")
    monkeypatch.setattr(request_spec.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=str(tmp_path)))
    assert request_spec.documents_dir() == tmp_path


def test_destination_before_subject_and_model_spelling(monkeypatch, tmp_path):
    monkeypatch.setattr(request_spec, "documents_dir", lambda: tmp_path)
    req = request_spec.parse_creation_request(
        "génère moi dans Documents un model 3d figurine de spiderman version noel")
    assert req.subject == "figurine de spiderman version noel"
    assert req.output_dir.is_relative_to(tmp_path / "JOBIA")


def test_destination_with_quoted_path_before_subject(tmp_path):
    req = request_spec.parse_creation_request(f'create in "{tmp_path}/my objects" a 3d model of a robot')
    assert req.subject == "a robot"
    assert req.output_dir.is_relative_to(tmp_path / "my objects")


def test_image_destination_before_subject(monkeypatch, tmp_path):
    monkeypatch.setattr(request_spec, 'documents_dir', lambda: tmp_path)
    req = request_spec.parse_creation_request('génère moi dans Documents une image de dragon inventé', capability='image')
    assert req.subject == 'dragon inventé'
    assert req.output_dir.is_relative_to(tmp_path / 'JOBIA')
    assert req.output_dir.name.startswith('image-')


@pytest.mark.parametrize('template', [
    'génère moi cette image en modèle 3d : {path}',
    'convert this picture to a 3d model: "{path}"',
    "crée un maillage depuis '{path}'",
    'mesh from {path}',
])
def test_source_image_is_not_a_visual_prompt_or_output_directory(tmp_path, template):
    path = tmp_path / 'mes images' / 'référence inventée.png'
    request = request_spec.parse_creation_request(template.format(path=path))
    assert request.input_image == path
    assert str(path) not in request.subject
    assert request.output_dir != path


def test_reference_and_delivery_remain_separate(monkeypatch, tmp_path):
    monkeypatch.setattr(request_spec, 'documents_dir', lambda: tmp_path / 'docs')
    path = tmp_path / 'entrée.png'
    request = request_spec.parse_creation_request(f'génère dans Documents cette image en modèle 3d : "{path}"')
    assert request.input_image == path
    assert request.output_dir.is_relative_to(tmp_path / 'docs' / 'JOBIA')
    assert 'Documents' not in request.subject


def test_source_first_and_quoted_delivery(tmp_path):
    path = tmp_path / 'image.jpeg'
    request = request_spec.parse_creation_request(f'convert "{path}" to 3d and save it in "{tmp_path}/out"')
    assert request.input_image == path
    assert request.output_dir.is_relative_to(tmp_path / 'out')


def test_copied_path_wrap_after_directory_separator_is_rejoined(tmp_path):
    path = tmp_path / 'references' / 'candidate-1.png'
    request = request_spec.parse_creation_request(f'cette image en 3d : {path.parent}/\n  {path.name}')
    assert request.input_image == path


@pytest.mark.parametrize('source', ['./reference.png', 'reference.jpg', '../photo.webp'])
@pytest.mark.parametrize('template', ['convert this image to 3d: {source}', 'mesh from {source}'])
def test_relative_image_path_resolves_from_working_directory(monkeypatch, tmp_path, source, template):
    monkeypatch.chdir(tmp_path)
    request = request_spec.parse_creation_request(template.format(source=source))
    assert request.input_image == (tmp_path / source).resolve()


def test_source_path_at_sentence_end_keeps_its_extension(tmp_path):
    source = tmp_path / 'reference.PNG'
    assert request_spec.parse_creation_request(f'convert this image to 3d : {source}.').input_image == source


def test_missing_source_is_still_an_input_not_a_text_generation(tmp_path):
    source = tmp_path / 'missing.png'
    assert request_spec.parse_creation_request(f'cette image en 3d : {source}').input_image == source


def test_bare_filename_on_an_object_is_not_an_attachment():
    assert request_spec.parse_creation_request('create a 3d box labelled logo.png').input_image is None


def test_multiple_references_require_an_explicit_choice(tmp_path):
    with pytest.raises(ValueError, match='Plusieurs images'):
        request_spec.parse_creation_request(f'3d from "{tmp_path}/a.png" and "{tmp_path}/b.png"')


def test_explicit_image_input_api_with_no_description(tmp_path):
    request = request_spec.parse_creation_request('', tmp_path / 'out', input_image=tmp_path / 'a.png')
    assert request.input_image == tmp_path / 'a.png'
    assert 'Reconstruction' in request.subject


def test_conflicting_image_input_cannot_be_silently_substituted(tmp_path):
    with pytest.raises(ValueError, match='diffère'):
        request_spec.parse_creation_request(f'3d from {tmp_path}/a.png', input_image=tmp_path / 'b.png')


@pytest.mark.skipif(os.name == 'nt', reason='Windows paths are native on Windows')
@pytest.mark.parametrize('path', [r'C:\Users\alice\Pictures\reference.png', r'\\server\share\reference.png'])
def test_foreign_windows_path_fails_instead_of_becoming_a_local_prompt(path):
    with pytest.raises(ValueError, match='Windows inaccessible'):
        request_spec.parse_creation_request(f'convert this image to 3d : {path}')


@pytest.mark.parametrize('prompt', [
    'génère moi cette image en modèle 3d',
    'convert this image to 3d : https://example.com/image.png',
    'modèle 3d de la référence jointe : unknown-format.jxl',
])
def test_unresolved_explicit_reference_is_never_treated_as_a_text_creation(prompt):
    with pytest.raises(ValueError, match='remplacement'):
        request_spec.parse_creation_request(prompt)
