"""Failure and resume contracts at the image-to-mesh boundary."""
import base64
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from aurora_cli.core import pipeline, reference_brief


@pytest.fixture(autouse=True)
def isolate_review_selection(monkeypatch):
    # Orchestration tests must not depend on a live model's current RAM usage.
    monkeypatch.setattr(pipeline, 'select_vision_model', lambda: 'fixture-vision')
    def select_3d(**kwargs):
        if kwargs.get('excluded'):
            raise RuntimeError('No further fixture recipes')
        return 'tencent/Hunyuan3D-2.1'
    monkeypatch.setattr(pipeline, 'select_3d_model', select_3d)
    monkeypatch.setattr(pipeline, 'prepare_review_image', lambda path, size: path)


def glb_bytes(*, textured=False, edit=None):
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    uvs = struct.pack("<6f", 0, 0, 1, 0, 0, 1)
    binary = positions + uvs
    primitive = {"attributes": {"POSITION": 0}}
    doc = {
        "asset": {"version": "2.0"}, "scene": 0,
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [primitive]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteLength": len(positions)},
                        {"buffer": 0, "byteOffset": len(positions), "byteLength": len(uvs)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                      {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"}],
    }
    if textured:
        # One valid embedded 1x1 PNG; texture must be connected to a primitive.
        png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aE8sAAAAASUVORK5CYII="
        primitive.update(material=0)
        primitive["attributes"]["TEXCOORD_0"] = 1
        doc.update(materials=[{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
                   textures=[{"source": 0}], images=[{"uri": "data:image/png;base64," + png}])
    if edit:
        edit(doc)
    payload = json.dumps(doc).encode()
    payload += b" " * (-len(payload) % 4)
    binary += b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<I4s", len(payload), b"JSON") + payload
    chunks += struct.pack("<I4s", len(binary), b"BIN\0") + binary
    return struct.pack("<4sII", b"glTF", 2, len(chunks) + 12) + chunks


@pytest.mark.parametrize("textured", [False, True])
def test_real_glb_mesh_and_connected_texture_validate(tmp_path, textured):
    path = tmp_path / "triangle.glb"
    path.write_bytes(glb_bytes(textured=textured))
    assert pipeline.stage_validate_mesh(path, require_textures=textured).status == "done"


@pytest.mark.parametrize("edit", [
    lambda d: d["meshes"][0]["primitives"][0].pop("material"),
    lambda d: d["meshes"][0]["primitives"][0]["attributes"].pop("TEXCOORD_0"),
    lambda d: d["images"][0].update(uri="texture.png"),
    lambda d: d["textures"][0].update(source=99),
    lambda d: d["accessors"][0].update(count=300),
    lambda d: d["nodes"][0].update(mesh=99),
])
def test_invalid_glb_connections_never_pass_on_texture_keywords(tmp_path, edit):
    path = tmp_path / "bad.glb"
    path.write_bytes(glb_bytes(textured=True, edit=edit))
    assert pipeline.stage_validate_mesh(path, require_textures=True).status == "failed"


@pytest.mark.parametrize("verdict", [
    '{"match": false, "reason": "Wrong character"}',
    '{"match": "true", "reason": "Wrong type"}',
    '{"reason": "Looks good"}', 'This is a different character',
    '{"match": true}', '[]',
])
def test_vlm_rejection_or_malformed_output_is_failure(monkeypatch, tmp_path, verdict):
    import httpx
    path = tmp_path / "image.png"
    path.write_bytes(b"png")
    monkeypatch.setattr(pipeline, "start_ollama", lambda: None)
    monkeypatch.setattr(pipeline, "find_ollama_model", lambda _: True)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"response": verdict, 'done': True, 'done_reason': 'stop'}))
    result = pipeline.stage_verify_image(path, "an otter in a red jacket")
    assert result.status == "failed"
    assert result.error_kind in {"mismatch", "invalid_verdict"}


def test_vlm_acceptance_cannot_contradict_its_own_observations(monkeypatch, tmp_path):
    import httpx
    path = tmp_path / 'image.png'
    path.write_bytes(b'fixture')
    verdict = {'match': True, 'reason': 'ok', 'issues': ['missing hand'], 'checks': [
        {'aspect': f'feature {i}', 'passed': True, 'evidence': 'visible'} for i in range(3)]}
    monkeypatch.setattr(pipeline, 'start_ollama', lambda: None)
    monkeypatch.setattr(pipeline, 'find_ollama_model', lambda _: True)
    monkeypatch.setattr(httpx, 'post', lambda *a, **kw: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {'response': json.dumps(verdict), 'done': True, 'done_reason': 'stop'}))
    result = pipeline.stage_verify_image(path, 'an otter')
    assert result.status == 'failed' and result.error_kind == 'mismatch'


