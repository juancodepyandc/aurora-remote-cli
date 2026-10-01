"""Managed audio execution shares selection, provisioning and failure evidence."""
from pathlib import Path
import os
import subprocess
import uuid


def generate(*, text=None, source=None, output=None, model=None):
    from . import locations, pipeline
    from .model_selection import choose, remember_failure
    from aurora_cli.adapters import AdapterRegistry
    role, capability = ('speech', 'tts') if text is not None else ('audio', 'audio')
    if text is None and source is None:
        raise ValueError('Texte à prononcer ou audio à transcrire requis.')
    if text is not None and source is not None:
        raise ValueError('Choisis la synthèse OU la transcription.')
    if text is not None:
        from aurora_cli.evolution import load_policy
        if not text.strip() or len(text) > int(load_policy().get('delivery_audio', {}).get('max_text_characters', 20000)):
            raise ValueError('Texte vocal vide ou hors du budget configuré ; aucune troncature.')
    if source is not None and not Path(source).is_file():
        raise ValueError('Audio source introuvable.')
    model = model or choose(role).artifact.ref
    registry = AdapterRegistry()
    runner, reason = registry.resolve(capability, model)
    if runner is None:
        raise RuntimeError(reason)
    output = Path(output) if output else locations.data_dir() / 'outputs' / 'audio' / (uuid.uuid4().hex + ('.wav' if text is not None else '.json'))
    if output.exists():
        raise FileExistsError('La destination existe déjà et sera conservée.')
    prepared = pipeline.ensure_model(model, capability)
    if not prepared:
        raise RuntimeError('Runtime audio indisponible.')
    root, python = prepared
    output.parent.mkdir(parents=True, exist_ok=True)
    command = registry.build(runner, interpreter=python, worker=Path(__file__).with_name(runner.worker),
        model_dir=root, input=Path(source).resolve() if source else '', text=text or '', output=output.resolve())
    log = output.with_suffix(output.suffix + '.runtime.log')
    try:
        with log.open('w', encoding='utf-8') as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                text=True, timeout=1800, env=os.environ | runner.environment())
        if result.returncode or not output.is_file():
            raise RuntimeError(f'Échec audio ; diagnostic conservé : {log}')
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        remember_failure(role, model, str(exc), context=text or str(source))
        raise
    return output
