"""Local image creation uses the same runner and verifier as 3D references."""
from pathlib import Path
import shutil
import uuid
from aurora_cli import display
from . import catalog, fetcher, pipeline
from .agents import get
from .machine import profile
from .request_spec import parse_creation_request


def generate(prompt, result):
    model_name = model_spec = pipeline.select_image_model()
    request = parse_creation_request(prompt, capability='image')
    output_dir = request.output_dir
    attempt = output_dir / '.jobia' / uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=True)
    manifest = {'request': prompt, 'subject': request.subject, 'model': model_spec, 'stages': []}
    from aurora_cli.evolution import load_policy
    retries = int(load_policy().get('delivery_image', {}).get('max_attempts', 2))
    feedback = ''
    used = set()
    discovered = {m.name: Path(m.path) for m in result.loose_models
                  if m.capability == 'image' and m.path and (Path(m.path) / 'model_index.json').is_file()}
    from .model_selection import remember_failure
    for index in range(max(1, retries)):
        used.add(model_spec)
        try:
            if model_spec in discovered:
                prepared = pipeline.ensure_model(model_spec, 'image', model_dir=discovered[model_spec])
            else:
                prepared = pipeline.ensure_model(model_name, 'image')
            if not prepared:
                raise RuntimeError('Aucun moteur image exécutable pour le modèle sélectionné.')
        except Exception as exc:
            manifest['stages'].append(dict(name='image_preparation', status='failed', provider=model_spec, log=str(exc)))
            pipeline._save_manifest(attempt / 'job.json', manifest)
            alternative = pipeline.alternative_image_model(used) if index + 1 < retries else None
            if not alternative:
                raise RuntimeError(f'Échec image, diagnostics conservés : {attempt}. {exc}') from exc
            model_name = model_spec = alternative
            continue
        candidate = attempt / f'candidate-{index + 1}.png'
        display.info(f'Génération image et contrôle visuel, tentative {index + 1}.')
        correction = '\nCorrect these visual inconsistencies: ' + pipeline.visual_corrections(feedback) if feedback else ''
        stage = pipeline.stage_generate_image(request.subject + correction, candidate,
                    model_name=model_name, model_spec=model_spec, seed=index + 1, prepared=prepared)
        manifest['stages'].append({'name': stage.name, 'status': stage.status, 'provider': model_spec, 'log': stage.log})
        if stage.status != 'done':
            pipeline._save_manifest(attempt / 'job.json', manifest)
            remember_failure('image', model_spec, stage.log, context=request.subject,
                             infrastructure='out of memory' in stage.log.lower())
            alternative = pipeline.alternative_image_model(used) if index + 1 < retries else None
            if not alternative:
                raise RuntimeError(f'Échec image, diagnostics conservés : {attempt}. {stage.log[-1000:]}')
            model_name = model_spec = alternative
            continue
        verification = pipeline.stage_verify_image(candidate, request.subject)
        manifest['stages'].append({'name': verification.name, 'status': verification.status,
                                   'log': verification.log})
        pipeline._save_manifest(attempt / 'job.json', manifest)
        if verification.status == 'done':
            target = output_dir / f'image-{attempt.name[:12]}.png'
            temporary = target.with_suffix('.tmp')
            shutil.copyfile(candidate, temporary)
            temporary.replace(target)
            manifest.update(status='delivered', output=str(target), sha256=pipeline._digest(target))
            pipeline._save_manifest(attempt / 'job.json', manifest)
            display.success(f'Image livrée après contrôle visuel : {target}')
            return target
        if verification.error_kind != 'mismatch':
            break
        feedback = verification.log
        remember_failure('image', model_spec, feedback, context=request.subject)
        alternative = pipeline.alternative_image_model(used) if index + 1 < retries else None
        if alternative:
            manifest['stages'].append(dict(name='image_recipe_change', status='done',
                provider=alternative, log='Référence refusée ; nouvelle recette, mêmes critères de validation.'))
            model_name = model_spec = alternative
    display.warning(f'Image non validée, aucune livraison finale. Diagnostics : {attempt / "job.json"}')
    return False
