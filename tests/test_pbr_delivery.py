"""Connected PBR maps, intact evidence, and the user's JOBIA interface."""
import json
from pathlib import Path

import pytest

from aurora_cli import workspace
from aurora_cli.core.glb_validation import inspect_glb
from aurora_cli.core.delivery_audit import audit_delivery
from aurora_cli.core.pipeline import _digest, _visual_contract
from test_pipeline_execution import glb_bytes


@pytest.mark.parametrize('edit', [
    lambda d: d['materials'][0].update(normalTexture={'index': 99}),
    lambda d: d['materials'][0].update(occlusionTexture={'index': 0, 'texCoord': 1}),
    lambda d: d['materials'][0]['pbrMetallicRoughness'].update(metallicRoughnessTexture={'index': 99}),
    lambda d: d['materials'][0]['pbrMetallicRoughness'].update(roughnessFactor=-1),
    lambda d: d['materials'][0]['pbrMetallicRoughness'].update(metallicFactor=float('nan')),
    lambda d: d['materials'][0].update(alphaMode='invalid'),
    lambda d: d['materials'][0].update(occlusionTexture={'index': 0, 'strength': 2}),
    lambda d: d['materials'][0]['pbrMetallicRoughness']['baseColorTexture'].update(texCoord=-1),
])
def test_invalid_auxiliary_map_or_factor_cannot_hide_behind_valid_albedo(tmp_path, edit):
    path = tmp_path / 'material.glb'
    path.write_bytes(glb_bytes(textured=True, edit=edit))
    with pytest.raises(ValueError):
        inspect_glb(path, require_textures=True)


def test_promised_generated_pbr_maps_are_distinguished_from_valid_constant_factors(tmp_path):
    path = tmp_path / 'material.glb'
    path.write_bytes(glb_bytes(textured=True))
    assert inspect_glb(path, require_textures=True)['materials'][0]['roughness_factor'] == 1
    with pytest.raises(ValueError, match='roughness/metallic'):
        inspect_glb(path, require_pbr_maps=True)
    path.write_bytes(glb_bytes(textured=True, edit=lambda d:
        d['materials'][0]['pbrMetallicRoughness'].update(metallicRoughnessTexture={'index': 0})))
    info = inspect_glb(path, require_pbr_maps=True)
    assert set(info['materials'][0]['maps']) == {'base_colour', 'metallic_roughness'}
    assert info['materials'][0]['maps']['metallic_roughness']['decoded'] is False