def test_truncated_visual_review_retries_same_image_before_acceptance(monkeypatch, tmp_path):
    import httpx
    path = tmp_path / 'image.png'
    path.write_bytes(b'fixture')
    verdict = {'match': True, 'reason': 'visible features', 'issues': [], 'checks': [
        {'aspect': f'feature {i}', 'passed': True, 'evidence': 'visible'} for i in range(3)]}
    responses = iter([{'response': json.dumps(verdict), 'done': True, 'done_reason': 'length'},
                      {'response': json.dumps(verdict), 'done': True, 'done_reason': 'stop'}])
    calls = []
    def post(*args, **kwargs):
        calls.append(json.loads(json.dumps(kwargs['json'])))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: next(responses))
    monkeypatch.setattr(pipeline, 'start_ollama', lambda: None)
    monkeypatch.setattr(pipeline, 'find_ollama_model', lambda _: True)
    monkeypatch.setattr(pipeline, '_release_ollama_model', lambda _: None)
    monkeypatch.setattr(httpx, 'post', post)
    result = pipeline.stage_verify_image(path, 'an otter', model_name='fixture')
    assert result.status == 'done'
    assert len(calls) == 2 and calls[0]['messages'][0]['images'] == calls[1]['messages'][0]['images']
    assert calls[0]['think'] is False
    assert calls[1]['options']['num_predict'] > calls[0]['options']['num_predict']
    assert 'verification_recovery' in result.log


def test_visual_gate_failure_blocks_final_delivery(monkeypatch, tmp_path):
    setup_pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, 'stage_verify_mesh', lambda *a, **kw:
                        pipeline.PipelineStage('mesh_visual_gate', 'fixture', 'failed', log='misplaced'))
    result = pipeline.run_pipeline('red otter', tmp_path, image_model='fixture')
    assert not result.success
    assert not list(tmp_path.glob('model-*.glb'))


def test_worker_sidecars_resume_with_actual_names(monkeypatch, tmp_path):
    setup_pipeline(monkeypatch, tmp_path)
    calls = []
    def mesh(image, path, **kwargs):
        if calls:
            assert path.with_name('model.shape.glb').read_bytes() == b'previous shape'
            assert path.with_name('model.shape.latents.pt').read_bytes() == b'previous latents'
        else:
            path.with_name('model.shape.glb').write_bytes(b'previous shape')
            path.with_name('model.shape.latents.pt').write_bytes(b'previous latents')
        calls.append(path)
        return pipeline.PipelineStage('3d_generation', 'fixture', 'failed', log='interrupted')
    monkeypatch.setattr(pipeline, 'stage_generate_3d', mesh)
    pipeline.run_pipeline('red otter', tmp_path, image_model='fixture')
    pipeline.run_pipeline('red otter', tmp_path, image_model='fixture')
    assert len(calls) == 2 and calls[0] != calls[1]


