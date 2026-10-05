"""Resumable local image/mesh execution with explicit delivery gates."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import catalog, fetcher
from .agents import get
from .bootstrap import ensure_3d_engine, ensure_hf, python_engine, start_ollama
from .machine import profile
from .glb_validation import validate_glb


@dataclass
class PipelineStage:
    """One stage in the pipeline."""

    name: str
    provider: str
    status: str = "pending"  # pending, running, done, failed
    output: Path | None = None
    log: str = ""
    duration_s: float = 0.0
    error_kind: str = ""


@dataclass
class PipelineResult:
    """Result of a full pipeline run."""

    success: bool
    stages: list[PipelineStage] = field(default_factory=list)
    final_output: Path | None = None
    log: str = ""
    checkpoint: Path | None = None


def _timeout_log(exc: subprocess.TimeoutExpired) -> str:
    """TimeoutExpired retains partial bytes even with text=True; keep them."""
    lines = [f"TimeoutExpired : délai de calcul dépassé ({exc.timeout:g} s)."]
    for label, output in (("stdout", exc.output), ("stderr", exc.stderr)):
        if output:
            detail = output.decode('utf-8', errors='replace') if isinstance(output, bytes) else str(output)
            lines.append(f"{label} conservé :\n{detail}")
    return '\n'.join(lines)


def stage_import_reference(source: Path, destination: Path, *, expected_sha256: str) -> PipelineStage:
    """Snapshot and actually decode a user image; never call a generative model."""
    stage = PipelineStage('reference_input', 'local-image-decoder')
    started = time.monotonic()
    temporary = None
    try:
        if destination.exists():
            if _digest(destination) != expected_sha256:
                raise ValueError('La copie de référence a été modifiée ; reconstruction interrompue.')
        else:
            with tempfile.NamedTemporaryFile(prefix='reference-input-', suffix='.tmp',
                                             dir=destination.parent, delete=False) as handle:
                temporary = Path(handle.name)
            shutil.copyfile(source, temporary)
            if _digest(temporary) != expected_sha256:
                raise ValueError('L’image d’entrée a changé pendant sa copie ; relance la demande.')
            temporary.replace(destination)
            temporary = None
        python = python_engine('images', ['Pillow'])
        worker = Path(__file__).with_name('image_worker.py')
        proc = subprocess.run([str(python), str(worker), '--inspect-image', str(destination)],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise ValueError('Image fournie illisible ou non prise en charge : ' +
                             (proc.stderr or proc.stdout)[-2000:])
        metadata = json.loads(proc.stdout)
        if (not isinstance(metadata, dict) or type(metadata.get('width')) is not int
                or type(metadata.get('height')) is not int
                or min(metadata['width'], metadata['height']) <= 0):
            raise ValueError('Le décodeur n’a pas confirmé les dimensions de l’image.')
        if _digest(destination) != expected_sha256:
            raise ValueError('L’image de référence a changé pendant sa validation.')
        stage.status, stage.output = 'done', destination
        stage.log = json.dumps(dict(source=str(source), sha256=expected_sha256, image=metadata,
            authority='user_input', generated=False), ensure_ascii=False)
    except subprocess.TimeoutExpired as exc:
        stage.status, stage.error_kind = 'failed', 'timeout'
        stage.log = _timeout_log(exc) + ' Aucune image de remplacement ne sera générée.'
    except Exception as exc:
        stage.status, stage.error_kind = 'failed', 'input_validation'
        stage.log = str(exc) + ' Aucune image de remplacement ne sera générée.'
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    stage.duration_s = time.monotonic() - started
    return stage


def stage_validate_mesh(mesh_path: Path, *, require_textures: bool = False) -> PipelineStage:
    """Validate the delivered GLB before marking a complex job complete."""
    stage = PipelineStage(name="mesh_quality_gate", provider="local-validator")
    try:
        stage.log = validate_glb(mesh_path, require_textures=require_textures)
        stage.status = "done"
        stage.output = mesh_path
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        stage.status = "failed"
        stage.log = str(exc)
        stage.error_kind = "validation"
    return stage


def stage_verify_mesh(reference: Path, mesh: Path, *, prepared: tuple[Path, Path],
                      require_textures: bool = True, model_name: str = '') -> PipelineStage:
    """Measure actual textured views in the engine environment before delivery."""
    from aurora_cli.evolution import load_policy
    settings = load_policy().get('delivery_3d', {})
    root, python = prepared
    stage = PipelineStage('mesh_visual_gate', 'local-renderer')
    t0 = time.monotonic()
    try:
        from aurora_cli.adapters import AdapterRegistry
        runner, _ = AdapterRegistry().resolve('3d', model_name)
        geometry_contract = runner.geometry_contract if runner else 'volumetric'
        thresholds = mesh.with_suffix('.thresholds.json')
        _save_manifest(thresholds, {
            'min_bbox_fill': float(settings.get('min_bbox_fill', 0.05)),
            'min_silhouette_iou': float(settings.get('min_silhouette_iou', 0.35)),
            'geometry_contract': geometry_contract,
        })
        report = mesh.with_suffix('.fidelity.json')
        foreground = mesh.with_name(mesh.stem + '.input-rgba.png')
        conditioning = mesh.with_name(mesh.stem + '.conditioning.png')
        measured_reference = conditioning if conditioning.is_file() else foreground if foreground.is_file() else reference
        command = [str(python), str(Path(__file__).with_name('fidelity.py')),
                   '--reference', str(measured_reference),
                   '--mesh', str(mesh), '--engine-root', str(root),
                   '--thresholds', str(thresholds), '--size', str(settings.get('render_size', 256)),
                   '--output', str(report)]
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=float(settings.get('timeout_s', 600)))
        if not report.is_file():
            raise RuntimeError('Contrôle visuel indisponible : ' + (proc.stderr or proc.stdout)[-2000:])
        measured = json.loads(report.read_text(encoding='utf-8'))
        stage.output = report
        if proc.returncode or measured.get('verdict') != 'plausible':
            raise RuntimeError('Rendu 3D refusé : ' + json.dumps(measured, ensure_ascii=False))
        placement = (measured.get('texture_placement') or {}).get('verdict')
        if require_textures and settings.get('require_placed_texture', True) and placement != 'placed':
            raise RuntimeError(f'Texture non validée ({placement or "non mesurée"}). Diagnostics : {report}')
        measured.update(mesh_sha256=_digest(mesh), reference_sha256=_digest(reference),
                        measured_reference=str(measured_reference),
                        measured_reference_sha256=_digest(measured_reference))
        _save_manifest(report, measured)
        stage.status, stage.output = 'done', report
        stage.log = json.dumps(measured, ensure_ascii=False)
    except subprocess.TimeoutExpired as exc:
        stage.status, stage.error_kind, stage.log = 'failed', 'timeout', _timeout_log(exc)
    except (OSError, ValueError, RuntimeError) as exc:
        stage.status, stage.error_kind, stage.log = 'failed', 'visual_validation', str(exc)
    stage.duration_s = time.monotonic() - t0
    return stage


def stage_verify_mesh_subject(visual_report: Path, description: str, *, model_name: str,
                              reference: Path | None = None) -> PipelineStage:
    """Check the actual delivered geometry/materials, not just the source image."""
    stage = PipelineStage('mesh_semantic_gate', model_name)
    try:
        measured = json.loads(visual_report.read_text(encoding='utf-8'))
        preview = Path(measured['contact_sheet'])
        if not preview.resolve().is_relative_to(visual_report.parent.resolve()) or not preview.is_file():
            raise ValueError('Rendu de vérification absent ou hors du dossier de travail')
        stage = stage_verify_image(preview, description + (
            '\nThis contact sheet shows the SAME single 3D object from several camera angles, '
            'not a requested collage or multiple subjects. Inspect ALL visible angles for '
            'missing geometry, missing or misplaced textures and inconsistent features. '
            'Do not reject the diagnostic view labels or the multiplicity of camera views.'),
            model_name=model_name, reference_image=reference)
        stage.name = 'mesh_semantic_gate'
        measured['semantic_review'] = dict(status=stage.status, provider=model_name,
            verdict=json.loads(stage.log) if stage.error_kind in {'', 'mismatch'} else stage.log,
            preview_sha256=_digest(preview))
        _save_manifest(visual_report, measured)
        if stage.status == 'done':
            stage.output = visual_report
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        stage.status, stage.error_kind, stage.log = 'failed', 'visual_validation', str(exc)
    return stage


# --- Dynamic discovery ------------------------------------------------------


def find_model(model_name: str, *, capability: str = "") -> tuple[Path, Path] | None:
    """Find the exact model directory or HF snapshot, never a substring match."""
    from .locations import model_search_roots

    requested = Path(model_name).expanduser()
    candidates = [requested] if requested.is_dir() else []
    expected = model_name.casefold().replace("/", "--")
    for _runtime, root in model_search_roots():
        if not root.is_dir():
            continue
        try:
            entries = [root, *root.iterdir()]
        except OSError:
            continue
        for child in entries:
            normalized = child.name.casefold().removeprefix("models--")
            if child.is_dir() and normalized == expected:
                candidates.append(child)
                snapshots = child / "snapshots"
                if snapshots.is_dir():
                    candidates.extend(sorted(snapshots.iterdir(), reverse=True))
    for candidate in candidates:
        if capability == "image" and not (candidate / "model_index.json").is_file():
            continue
        python = candidate / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
        return candidate, python if python.is_file() else Path(sys.executable)
    return None


def ensure_model(model_name: str, capability: str, *, model_dir: Path | None = None):
    """Provision exactly the selected model; never substitute another role tier."""
    from aurora_cli.adapters import AdapterRegistry
    runner, reason = AdapterRegistry().resolve(capability, model_name)
    if capability in {'image', '3d', 'audio', 'tts'}:
        if runner is None:
            raise RuntimeError(reason)
        if reason := runner.incompatibility(profile()):
            raise RuntimeError(reason)
    found = find_model(str(model_dir) if model_dir is not None else model_name, capability=capability)
    if model_dir is not None and found is None:
        raise RuntimeError('Le dossier local sélectionné est absent ou incomplet ; aucun remplacement silencieux.')
    # Always use JOBIA's managed runtime for executable pipelines. A cache hit
    # can be only weights (or a stale system interpreter without torch).
    if capability == "image":
        engine = python_engine("images", list(runner.packages) or [
            "torch", "diffusers", "transformers", "accelerate", "safetensors", "Pillow"])
        if found:
            return found[0], engine
    if capability in {'audio', 'tts'}:
        engine = python_engine('audio', list(runner.packages))
        if found:
            return found[0], engine
    if capability == "3d":
        if runner.provisioner == 'native':
            from .native_runtime import ensure_native
            return ensure_native(runner, model_name)
        if runner.provisioner != 'hunyuan':
            raise RuntimeError(f'Provisionneur non implémenté : {runner.provisioner}')
        root = ensure_3d_engine()
        python = root / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
        # The 3D adapter owns model loading and its components. A different
        # catalogue tier is not interchangeable with this engine.
        return root, python
    if found:
        return found
    agent = get({'tts': 'speech', 'llm': 'resume', 'text': 'resume'}.get(capability, capability))
    if agent is None:
        return None
    artifact = next((a for a in catalog.ARTIFACTS if a.ref == model_name), None)
    if artifact is None:
        artifact = catalog.Artifact(agent.id, 'explicit',
            'ollama' if agent.capability in {'llm', 'vision', 'code'} else 'huggingface', model_name, model_name)
    if artifact.runtime == "huggingface":
        ensure_hf()
    elif artifact.runtime == "ollama":
        start_ollama()
    plan, _ = fetcher.install(artifact, profile(), agent=agent.id,
                              job=f"pipeline:{capability}")
    if capability in {'image', 'audio', 'tts'}:
        return plan.target, engine
    return find_model(artifact.ref, capability=capability) or (plan.target, Path(sys.executable))


def select_image_model() -> str:
    """Prefer a valid evaluated metric, then compatible catalogue preferences."""
    from types import SimpleNamespace
    from aurora_cli.engine_manager import EngineRegistry
    from aurora_cli.evolution import EvolutionLoop

    loop = EvolutionLoop(SimpleNamespace(registry=EngineRegistry()))
    candidate = loop.recommendation("image")
    from .model_selection import choose
    return choose('image', machine=profile(), proven=candidate).artifact.ref


def select_3d_model(*, texture=True, excluded=(), context='') -> str:
    from .model_selection import choose
    return choose('3d', machine=profile(), require_textures=texture, excluded=excluded, context=context).artifact.ref


def alternative_image_model(excluded: set[str]) -> str | None:
    """Try another declared compatible recipe after measured visual rejection.

    Catalogue tiers are a preference, not proof of semantic superiority. The
    replacement must still pass the same visual checks for this exact request.
    """
    from .model_selection import choices
    candidates, _ = choices('image', machine=profile(), excluded=excluded)
    return candidates[0].artifact.ref if candidates else None


def visual_corrections(verdict: str) -> str:
    """Send corrective observations, not the entire JSON and successful checks."""
    try:
        data = json.loads(verdict)
        issues = [issue for issue in data.get('issues', []) if isinstance(issue, str)]
        failed = [check.get('evidence', '') for check in data.get('checks', [])
                  if isinstance(check, dict) and check.get('passed') is False]
        return '\n'.join(dict.fromkeys(issues + failed)) or data.get('reason', verdict)
    except (ValueError, TypeError, AttributeError):
        return verdict


def find_ollama() -> str | None:
    """Check if Ollama is running."""
    try:
        import httpx
        resp = httpx.get(f"{_ollama_url()}/api/tags", timeout=2.0)
        if resp.status_code == 200:
            return _ollama_url()
    except Exception:
        pass
    return None


def select_vision_model() -> str:
    """Resolve a visual reviewer from declared budgets, not a fixed legacy tag."""
    from .model_selection import choose
    return choose('vision', machine=profile()).artifact.ref


def _ollama_url() -> str:
    url = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    return url if "://" in url else "http://" + url


def _release_ollama_model(model_name: str) -> None:
    """Release only the model used by this job, between memory-heavy stages."""
    try:
        import httpx
        httpx.post(f"{_ollama_url()}/api/generate",
                   json={"model": model_name, "keep_alive": 0, "stream": False},
                   timeout=30.0).raise_for_status()
    except Exception:
        # Resource planning still measures actual memory after this operation.
        # A daemon refusing unload must not be recorded as memory reclaimed.
        pass


def find_ollama_model(model_name: str) -> bool:
    """Check if a model is available in Ollama."""
    ollama = find_ollama()
    if not ollama:
        return False
    try:
        import httpx
        resp = httpx.get(f"{ollama}/api/tags", timeout=5.0)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            return any(m.get("name", "") in {model_name, model_name + ":latest"} for m in models)
    except Exception:
        pass
    return False


def install_ollama_model(model_name: str) -> bool:
    """Install a model into Ollama."""
    try:
        import httpx
        resp = httpx.post(
            f"{_ollama_url()}/api/pull",
            json={"name": model_name, "stream": False},
            timeout=600.0,
        )
        return resp.status_code == 200 and not resp.json().get("error")
    except Exception:
        return False


# --- Stage implementations --------------------------------------------------


def stage_generate_image(
    prompt: str,
    output_path: Path,
    *,
    model_name: str | None = None,
    model_spec: str | None = None,
    seed: int = 0,
    negative_prompt: str = "",
    prepared: tuple[Path, Path] | None = None,
) -> PipelineStage:
    """Generate an image using a local diffusion model."""
    from aurora_cli.adapters import AdapterRegistry

    stage = PipelineStage(name="image_generation", provider=model_name or "auto")

    try:
        model_name = model_name or select_image_model()
        stage.provider = model_spec or model_name
        adapters = AdapterRegistry()
        runner, reason = adapters.resolve("image", model_spec or model_name)
        if runner is None:
            raise RuntimeError(reason)
        found = prepared or ensure_model(model_name, "image")
    except Exception as exc:
        stage.status = "failed"
        stage.error_kind = "preparation"
        stage.log = f"Préparation automatique du moteur image impossible : {exc}"
        return stage
    if not found:
        stage.status = "failed"
        stage.error_kind = "preparation"
        stage.log = f"Model {model_name} not found. Install it first."
        return stage

    model_root, venv_python = found
    output_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        command = adapters.build(
            runner, interpreter=venv_python, worker=Path(__file__).with_name("image_worker.py"),
            model_dir=model_root, prompt=prompt, output=output_path, seed=seed,
            negative=negative_prompt, model_spec=model_spec or model_name,
        )
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=1800,
            env=os.environ | runner.environment(),
        )
        stage.duration_s = time.time() - t0
        stage.log = result.stdout + result.stderr
        if result.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0:
            stage.status = "done"
            stage.output = output_path
        else:
            stage.status = "failed"
            stage.error_kind = "execution"
    except subprocess.TimeoutExpired as exc:
        stage.duration_s = time.time() - t0
        stage.status, stage.error_kind, stage.log = 'failed', 'timeout', _timeout_log(exc)
    except Exception as exc:
        stage.duration_s = time.time() - t0
        stage.status = "failed"
        stage.error_kind = "execution"
        stage.log = str(exc)

    return stage


def prepare_review_image(image_path: Path, max_dimension: int) -> Path:
    """Run image preprocessing in its isolated Pillow runtime, never the CLI."""
    preview = image_path.with_name(image_path.stem + '.review.png')
    receipt = preview.with_suffix('.json')
    signature = dict(source_sha256=_digest(image_path), max_dimension=max_dimension,
                     worker_sha256=_digest(Path(__file__).with_name('image_worker.py')))
    try:
        saved = json.loads(receipt.read_text(encoding='utf-8'))
        if (preview.is_file() and saved.get('signature') == signature
                and saved.get('sha256') == _digest(preview)):
            return preview
    except (OSError, ValueError):
        pass
    python = python_engine('images', ['Pillow'])
    process = subprocess.run([str(python), str(Path(__file__).with_name('image_worker.py')),
        '--prepare-review', str(image_path), str(preview), '--max-dimension', str(max_dimension)],
        capture_output=True, text=True, timeout=60)
    if process.returncode or not preview.is_file():
        raise RuntimeError('Préparation de la revue visuelle impossible : ' + process.stderr[-1000:])
    _save_manifest(receipt, dict(signature=signature, sha256=_digest(preview)))
    return preview


def stage_verify_image(
    image_path: Path,
    character_desc: str,
    *,
    model_name: str | None = None,
    reference_image: Path | None = None,
) -> PipelineStage:
    """Verify the generated image matches the request using a VLM."""
    stage = PipelineStage(name="vlm_verification", provider=model_name or 'auto')

    try:
        model_name = model_name or select_vision_model()
        stage.provider = model_name
        start_ollama()
    except Exception as exc:
        stage.status = "failed"
        stage.error_kind = 'preparation'
        stage.log = f"Préparation automatique du VLM impossible : {exc}"
        return stage
    if not find_ollama_model(model_name):
        stage.status = "running"
        stage.log = f"Installing {model_name} into Ollama..."
        if not install_ollama_model(model_name):
            stage.status = "failed"
            stage.error_kind = 'preparation'
            stage.log = f"Failed to install {model_name}"
            return stage

    import httpx
    import base64

    prompt = f"""Evaluate this generated image against the user's requested subject and criteria:
{character_desc}
Check requested identity and features, completeness, and internal spatial/anatomical consistency.
Imaginative designs and requested transformations are allowed; do not reject invention just
because it has no canonical appearance. Do reject contradictory, missing essential features
and conspicuous visual artifacts. Report what is actually visible, not inferred intentions.
Do not infer that any file was delivered or that a 3D mesh was created from the image.
If a required identity cannot be established, reject the image. Return JSON only:
{{"match": true/false, "reason": "visible evidence and necessary corrections",
"checks": [{{"aspect": "specific requested feature", "passed": true/false,
"evidence": "visible observation"}}], "issues": ["observed inconsistency"]}}
Include at least three distinct checks. match must be false if any check fails or issues remain.
issues must be [] when no issue is observed; never put "no issues" in the issues list."""

    t0 = time.time()
    try:
        from aurora_cli.evolution import load_policy
        settings = load_policy().get('visual_review', {})
        review_image = prepare_review_image(image_path, int(settings.get('max_dimension', 512)))
        img_b64 = base64.b64encode(review_image.read_bytes()).decode()
        images = [img_b64]
        if reference_image is not None:
            reviewed_reference = prepare_review_image(reference_image, int(settings.get('max_dimension', 512)))
            images.insert(0, base64.b64encode(reviewed_reference.read_bytes()).decode())
            prompt += ('\nTwo image inputs are provided. The FIRST is the accepted reference design; '
                       'the SECOND is the actual 3D output in its diagnostic camera views. '
                       'Evaluate the SECOND image, comparing identity, required features and material '
                       'placement against the FIRST. Allow camera and lighting differences, not '
                       'missing parts, unrelated color regions or broken facial/limb features.')
        payload = {
                "model": model_name,
                "messages": [{'role': 'user', 'content': prompt, 'images': images}],
                "stream": False,
                "think": False,
                "format": {
                    'type': 'object', 'properties': {
                        'match': {'type': 'boolean'}, 'reason': {'type': 'string'},
                        'checks': {'type': 'array', 'minItems': 3, 'items': {'type': 'object',
                            'properties': {'aspect': {'type': 'string'}, 'passed': {'type': 'boolean'},
                                           'evidence': {'type': 'string'}},
                            'required': ['aspect', 'passed', 'evidence']}},
                        'issues': {'type': 'array', 'items': {'type': 'string'}},
                    }, 'required': ['match', 'reason', 'checks', 'issues'],
                },
                'options': {'temperature': 0,
                            'num_ctx': int(settings.get('context_tokens', 8192))},
        }
        invalid = []
        attempts = max(1, int(settings.get('max_attempts', 2)))
        for attempt in range(attempts):
            payload['options']['num_predict'] = min(
                int(settings.get('output_tokens', 2048)) * (attempt + 1),
                int(settings.get('max_output_tokens', 4096)))
            payload['messages'][0]['content'] = prompt + ('\nYour last response was incomplete or invalid. '
                'Return a COMPLETE concise JSON object, with short observations and no preamble.'
                if attempt else '\nKeep each observation concise; return a complete JSON object.')
            resp = httpx.post(f"{_ollama_url()}/api/chat", json=payload,
                              timeout=float(settings.get('timeout_s', 180)))
            resp.raise_for_status()
            data = resp.json()
            text = (data.get('message') or {}).get('content', data.get('response', ''))
            try:
                if data.get('done') is not True or data.get('done_reason') == 'length':
                    raise ValueError('Le moteur a renvoyé un verdict incomplet ou tronqué')
                result = json.loads(text)
                if not isinstance(result, dict) or type(result.get("match")) is not bool:
                    raise ValueError("Le verdict VLM doit contenir un booléen match")
                if not isinstance(result.get("reason"), str) or not result["reason"].strip():
                    raise ValueError("Le verdict VLM ne contient aucune justification")
                checks = result.get('checks')
                if not isinstance(checks, list) or len(checks) < 3 or any(
                    not isinstance(check, dict) or type(check.get('passed')) is not bool
                    or not isinstance(check.get('aspect'), str) or not check['aspect'].strip()
                    or not isinstance(check.get('evidence'), str) or not check['evidence'].strip()
                    for check in checks
                ):
                    raise ValueError('Verdict sans observations visuelles détaillées')
                if len({check['aspect'].strip().casefold() for check in checks}) < 3:
                    raise ValueError('Les observations visuelles doivent être distinctes')
                if (not isinstance(result.get('issues'), list)
                        or any(not isinstance(issue, str) or not issue.strip() for issue in result['issues'])):
                    raise ValueError('Le contrôle des incohérences est absent ou invalide')
                if any(not check['passed'] for check in checks) or result['issues']:
                    result['match'] = False
                stage.status = "done" if result["match"] is True else "failed"
                stage.error_kind = "" if result["match"] is True else "mismatch"
                if invalid:
                    result['verification_recovery'] = invalid
                result['review_input'] = dict(source_sha256=_digest(image_path),
                    reviewed_sha256=_digest(review_image), max_dimension=int(settings.get('max_dimension', 512)))
                if reference_image is not None:
                    result['review_input']['comparison_reference_sha256'] = _digest(reference_image)
                result['execution'] = {key: data.get(key) for key in
                    ('done_reason', 'prompt_eval_count', 'eval_count', 'total_duration')}
                stage.log = json.dumps(result, ensure_ascii=False)
                break
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                invalid.append({'attempt': attempt + 1, 'error': str(exc),
                                'done': data.get('done'), 'done_reason': data.get('done_reason'),
                                'response': text[:2000]})
        else:
            stage.status, stage.error_kind = 'failed', 'invalid_verdict'
            stage.log = 'Contrôle visuel invalide : ' + json.dumps(invalid, ensure_ascii=False)
    except subprocess.TimeoutExpired as exc:
        stage.status, stage.error_kind, stage.log = 'failed', 'timeout', _timeout_log(exc)
    except Exception as exc:
        stage.duration_s = time.time() - t0
        stage.status = "failed"
        stage.error_kind = "verification"
        stage.log = str(exc)
    finally:
        stage.duration_s = time.time() - t0
        _release_ollama_model(model_name)

    return stage


def stage_generate_3d(
    image_path: Path,
    output_path: Path,
    *,
    model_name: str = "tencent/Hunyuan3D-2.1",
    texture: bool = True,
    prepared: tuple[Path, Path] | None = None,
) -> PipelineStage:
    """Use the shared engine adapter; an installed directory is not a result."""
    from .engine3d import Engine3D, generate_mesh

    stage = PipelineStage(name="3d_generation", provider=model_name)
    t0 = time.monotonic()
    try:
        found = prepared or ensure_model(model_name, "3d")
        if not found:
            raise RuntimeError(f"Moteur absent pour {model_name}")
        root, python = found
        from aurora_cli.adapters import AdapterRegistry
        runner, reason = AdapterRegistry().resolve('3d', model_name)
        if runner is None:
            raise RuntimeError(reason)
        if runner.provisioner == 'native':
            repo, can_paint = root / 'repo', runner.texturing and (root / 'runtime.json').is_file()
        else:
            repo = root if (root / "hy3dshape").is_dir() else root / "Hunyuan3D-2.1"
            can_paint = (repo / 'hy3dpaint').is_dir()
        engine = Engine3D(model_name, root, python, repo, can_paint)
        success, stage.log = generate_mesh(engine, image_path, output_path,
                                          paint=texture, model_ref=model_name)
        stage.status = "done" if success and output_path.is_file() else "failed"
        stage.output = output_path if stage.status == "done" else None
        if stage.status == "failed":
            stage.error_kind = "execution"
    except Exception as exc:
        stage.status = "failed"
        stage.error_kind = "preparation" if prepared is None else "execution"
        stage.log = str(exc)
    stage.duration_s = time.monotonic() - t0
    return stage


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _visual_contract() -> str:
    from aurora_cli.adapters import manifest_path
    from aurora_cli.evolution import load_policy
    payload = (Path(__file__).with_name('fidelity.py').read_bytes()
               + Path(__file__).with_name('glb_validation.py').read_bytes()
               + Path(__file__).with_name('portable_rasterizer.py').read_bytes()
               + Path(__file__).with_name('texture_placement.py').read_bytes()
               + manifest_path().read_bytes()
               + Path(__file__).read_bytes()
               + json.dumps({key: load_policy().get(key, {}) for key in
                             ('delivery_3d', 'visual_review', 'texture_placement')},
                            sort_keys=True).encode())
    return hashlib.sha256(payload).hexdigest()


def _reference_contract() -> str:
    import inspect
    from aurora_cli.evolution import load_policy
    payload = (inspect.getsource(stage_verify_image).encode()
               + inspect.getsource(prepare_review_image).encode()
               + json.dumps(load_policy().get('visual_review', {}), sort_keys=True).encode())
    return hashlib.sha256(payload).hexdigest()


def _save_manifest(path: Path, data: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def run_pipeline(
    character_desc: str,
    output_dir: Path,
    *,
    image_prompt: str | None = None,
    input_image: Path | None = None,
    image_model: str | None = None,
    vlm_model: str | None = None,
    model_3d: str | None = None,
    texture: bool = True,
    max_retries: int = 2,
    progress: Callable[[PipelineStage], None] | None = None,
) -> PipelineResult:
    """Run/resume a job, retaining accepted stages and evidence across failures.

    Installation recovery belongs to the runtime manager. An unchanged setup
    error is never retried by regenerating a reference image. Semantic rejection
    gets a new seed and critique; accepted references survive mesh failures.
    """
    from .request_spec import parse_creation_request
    from .reference_brief import build_reference_brief

    request = parse_creation_request(character_desc, output_dir, input_image=input_image)
    subject, output_dir = request.subject, request.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    input_identity, input_error = None, None
    if request.input_image is not None:
        try:
            if not request.input_image.is_file():
                raise ValueError(f'Image d’entrée introuvable : {request.input_image}')
            input_identity = dict(path=str(request.input_image), sha256=_digest(request.input_image))
            if image_prompt is not None or image_model is not None:
                raise ValueError('Une image fournie ne peut pas être remplacée par un prompt ou un moteur image.')
        except (OSError, ValueError) as exc:
            input_error = str(exc)
            input_identity = input_identity or dict(path=str(request.input_image), sha256=None)
    selection_error = None
    automatic_3d = model_3d is None
    try:
        model_3d = model_3d or select_3d_model(texture=texture, context=input_identity['sha256'] if input_identity else subject)
    except Exception as exc:
        selection_error, model_3d = str(exc), 'auto'
    vision_error = None
    try:
        vlm_model = vlm_model or ('auto' if input_error else select_vision_model())
    except Exception as exc:
        vision_error, vlm_model = str(exc), 'auto'
    identity = dict(subject=subject, image_prompt=image_prompt, image_model=image_model,
                    input_image=input_identity,
                    vlm_model=vlm_model if vlm_model != 'auto' else 'auto',
                    model_3d='auto' if automatic_3d else model_3d, texture=texture, version=7)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    job_dir = output_dir / ".jobia" / key
    job_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = job_dir / "job.json"
    try:
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        state = saved if isinstance(saved, dict) and saved.get("identity") == identity else {}
    except (OSError, ValueError):
        state = {}
    if not state:
        state = dict(identity=identity, status="pending", stages=[], next_attempt=1)
    stages: list[PipelineStage] = []

    def report(stage):
        stages.append(stage)
        sequence = len(state["stages"]) + 1
        log_path = job_dir / f"{sequence:04d}-{stage.name}.log"
        log_path.write_text(stage.log, encoding="utf-8")
        state["stages"].append(dict(name=stage.name, provider=stage.provider, status=stage.status,
                                    output=str(stage.output) if stage.output else None,
                                    error_kind=stage.error_kind, duration_s=stage.duration_s,
                                    log=str(log_path)))
        _save_manifest(checkpoint, state)
        if progress:
            progress(stage)
        return log_path

    def pending(name, provider, message):
        state["status"] = name
        _save_manifest(checkpoint, state)
        if progress:
            progress(PipelineStage(name, provider, "running", log=message))

    def result(success=False, output=None):
        state["status"] = "completed" if success else "waiting_for_recovery"
        _save_manifest(checkpoint, state)
        return PipelineResult(success, stages, output, "\n".join(s.log for s in stages), checkpoint)

    image_path = None
    if input_error:
        report(PipelineStage('reference_input', 'local-image-decoder', 'failed',
            error_kind='input_validation', log=input_error + ' Aucune image de remplacement ne sera générée.'))
        return result()
    if request.input_image is not None:
        pending('reference_input', 'local-image-decoder',
                'Lecture de l’image fournie : copie fidèle, sans nouvelle génération ni brief inventé.')
        imported = stage_import_reference(request.input_image,
            job_dir / ('reference-input' + (request.input_image.suffix.lower() or '.img')),
            expected_sha256=input_identity['sha256'])
        proof = report(imported)
        if imported.status != 'done':
            return result()
        image_path = imported.output
        state['accepted_reference'] = dict(kind='user_input', source=input_identity['path'],
            filename=image_path.name, sha256=input_identity['sha256'],
            validation_report=str(proof.relative_to(job_dir)), validation_report_sha256=_digest(proof))
        # Old generated candidates are never an alternative to this input.
        state.pop('pending_reference', None)
        _save_manifest(checkpoint, state)

    delivered = state.get("delivered")
    if delivered:
        path = output_dir / delivered["filename"]
        proof = job_dir / (delivered.get('visual_report') or 'missing-proof')
        if (path.is_file() and _digest(path) == delivered.get("sha256")
                and delivered.get('visual_contract') == _visual_contract()
                and proof.resolve().is_relative_to(job_dir.resolve()) and proof.is_file()
                and _digest(proof) == delivered.get('visual_report_sha256')):
            gate = stage_validate_mesh(path, require_textures=texture)
            report(gate)
            if gate.status == "done":
                return result(True, path)

    if vision_error:
        report(PipelineStage('vlm_preparation', 'auto', 'failed', log=vision_error,
                             error_kind='preparation'))
        return result()

    if selection_error:
        report(PipelineStage('3d_preparation', 'auto', 'failed', log=selection_error,
                             error_kind='preparation'))
        return result()

    pending("3d_preparation", model_3d, "Vérification du moteur 3D avant la reconstruction."
            if request.input_image is not None else "Vérification du moteur 3D avant de générer la référence.")
    try:
        prepared = ensure_model(model_3d, "3d")
        if prepared is None:
            raise RuntimeError(f"Moteur 3D absent pour {model_3d}")
        report(PipelineStage("3d_preparation", model_3d, "done",
                             log="Moteur 3D préparé."))
    except Exception as exc:
        report(PipelineStage("3d_preparation", model_3d, "failed", log=str(exc),
                             error_kind="preparation"))
        return result()

    accepted = state.get("accepted_reference")
    image_path = job_dir / accepted["filename"] if accepted else None
    if image_path and (not image_path.resolve().is_relative_to(job_dir.resolve())
                       or not image_path.is_file() or _digest(image_path) != accepted.get("sha256")):
        image_path = None
        state.pop("accepted_reference", None)
    if image_path and request.input_image is None:
        proof = job_dir / (accepted.get('review_report') or 'missing-reference-proof')
        if (accepted.get('review_contract') != _reference_contract()
                or not proof.resolve().is_relative_to(job_dir.resolve()) or not proof.is_file()
                or _digest(proof) != accepted.get('review_report_sha256')):
            state['pending_reference'] = dict(filename=accepted['filename'], sha256=accepted['sha256'])
            state.pop('accepted_reference', None)
            image_path = None
    if request.input_image is not None and image_path is None:
        report(PipelineStage('reference_input', 'local-image-decoder', 'failed',
            error_kind='input_validation', log='Copie de référence absente ou modifiée. '
            'Aucune image de remplacement ne sera générée.'))
        return result()
    if image_path:
        if request.input_image is None:
            report(PipelineStage("reference_reuse", vlm_model, "done", output=image_path,
                                 log="Référence déjà acceptée réutilisée après vérification de son empreinte."))
        else:
            subject = ('Reconstruct the supplied reference image as a 3D asset. Preserve its visible '
                       'subject, silhouette, accessories, colours and material placement. '
                       'The reference image is authoritative; do not substitute an invented identity '
                       'or demand a canonical character or body parts outside the image. '
                       'Additional user instructions: ' + subject)
    else:
        pending("reference_brief", "local-model", "Analyse du sujet et des critères visuels.")
        try:
            brief = state.get("reference_brief")
            if brief is None:
                brief = build_reference_brief(subject)
                state["reference_brief"] = brief
                _save_manifest(checkpoint, state)
                if brief.get("model") and find_ollama_model(brief["model"]):
                    _release_ollama_model(brief["model"])
            selected_image_model = state.get("selected_image_model") or image_model or select_image_model()
            state["selected_image_model"] = selected_image_model
            report(PipelineStage("reference_brief", brief.get("model", "local-model"), "done",
                                 log=json.dumps(brief, ensure_ascii=False)))
        except Exception as exc:
            report(PipelineStage("reference_brief", "local-model", "failed", log=str(exc),
                                 error_kind="preparation"))
            return result()
        prompt = image_prompt or brief["prompt"]
        description = (subject + "\nIdentity: " + brief["identity"] + "\nVisual criteria:\n"
                       + '\nRequired: the user-specified features, recognisable base identity and requested variant. '
                         'Unspecified creative details may vary; do not demand guessed extra costume or props.'
                       + '\nOne isolated subject on a plain neutral backdrop. No environment, scenery, '
                         'decorations floating around the subject, or large ground plane. '
                         'A requested base may be attached to the subject, not an entire floor.')
        feedback = state.get("reference_feedback", "")
        used_image_models = set(state.get('used_image_models', [selected_image_model]))

        def verify_reference(candidate):
            pending("vlm_verification", vlm_model, "Contrôle de la référence contre la demande.")
            check = stage_verify_image(candidate, description, model_name=vlm_model)
            proof = report(check)
            if check.status == 'done':
                state['accepted_reference'] = dict(filename=str(candidate.relative_to(job_dir)),
                    sha256=_digest(candidate), review_contract=_reference_contract(),
                    review_report=str(proof.relative_to(job_dir)), review_report_sha256=_digest(proof))
                state.pop('pending_reference', None)
            elif check.error_kind == 'mismatch':
                state['reference_feedback'] = check.log
                state.pop('pending_reference', None)
            _save_manifest(checkpoint, state)
            return check

        # A broken reviewer is not evidence that the image is wrong. Keep and
        # recheck it after a repair instead of paying for another generation.
        pending_reference = state.get('pending_reference')
        if pending_reference:
            candidate = job_dir / pending_reference['filename']
            if (candidate.resolve().is_relative_to(job_dir.resolve()) and candidate.is_file()
                    and _digest(candidate) == pending_reference.get('sha256')):
                check = verify_reference(candidate)
                if check.status == 'done':
                    image_path = candidate
                elif check.error_kind != 'mismatch':
                    return result()
                feedback = state.get('reference_feedback', '')
            else:
                state.pop('pending_reference', None)
        for _ in range(max(1, max_retries)):
            if image_path is not None:
                break
            feedback_hash = hashlib.sha256(feedback.encode()).hexdigest() if feedback else ''
            if (feedback and image_prompt is None
                    and state.get('reference_repair_feedback_sha256') != feedback_hash):
                pending('reference_repair', 'local-model', 'Révision du brief d’après les défauts réellement observés.')
                try:
                    corrected = build_reference_brief(subject, previous=brief,
                                                      feedback=visual_corrections(feedback))
                    state.setdefault('reference_brief_history', []).append(brief)
                    brief = corrected
                    prompt = brief['prompt']
                    state.update(reference_brief=brief, reference_repair_feedback_sha256=feedback_hash)
                    if brief.get('model') and find_ollama_model(brief['model']):
                        _release_ollama_model(brief['model'])
                    report(PipelineStage('reference_repair', brief.get('model', 'local-model'), 'done',
                                         log=json.dumps(brief, ensure_ascii=False)))
                except Exception as exc:
                    report(PipelineStage('reference_repair', 'local-model', 'failed',
                                         error_kind='preparation', log=str(exc)))
                    return result()
            attempt = int(state["next_attempt"])
            state["next_attempt"] = attempt + 1
            attempt_dir = job_dir / f"attempt-{attempt:04d}"
            attempt_dir.mkdir()
            candidate = attempt_dir / "reference.png"
            # A revised brief already incorporates the critique. Re-appending
            # it can reintroduce rejected objects and exceed the image context.
            correction = ("\nCorrect these rejected visual details: " + visual_corrections(feedback)
                if feedback and (image_prompt is not None
                    or state.get('reference_repair_feedback_sha256') != feedback_hash) else '')
            pending("image_generation", selected_image_model, f"Référence visuelle, tentative {attempt}.")
            image_stage = stage_generate_image(prompt + correction, candidate,
                model_name=selected_image_model, seed=attempt,
                negative_prompt='scenery, environment, ground plane, floating decorations, patterned background')
            report(image_stage)
            if image_stage.status != "done":
                # Repeating the same installation or execution without a repair
                # is not progress. Runtime diagnostics and partial files remain.
                return result()
            state['pending_reference'] = dict(filename=str(candidate.relative_to(job_dir)),
                                             sha256=_digest(candidate))
            _save_manifest(checkpoint, state)
            check = verify_reference(candidate)
            if check.status == "done":
                image_path = candidate
                break
            if check.error_kind != "mismatch":
                return result()
            feedback = check.log
            state["reference_feedback"] = feedback
            if image_model is None:
                alternative = alternative_image_model(used_image_models)
                if alternative:
                    report(PipelineStage('image_recipe_change', alternative, 'done', log=(
                        f'Référence refusée avec {selected_image_model}. Essai de {alternative} : '
                        'recette compatible, installation si nécessaire, qualité encore à vérifier.')))
                    selected_image_model = alternative
                    used_image_models.add(alternative)
                    state.update(selected_image_model=alternative, used_image_models=sorted(used_image_models))
            _save_manifest(checkpoint, state)
        if image_path is None:
            return result()

    from .model_selection import remember_failure
    from aurora_cli.evolution import load_policy
    recipe_limit = max(1, int(load_policy().get('delivery_3d', {}).get('max_recipe_attempts', 2)))
    used_recipes = set()
    for recipe_index in range(recipe_limit if automatic_3d else 1):
        used_recipes.add(model_3d)
        # Each recipe keeps separate shape checkpoints and the SAME reference.
        attempt = int(state['next_attempt'])
        state['next_attempt'] = attempt + 1
        mesh_dir = job_dir / f'attempt-{attempt:04d}'
        mesh_dir.mkdir()
        mesh_path = mesh_dir / 'model.glb'
        previous_mesh = state.get('mesh_attempts', {}).get(model_3d)
        if previous_mesh:
            previous_dir = (job_dir / previous_mesh).resolve()
            if previous_dir.is_relative_to(job_dir.resolve()):
                for name in ('model.shape.glb', 'model.shape.state.json', 'model.shape.latents.pt',
                             'model.shape.latents.json', 'shape.glb', 'shape.state.json'):
                    source = previous_dir / name
                    if source.is_file():
                        shutil.copyfile(source, mesh_dir / name)
        state.setdefault('mesh_attempts', {})[model_3d] = mesh_dir.name
        state['last_mesh_attempt'] = mesh_dir.name
        pending('3d_generation', model_3d, 'Reconstruction et texturation depuis la référence acceptée.')
        mesh = stage_generate_3d(image_path, mesh_path, model_name=model_3d,
                                 texture=texture, prepared=prepared)
        report(mesh)
        failed = mesh if mesh.status != 'done' else None
        if failed is None:
            gate = stage_validate_mesh(mesh_path, require_textures=texture)
            report(gate)
            failed = gate if gate.status != 'done' else None
        if failed is None:
            pending('mesh_visual_gate', 'local-renderer', 'Contrôle des rendus et du placement des textures.')
            visual = stage_verify_mesh(image_path, mesh_path, prepared=prepared,
                                       require_textures=texture, model_name=model_3d)
            report(visual)
            # A visual failure still gets the semantic evidence when available.
            if visual.output is not None:
                pending('mesh_semantic_gate', vlm_model, 'Contrôle du sujet sur le rendu 3D réel.')
                semantic = stage_verify_mesh_subject(visual.output, subject, model_name=vlm_model, reference=image_path)
                report(semantic)
                failed = visual if visual.status != 'done' else semantic if semantic.status != 'done' else None
            else:
                failed = PipelineStage('mesh_semantic_gate', vlm_model, 'failed',
                    log='Rapport visuel absent', error_kind='visual_validation')
                report(failed)
        if failed is None:
            break
        remember_failure('3d', model_3d, failed.log, context=_digest(image_path),
            infrastructure=any(word in failed.log.lower() for word in ('out of memory', 'cuda unavailable', 'no module named')))
        if not automatic_3d or recipe_index + 1 == recipe_limit:
            return result()
        try:
            replacement = select_3d_model(texture=texture, excluded=used_recipes, context=_digest(image_path))
        except RuntimeError:
            return result()
        report(PipelineStage('3d_recipe_change', replacement, 'done', log=(
            f'Échec de {model_3d}. Essai de {replacement} avec la référence conservée ; '
            'la nouvelle recette doit passer les mêmes contrôles.')))
        model_3d = replacement
        pending('3d_preparation', model_3d, 'Préparation du moteur de remplacement.')
        try:
            prepared = ensure_model(model_3d, '3d')
            if prepared is None:
                raise RuntimeError('Moteur de remplacement absent.')
            report(PipelineStage('3d_preparation', model_3d, 'done', log='Moteur de remplacement préparé.'))
        except Exception as exc:
            report(PipelineStage('3d_preparation', model_3d, 'failed', error_kind='preparation', log=str(exc)))
            return result()
    target = output_dir / f"model-{key}.glb"
    # The public destination is updated only after validation. Attempt artefacts
    # remain beside their evidence, including earlier failed runs.
    if target.exists() and state.get("delivered", {}).get("sha256") != _digest(target):
        target = output_dir / f"model-{key}-{attempt}.glb"
    temporary = target.with_suffix(".glb.tmp")
    shutil.copyfile(mesh_path, temporary)
    temporary.replace(target)
    state["delivered"] = dict(filename=target.name, sha256=_digest(target),
                              visual_contract=_visual_contract(),
                              visual_report=str(visual.output.relative_to(job_dir)),
                              visual_report_sha256=_digest(visual.output))
    return result(True, target)