def evidence(tmp_path):
    """Mock evidence for testing attribution/integrity, NOT a neural model run."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    asset = tmp_path / 'model.glb'
    asset.write_bytes(glb_bytes(textured=True))
    job_dir = tmp_path / '.jobia' / 'fixture'
    job_dir.mkdir(parents=True)
    reference = job_dir / 'reference.png'
    reference.write_bytes(b'test fixture reference, not a neural image')
    previews = [job_dir / f'view-{n}.png' for n in range(2)]
    for index, preview in enumerate(previews):
        preview.write_bytes(f'test render fixture {index}'.encode())
    sheet = job_dir / 'sheet.png'
    sheet.write_bytes(b'test fixture contact sheet')
    info = inspect_glb(asset, require_textures=True)
    info['pixels_decoded'] = True
    for material in info['materials']:
        for binding in material['maps'].values():
            binding['decoded'] = True
    report = job_dir / 'fidelity.json'
    report.write_text(json.dumps(dict(verdict='plausible', mesh_sha256=_digest(asset),
        reference_sha256=_digest(reference), measured_reference=str(reference),
        measured_reference_sha256=_digest(reference), asset_validation=info,
        texture_placement={'verdict': 'placed'}, previews=[str(p) for p in previews],
        previews_sha256=[_digest(p) for p in previews], views=[{}, {}],
        render_mode='albedo_only', contact_sheet=str(sheet), semantic_review=dict(
            status='done', provider='mock for integrity tests only', preview_sha256=_digest(sheet),
            verdict=dict(match=True, issues=[], checks=[dict(passed=True, aspect=f'feature {i}',
                evidence='mock observation') for i in range(3)], review_input=dict(
                    source_sha256=_digest(sheet), comparison_reference_sha256=_digest(reference)))))))
    state = dict(status='completed', accepted_reference=dict(filename=reference.name, sha256=_digest(reference)),
        delivered=dict(filename=asset.name, sha256=_digest(asset), visual_contract=_visual_contract(),
                       visual_report=report.name, visual_report_sha256=_digest(report)))
    checkpoint = job_dir / 'job.json'
    checkpoint.write_text(json.dumps(state))
    return asset, report, checkpoint


def test_complete_attributed_evidence_can_be_verified(tmp_path):
    asset, _, _ = evidence(tmp_path)
    result = audit_delivery(asset)
    assert result['verified']
    assert 'indépendante' in result['limits']
    assert result['checks']['textures_decodees']['detail']['limit'].endswith('pas une mesure BRDF.')


@pytest.mark.parametrize('target', ['asset', 'reference', 'preview', 'sheet', 'report'])
def test_any_changed_evidence_revokes_verification(tmp_path, target):
    asset, report, _ = evidence(tmp_path)
    paths = dict(asset=asset, report=report, reference=report.parent / 'reference.png',
                 preview=report.parent / 'view-0.png', sheet=report.parent / 'sheet.png')
    paths[target].write_bytes(b'modified')
    assert not audit_delivery(asset)['verified']


@pytest.mark.parametrize('missing', ['asset_validation', 'texture_placement', 'previews_sha256', 'semantic_review'])
def test_missing_quality_checks_are_not_certification(tmp_path, missing):
    asset, report, checkpoint = evidence(tmp_path)
    measured = json.loads(report.read_text())
    measured.pop(missing)
    report.write_text(json.dumps(measured))
    state = json.loads(checkpoint.read_text())
    state['delivered']['visual_report_sha256'] = _digest(report)
    checkpoint.write_text(json.dumps(state))
    assert not audit_delivery(asset)['verified']


def test_valid_structure_alone_has_no_quality_certificate(tmp_path):
    asset = tmp_path / 'unattributed.glb'
    asset.write_bytes(glb_bytes(textured=True))
    result = audit_delivery(asset)
    assert result['checks']['structure_et_uv']['status'] == 'passed'
    assert result['checks']['controle_visuel']['status'] == 'not_run'
    assert not result['verified']


@pytest.mark.parametrize('change', ['expired_contract', 'proof_outside_job', 'duplicate_checkpoint'])
def test_old_unscoped_or_ambiguous_proof_is_refused(tmp_path, change):
    asset, report, checkpoint = evidence(tmp_path)
    state = json.loads(checkpoint.read_text())
    if change == 'expired_contract':
        state['delivered']['visual_contract'] = 'old-contract'
    elif change == 'proof_outside_job':
        state['delivered']['visual_report'] = str(asset)
    else:
        duplicate = checkpoint.parent.parent / 'duplicate'
        duplicate.mkdir()
        (duplicate / 'job.json').write_text(json.dumps(state))
    checkpoint.write_text(json.dumps(state))
    assert not audit_delivery(asset)['verified']


def test_unrelated_broken_job_does_not_revoke_valid_attributed_evidence(tmp_path):
    asset, _, checkpoint = evidence(tmp_path)
    unrelated = checkpoint.parent.parent / 'unrelated'
    unrelated.mkdir()
    (unrelated / 'job.json').write_text('interrupted JSON')
    assert audit_delivery(asset)['verified']


def test_jobia_verify_uses_own_interface_and_never_a_bridge(monkeypatch, tmp_path):
    asset, _, _ = evidence(tmp_path / 'path with spaces')
    from aurora_cli import bridge
    monkeypatch.setattr(bridge, 'Bridge', lambda *a, **kw: pytest.fail('No remote app needed'))
    messages = []
    for method in ('hint', 'warning', 'success'):
        monkeypatch.setattr(workspace.display, method, messages.append)
    assert workspace.verify_delivery(f'"{asset}"')['verified']
    assert any('enregistrés vérifiés' in message for message in messages)


def test_verify_command_is_dispatched_inside_interactive_jobia(monkeypatch, tmp_path):
    import prompt_toolkit
    from types import SimpleNamespace
    asset, _, _ = evidence(tmp_path)
    entries = iter([f'/verify "{asset}"', '/quit'])
    session = SimpleNamespace(prompt=lambda *a, **kw: next(entries))
    monkeypatch.setattr(prompt_toolkit, 'PromptSession', lambda **kw: session)
    monkeypatch.setattr(workspace, 'scan', lambda **kw: object())
    monkeypatch.setattr(workspace, 'dashboard', lambda *a: None)
    original = workspace.verify_delivery
    reports = []
    def verify(argument):
        reports.append(original(argument))
    monkeypatch.setattr(workspace, 'verify_delivery', verify)
    workspace.run_workspace()
    assert len(reports) == 1 and reports[0]['verified']