def setup_pipeline(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(pipeline, "ensure_model", lambda *a: (tmp_path, Path("python")))
    monkeypatch.setattr(pipeline, "find_ollama_model", lambda *a: False)
    monkeypatch.setattr(reference_brief, "build_reference_brief", lambda subject, **kwargs: {
        "prompt": subject + ' ' + kwargs.get('feedback', ''), "identity": subject,
        "criteria": ["red jacket", "otter", "whole body"],
        "model": "fixture-model"})

    def image(prompt, path, **kwargs):
        calls.append(("image", prompt, kwargs["seed"]))
        path.write_bytes(b"reference" + str(kwargs["seed"]).encode())
        return pipeline.PipelineStage("image_generation", "fixture", "done", output=path)

    def verify(path, description, **kwargs):
        calls.append(("verify", description))
        return pipeline.PipelineStage("vlm_verification", "fixture", "done")

    def mesh(image, path, **kwargs):
        calls.append(("mesh", image))
        path.write_bytes(glb_bytes(textured=True))
        return pipeline.PipelineStage("3d_generation", "fixture", "done", output=path)

    monkeypatch.setattr(pipeline, "stage_generate_image", image)
    monkeypatch.setattr(pipeline, "stage_verify_image", verify)
    monkeypatch.setattr(pipeline, "stage_generate_3d", mesh)
    def visual(reference, mesh, **kwargs):
        proof = mesh.with_suffix('.fidelity.json')
        proof.write_text('{"verdict": "plausible"}')
        return pipeline.PipelineStage('mesh_visual_gate', 'fixture', 'done', output=proof)
    monkeypatch.setattr(pipeline, 'stage_verify_mesh', visual)
    monkeypatch.setattr(pipeline, 'stage_verify_mesh_subject', lambda proof, *a, **kw:
                        pipeline.PipelineStage('mesh_semantic_gate', 'fixture', 'done', output=proof))
    return calls


def test_preflight_error_does_not_regenerate_images(monkeypatch, tmp_path):
    calls = setup_pipeline(monkeypatch, tmp_path)
    preparations = []

    def fail(*args):
        preparations.append(args)
        raise RuntimeError("native runtime unavailable")

    monkeypatch.setattr(pipeline, "ensure_model", fail)
    result = pipeline.run_pipeline("a red otter", tmp_path, image_model="fixture")
    assert not result.success
    assert len(preparations) == 1
    assert calls == []
    assert result.checkpoint.is_file()
    assert "native runtime unavailable" in result.log


def test_delivery_clause_is_absent_from_brief_and_visual_check(monkeypatch, tmp_path):
    calls = setup_pipeline(monkeypatch, tmp_path)
    result = pipeline.run_pipeline("génère moi un modèle 3D Nick de Zootopie et mets le dans le dossier Documents",
                                   tmp_path, image_model="fixture")
    assert result.success
    assert "Documents" not in calls[0][1]
    assert "Documents" not in calls[1][1]
    assert "Nick de Zootopie" in calls[0][1]


def test_rejected_reference_gets_critique_and_different_seed(monkeypatch, tmp_path):
    calls = setup_pipeline(monkeypatch, tmp_path)
    attempts = []

    def verify(*args, **kwargs):
        attempts.append(args)
        if len(attempts) == 1:
            return pipeline.PipelineStage("vlm_verification", "fixture", "failed",
                                           log="missing red jacket", error_kind="mismatch")
        return pipeline.PipelineStage("vlm_verification", "fixture", "done")

    monkeypatch.setattr(pipeline, "stage_verify_image", verify)
    result = pipeline.run_pipeline("red otter", tmp_path, image_model="fixture")
    assert result.success
    images = [c for c in calls if c[0] == "image"]
    assert len(images) == 2 and images[0][2] != images[1][2]
    assert "missing red jacket" in images[1][1]
    assert len(list(tmp_path.glob(".jobia/*/attempt-*/reference.png"))) == 2


def test_visual_failure_can_try_another_recipe_without_claiming_it_is_better(monkeypatch, tmp_path):
    setup_pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, 'select_image_model', lambda: 'first-engine')
    monkeypatch.setattr(pipeline, 'alternative_image_model', lambda excluded:
                        'second-engine' if 'second-engine' not in excluded else None)
    seen = []
    def image(prompt, path, **kwargs):
        seen.append(kwargs['model_name'])
        path.write_bytes(b'fixture')
        return pipeline.PipelineStage('image_generation', kwargs['model_name'], 'done', output=path)
    def verify(*args, **kwargs):
        if len(seen) == 1:
            return pipeline.PipelineStage('vlm_verification', 'fixture', 'failed',
                                          error_kind='mismatch', log='wrong subject')
        return pipeline.PipelineStage('vlm_verification', 'fixture', 'done')
    monkeypatch.setattr(pipeline, 'stage_generate_image', image)
    monkeypatch.setattr(pipeline, 'stage_verify_image', verify)
    result = pipeline.run_pipeline('red otter', tmp_path)
    assert result.success and seen == ['first-engine', 'second-engine']
    assert any(stage.name == 'image_recipe_change' and 'encore à vérifier' in stage.log
               for stage in result.stages)


