"""Local image generation with isolated dependencies and reusable weights."""
from pathlib import Path
import subprocess
import uuid
import click
from aurora_cli import display
from . import catalog, fetcher, locations
from .agents import get
from .bootstrap import ensure_hf, python_engine
from .machine import profile


def generate(prompt, result):
    machine = profile()
    if machine.free_ram_gb < 10:
        from .bootstrap import start_ollama
        try:
            start_ollama()
            subprocess.run(["ollama", "ps"], capture_output=True, text=True,
                           timeout=5, check=False)
            from .resource_governor import release_ollama_models
            released = release_ollama_models()
            if released:
                display.info(f"Mémoire libérée automatiquement : {len(released)} modèle(s) local(aux).")
                machine = profile()
        except Exception as exc:
            display.hint(f"Gestion automatique de la mémoire indisponible : {exc}")
    # Only complete diffusion pipelines are loadable by this adapter. A loose
    # checkpoint or a LoRA must never be passed off as an executable pipeline.
    roots = [fetcher.hf_target_dir(a) for a in catalog.for_agent(get('image'))]
    for model in result.loose_models:
        path = Path(model.path) if model.path else None
        if path and model.capability == 'image':
            roots.append(path if path.is_dir() else path.parent)
    model_dir = next((p for p in roots if (p / 'model_index.json').is_file()
                      and 'flux' not in str(p).lower()), None)
    if machine.free_ram_gb < 10:
        display.warning(f'{machine.free_ram_gb:.1f} Go libres : le moteur image local nécessite une marge de 10 Go.')
        display.hint('/apps pour identifier la mémoire occupée ; /close PID puis réessaie.')
        return False
    consent_for_engine = False
    if model_dir is None:
        artifact = catalog.resolve(get('image'), machine, 'light')
        if artifact is None:
            display.warning('Aucun modèle image adapté à cette machine.')
            return False
        consent_for_engine = True
        ensure_hf()
        plan, _ = fetcher.install(artifact, profile(), job=prompt, progress=display.hint)
        model_dir = plan.target
    else:
        display.info(f'Pipeline image local : {model_dir.name}')
    engine = locations.data_dir() / 'engines' / 'images'
    import os
    python = engine / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    marker = engine / 'ready'
    if not python.exists() or not marker.exists():
        python = python_engine('images', ['torch', 'diffusers', 'transformers', 'accelerate', 'safetensors', 'Pillow'])
        marker.touch()
    output = locations.data_dir() / 'outputs' / f'image-{uuid.uuid4().hex[:12]}.png'
    output.parent.mkdir(parents=True, exist_ok=True)
    display.info('Génération locale en cours ; Ctrl-C pour interrompre.')
    subprocess.run([str(python), str(Path(__file__).with_name('image_worker.py')),
                    str(model_dir), prompt, str(output)], check=True)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError('Le moteur a terminé sans produire une image.')
    display.success(f'Image créée : {output}')
    return True