def test_visual_feedback_does_not_repeat_passed_checks():
    verdict = json.dumps({'reason': 'wrong backdrop', 'issues': ['remove background tree'], 'checks': [
        {'passed': True, 'evidence': 'good red mask'},
        {'passed': False, 'evidence': 'background tree visible'}]})
    correction = pipeline.visual_corrections(verdict)
    assert 'tree' in correction and 'red mask' not in correction and '"passed"' not in correction


def test_mesh_failure_resumes_without_regenerating_accepted_reference(monkeypatch, tmp_path):
    calls = setup_pipeline(monkeypatch, tmp_path)
    working_mesh = pipeline.stage_generate_3d
    monkeypatch.setattr(pipeline, "stage_generate_3d", lambda *a, **kw:
                        pipeline.PipelineStage("3d_generation", "fixture", "failed", log="interrupted"))
    first = pipeline.run_pipeline("red otter", tmp_path, image_model="fixture")
    assert not first.success
    assert len([c for c in calls if c[0] == "image"]) == 1
    monkeypatch.setattr(pipeline, "stage_generate_3d", working_mesh)
    second = pipeline.run_pipeline("red otter", tmp_path, image_model="fixture")
    assert second.success
    assert second.final_output.is_file()
    assert len([c for c in calls if c[0] == "image"]) == 1
    assert len([c for c in calls if c[0] == "verify"]) == 1
    saved = json.loads(second.checkpoint.read_text())
    assert saved["status"] == "completed"
    assert any(Path(stage["log"]).read_text() == "interrupted" for stage in saved["stages"])


def test_finished_job_reuses_validated_delivery(monkeypatch, tmp_path):
    calls = setup_pipeline(monkeypatch, tmp_path)
    first = pipeline.run_pipeline("red otter", tmp_path, image_model="fixture")
    initial = list(calls)
    second = pipeline.run_pipeline("red otter", tmp_path, image_model="fixture")
    assert second.success and second.final_output == first.final_output
    assert calls == initial


def test_missing_visual_proof_invalidates_completed_delivery(monkeypatch, tmp_path):
    calls = setup_pipeline(monkeypatch, tmp_path)
    first = pipeline.run_pipeline('red otter', tmp_path, image_model='fixture')
    state = json.loads(first.checkpoint.read_text())
    (first.checkpoint.parent / state['delivered']['visual_report']).unlink()
    initial = len([call for call in calls if call[0] == 'mesh'])
    assert pipeline.run_pipeline('red otter', tmp_path, image_model='fixture').success
    assert len([call for call in calls if call[0] == 'mesh']) == initial + 1


def test_reviewer_error_resumes_same_reference_without_regeneration(monkeypatch, tmp_path):
    calls = setup_pipeline(monkeypatch, tmp_path)
    working_verify = pipeline.stage_verify_image
    monkeypatch.setattr(pipeline, 'stage_verify_image', lambda *a, **kw:
                        pipeline.PipelineStage('vlm_verification', 'fixture', 'failed',
                                              error_kind='invalid_verdict', log='truncated'))
    first = pipeline.run_pipeline('red otter', tmp_path, image_model='fixture')
    assert not first.success
    assert json.loads(first.checkpoint.read_text())['pending_reference']
    monkeypatch.setattr(pipeline, 'stage_verify_image', working_verify)
    assert pipeline.run_pipeline('red otter', tmp_path, image_model='fixture').success
    assert len([call for call in calls if call[0] == 'image']) == 1


def test_shape_is_retained_for_retry_of_failed_texture(monkeypatch, tmp_path):
    setup_pipeline(monkeypatch, tmp_path)
    attempts = []

    def mesh(image, path, **kwargs):
        attempts.append(path)
        shape = path.with_name("shape.glb")
        state = path.with_name("shape.state.json")
        if len(attempts) == 1:
            shape.write_bytes(b"shape-checkpoint")
            state.write_text('{"sha256": "fixture"}')
            return pipeline.PipelineStage("3d_generation", "fixture", "failed", log="paint failed")
        assert shape.read_bytes() == b"shape-checkpoint"
        assert state.read_text() == '{"sha256": "fixture"}'
        path.write_bytes(glb_bytes(textured=True))
        return pipeline.PipelineStage("3d_generation", "fixture", "done", output=path)

    monkeypatch.setattr(pipeline, "stage_generate_3d", mesh)
    assert not pipeline.run_pipeline("red otter", tmp_path, image_model="fixture").success
    assert pipeline.run_pipeline("red otter", tmp_path, image_model="fixture").success
    assert attempts[0].parent != attempts[1].parent
    assert attempts[0].with_name("shape.glb").read_bytes() == b"shape-checkpoint"


def test_requested_model_is_never_substituted_by_machine_tier(monkeypatch, tmp_path):
    installs = []
    monkeypatch.setattr(pipeline, "find_model", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "python_engine", lambda *a: Path("python"))
    monkeypatch.setattr(pipeline, "ensure_hf", lambda: None)
    monkeypatch.setattr(pipeline, "profile", lambda: object())

    def install(artifact, *args, **kwargs):
        installs.append(artifact.ref)
        return SimpleNamespace(target=tmp_path), ""

    monkeypatch.setattr(pipeline.fetcher, "install", install)
    assert pipeline.ensure_model("black-forest-labs/FLUX.2-klein-4B", "image")[0] == tmp_path
    assert installs == ["black-forest-labs/FLUX.2-klein-4B"]


def test_unknown_engine_is_refused_before_any_install(monkeypatch):
    monkeypatch.setattr(pipeline.fetcher, 'install', lambda *a, **kw: pytest.fail('Unexpected download'))
    with pytest.raises(RuntimeError, match='no runner declares'):
        pipeline.ensure_model('author/unsupported-model', 'image')


def test_3d_recipe_replacement_preserves_exact_reference_and_delivery_gates(monkeypatch, tmp_path):
    setup_pipeline(monkeypatch, tmp_path)
    successful = pipeline.stage_generate_3d
    observed = []
    def select(**kwargs):
        return 'microsoft/TRELLIS.2-4B' if kwargs.get('excluded') else 'tencent/Hunyuan3D-2.1'
    def generate(image, target, **kwargs):
        observed.append((kwargs['model_name'], image, pipeline._digest(image)))
        if len(observed) == 1:
            return pipeline.PipelineStage('3d_generation', kwargs['model_name'], 'failed', log='flat geometry')
        return successful(image, target, **kwargs)
    monkeypatch.setattr(pipeline, 'select_3d_model', select)
    monkeypatch.setattr(pipeline, 'stage_generate_3d', generate)
    result = pipeline.run_pipeline('red otter', tmp_path, image_model='fixture')
    assert result.success and len(observed) == 2
    assert observed[0][1:] == observed[1][1:]
    assert any(s.name == '3d_recipe_change' for s in result.stages)
    assert any(s.name == 'mesh_visual_gate' and s.status == 'done' for s in result.stages)
    assert any(s.name == 'mesh_semantic_gate' and s.status == 'done' for s in result.stages)


def test_explicit_3d_model_cannot_be_replaced_silently(monkeypatch, tmp_path):
    setup_pipeline(monkeypatch, tmp_path)
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(kwargs['model_name'])
        return pipeline.PipelineStage('3d_generation', kwargs['model_name'], 'failed', log='mismatch')
    monkeypatch.setattr(pipeline, 'stage_generate_3d', fail)
    monkeypatch.setattr(pipeline, 'select_3d_model', lambda **kw: pytest.fail('Explicit model replaced'))
    result = pipeline.run_pipeline('red otter', tmp_path, image_model='fixture', model_3d='explicit-engine')
    assert not result.success and attempts == ['explicit-engine']


def test_find_model_rejects_substring_and_incomplete_caches(monkeypatch, tmp_path):
    from aurora_cli.core import locations
    wrong = tmp_path / "author--model-extra"
    wrong.mkdir()
    (wrong / "model_index.json").write_text("{}")
    exact = tmp_path / "author--model"
    exact.mkdir()
    monkeypatch.setattr(locations, "model_search_roots", lambda: [("jobia", tmp_path)])
    assert pipeline.find_model("author/model", capability="image") is None
    (exact / "model_index.json").write_text("{}")
    assert pipeline.find_model("author/model", capability="image")[0] == exact


def setup_input_pipeline(monkeypatch, tmp_path):
    calls = setup_pipeline(monkeypatch, tmp_path)
    source = tmp_path / 'my images' / 'invented reference.png'
    source.parent.mkdir()
    source.write_bytes(b'user supplied bytes, not generated')
    monkeypatch.setattr(pipeline, 'python_engine', lambda *a: Path('image-decoder-python'))
    monkeypatch.setattr(pipeline.subprocess, 'run', lambda command, **kw: SimpleNamespace(
        returncode=0, stdout='{"width": 96, "height": 128, "format": "PNG"}', stderr=''))
    def forbidden(*a, **kw):
        raise AssertionError('A supplied image must never be reimagined or reviewed as a generated reference')
    monkeypatch.setattr(reference_brief, 'build_reference_brief', forbidden)
    monkeypatch.setattr(pipeline, 'select_image_model', forbidden)
    monkeypatch.setattr(pipeline, 'stage_generate_image', forbidden)
    monkeypatch.setattr(pipeline, 'stage_verify_image', forbidden)
    return source, calls


def test_supplied_image_bypasses_brief_generation_and_reference_vlm(monkeypatch, tmp_path):
    source, calls = setup_input_pipeline(monkeypatch, tmp_path)
    compared = []
    monkeypatch.setattr(pipeline, 'stage_verify_mesh_subject', lambda proof, description, **kw:
        compared.append((description, kw['reference'])) or
        pipeline.PipelineStage('mesh_semantic_gate', 'fixture', 'done', output=proof))
    progress = []
    result = pipeline.run_pipeline(f'génère moi cette image en modèle 3d : "{source}"',
                                   tmp_path / 'out', progress=progress.append)
    assert result.success
    assert [call[0] for call in calls] == ['mesh']
    imported = calls[0][1]
    assert imported != source and imported.read_bytes() == source.read_bytes()
    assert imported.is_relative_to(result.checkpoint.parent)
    assert compared[0][1] == imported
    assert 'reference image is authoritative' in compared[0][0]
    assert str(source) not in compared[0][0]
    names = {stage.name for stage in progress}
    assert not names & {'reference_brief', 'image_generation', 'vlm_verification'}
    assert {'reference_input', '3d_generation', 'mesh_semantic_gate'} <= names
    state = json.loads(result.checkpoint.read_text())
    assert state['accepted_reference']['kind'] == 'user_input'
    assert state['identity']['input_image']['sha256'] == pipeline._digest(source)
    assert not state.get('selected_image_model')


def test_missing_input_fails_before_any_model_without_fallback(monkeypatch, tmp_path):
    source, calls = setup_input_pipeline(monkeypatch, tmp_path)
    source.unlink()
    monkeypatch.setattr(pipeline, 'ensure_model', lambda *a: pytest.fail('Missing input must not install a model'))
    monkeypatch.setattr(pipeline, 'select_vision_model', lambda: pytest.fail('Missing input must not select a model'))
    result = pipeline.run_pipeline(f'3d from "{source}"', tmp_path / 'out')
    assert not result.success and calls == []
    assert result.stages[0].error_kind == 'input_validation'
    assert 'introuvable' in result.log and 'remplacement' in result.log


def test_corrupt_input_is_rejected_without_model_generation(monkeypatch, tmp_path):
    source, calls = setup_input_pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout='', stderr='UnidentifiedImageError'))
    monkeypatch.setattr(pipeline, 'ensure_model', lambda *a: pytest.fail('Invalid input must not install a model'))
    result = pipeline.run_pipeline(f'image to 3d: "{source}"', tmp_path / 'out')
    assert not result.success and calls == []
    assert 'UnidentifiedImageError' in result.log
    assert result.stages[0].error_kind == 'input_validation'


def test_input_failure_resume_uses_same_original_not_a_generated_candidate(monkeypatch, tmp_path):
    source, calls = setup_input_pipeline(monkeypatch, tmp_path)
    successful_mesh = pipeline.stage_generate_3d
    monkeypatch.setattr(pipeline, 'stage_generate_3d', lambda *a, **kw:
        pipeline.PipelineStage('3d_generation', 'fixture', 'failed', log='paint interrupted'))
    request = f'image to 3d: "{source}"'
    first = pipeline.run_pipeline(request, tmp_path / 'out')
    assert not first.success
    monkeypatch.setattr(pipeline, 'stage_generate_3d', successful_mesh)
    second = pipeline.run_pipeline(request, tmp_path / 'out')
    assert second.success and first.checkpoint == second.checkpoint
    assert calls[0][1].read_bytes() == source.read_bytes()


def test_changed_source_content_creates_new_job_and_does_not_reuse_old_delivery(monkeypatch, tmp_path):
    source, calls = setup_input_pipeline(monkeypatch, tmp_path)
    request = f'image to 3d: "{source}"'
    first = pipeline.run_pipeline(request, tmp_path / 'out')
    original_snapshot = calls[0][1]
    original_bytes = original_snapshot.read_bytes()
    source.write_bytes(b'new user image at same path')
    second = pipeline.run_pipeline(request, tmp_path / 'out')
    assert first.success and second.success
    assert first.checkpoint != second.checkpoint
    assert first.final_output != second.final_output
    assert len(calls) == 2 and calls[1][1].read_bytes() == source.read_bytes()
    assert original_snapshot.read_bytes() == original_bytes


def test_tampered_snapshot_cannot_trigger_reference_regeneration(monkeypatch, tmp_path):
    source, calls = setup_input_pipeline(monkeypatch, tmp_path)
    request = f'image to 3d: "{source}"'
    first = pipeline.run_pipeline(request, tmp_path / 'out')
    calls[0][1].write_bytes(b'tampered copied reference')
    second = pipeline.run_pipeline(request, tmp_path / 'out')
    assert first.success and not second.success
    assert len(calls) == 1 and second.stages[0].error_kind == 'input_validation'
    assert 'modifiée' in second.log


def test_image_prompt_cannot_override_a_supplied_image(monkeypatch, tmp_path):
    source, calls = setup_input_pipeline(monkeypatch, tmp_path)
    result = pipeline.run_pipeline(f'3d from "{source}"', tmp_path / 'out', image_prompt='invent a human')
    assert not result.success and calls == []
    assert 'remplacée' in result.log


def test_input_route_keeps_texture_gate_and_blocks_bad_delivery(monkeypatch, tmp_path):
    source, calls = setup_input_pipeline(monkeypatch, tmp_path)
    def failed_visual(reference, mesh, **kw):
        proof = mesh.with_suffix('.fidelity.json')
        proof.write_text('{"verdict":"implausible"}')
        return pipeline.PipelineStage('mesh_visual_gate', 'fixture', 'failed',
                                      output=proof, log='misplaced texture')
    monkeypatch.setattr(pipeline, 'stage_verify_mesh', failed_visual)
    result = pipeline.run_pipeline(f'3d from "{source}"', tmp_path / 'out')
    assert not result.success and result.final_output is None
    assert not list((tmp_path / 'out').glob('model-*.glb'))
    assert calls[0][1].read_bytes() == source.read_bytes()


def test_source_changes_during_copy_are_detected(monkeypatch, tmp_path):
    source = tmp_path / 'source.png'
    source.write_bytes(b'initial reference')
    expected = pipeline._digest(source)
    real_copy = pipeline.shutil.copyfile
    def changing_copy(src, dst):
        src.write_bytes(b'changed while taking snapshot')
        return real_copy(src, dst)
    monkeypatch.setattr(pipeline.shutil, 'copyfile', changing_copy)
    result = pipeline.stage_import_reference(source, tmp_path / 'snapshot.png', expected_sha256=expected)
    assert result.status == 'failed' and 'changé' in result.log
    assert not list(tmp_path.glob('reference-input-*.tmp'))
